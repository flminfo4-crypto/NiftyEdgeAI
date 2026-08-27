"""
Backtest job orchestration. docs/API/API.md models POST /backtests as an
async job (jobId -> poll GET /backtests/{jobId}) because a real historical
replay can take a while; fetching + simulating over a multi-year daily
candle series is fast enough in practice to just run synchronously and store
the finished result under a job id, so the API contract (and any frontend
polling code written against it) is honored without actually needing a task
queue yet.
"""

import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

from niftyedge_ai_engine import (
    STRATEGY_TEMPLATES,
    Candle,
    register_custom_strategy,
    run_backtest,
    unregister_custom_strategy,
)
from niftyedge_ai_engine.options_pricing import realized_volatility

from app.config import settings
from app.services import market_data

_jobs: dict[str, dict] = {}
_seed_seq = itertools.count(19)

# frontend <select> values -> (underlying symbol, is_futures)
_INSTRUMENT_MAP = {
    "NIFTY50_OPTIONS": ("NIFTY50", False),
    "BANKNIFTY_OPTIONS": ("NIFTYBANK", False),
    "NIFTY_FUTURES": ("NIFTY50", True),
}

# Extra calendar days fetched before `from` so the first in-range day still
# has real prior-day OHLC for CPR and a real trailing window for realized
# volatility (see options_pricing.realized_volatility's 20-trading-day window).
_LOOKBACK_DAYS = 45


def _parse_date(s: str) -> date:
    return datetime.fromisoformat(s).date()


def _fetch_candles(underlying: str, frm: date, to: date) -> list[Candle]:
    fetch_from = datetime.combine(frm - timedelta(days=_LOOKBACK_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    fetch_to = datetime.combine(to, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
    raw = market_data.get_candles(underlying, "1d", fetch_from, fetch_to)
    seen: dict[date, Candle] = {}
    for c in sorted(raw, key=lambda c: c.ts):
        d = c.ts.date()
        seen[d] = Candle(dt=d, open=c.open, high=c.high, low=c.low, close=c.close)
    return list(seen.values())


def _iv_reality_check(underlying: str, candles: list[Candle]) -> dict:
    """Dhan retains zero expired option contracts (verified directly against
    the live API), so there is no real historical option-price series to
    backtest against — only a live snapshot. Rather than silently keep using
    the model's realized-vol proxy unchecked, this compares it to today's
    real market-implied ATM IV from the live option chain, so a user can see
    how well-calibrated the model's volatility assumption is right now. Not
    a substitute for real historical option data — a disclosed sanity check
    against the one piece of real option data Dhan does expose."""
    closes = [c.close for c in candles]
    model_sigma_pct = round(realized_volatility(closes) * 100, 2) if closes else None
    try:
        expiries = market_data.get_expiries(underlying)
        if not expiries:
            raise ValueError("no expiries")
        chain = market_data.get_option_chain(underlying, expiries[0])
        if not chain.rows:
            raise ValueError("empty chain")
        atm = min(chain.rows, key=lambda r: abs(r.strike - chain.spot_price))
        real_market_iv_pct = round((atm.ce_iv + atm.pe_iv) / 2, 2)
    except Exception:
        return {"model_sigma_pct": model_sigma_pct, "real_market_iv_pct": None, "delta_pct": None, "available": False}
    delta_pct = round(real_market_iv_pct - model_sigma_pct, 2) if model_sigma_pct is not None else None
    return {
        "model_sigma_pct": model_sigma_pct, "real_market_iv_pct": real_market_iv_pct,
        "delta_pct": delta_pct, "available": True,
    }


def submit_backtest(request: dict) -> dict:
    job_id = f"BT-{uuid.uuid4().hex[:10]}"
    underlying, is_futures = _INSTRUMENT_MAP.get(request.get("instrument", ""), ("NIFTY50", False))
    frm = _parse_date(request.get("from_") or request.get("from") or "2026-01-01")
    to = _parse_date(request.get("to") or str(date.today()))

    candles = _fetch_candles(underlying, frm, to)
    result = run_backtest(
        candles=candles,
        strategy=request.get("strategy", "ai-bias-ce-writing-below-vah"),
        starting_capital=request.get("initial_capital", 100_000.0),
        position_size_lots=request.get("position_size_lots", 3),
        stop_loss_pct=request.get("stop_loss_pct", 1.5),
        target_pct=request.get("target_pct", 3.0),
        include_slippage_and_costs=request.get("include_slippage_and_costs", True),
        is_futures=is_futures,
        hold_mode=request.get("hold", "strategy"),
        custom_hold_days=request.get("hold_days", 5),
        symbol=underlying,
    )
    record = {
        "job_id": job_id,
        "status": "COMPLETE",
        "request": request,
        "submitted_at": datetime.now(timezone.utc),
        "result": result,
        "iv_reality_check": _iv_reality_check(underlying, candles),
    }
    _jobs[job_id] = record
    return record


def run_template_backtest(request: dict) -> dict:
    """Backtest a TEMPLATE directly from its parameters, without first saving
    it as a named strategy.

    The Strategies page flow (create a strategy, then backtest it by key) is
    right for something you want to keep. It is wrong for exploring — trying
    six moving-average pairs would leave six entries in the user's saved
    strategy list and in the Strategy Lab sweep. So this registers the
    instance under a throwaway key, runs it, and unregisters it in a finally
    block, leaving STRATEGY_REGISTRY exactly as it was found.

    Returns the full trade list inline rather than a job id: the caller is a
    grid that wants every row, and paginating a few hundred rows it has
    already computed would only add round-trips.
    """
    template = request.get("template")
    if template not in STRATEGY_TEMPLATES:
        raise ValueError(
            f"Unknown template '{template}'. Valid: {', '.join(sorted(STRATEGY_TEMPLATES))}"
        )

    underlying, is_futures = _INSTRUMENT_MAP.get(request.get("instrument", ""), ("NIFTY50", False))
    frm = _parse_date(request.get("from_") or request.get("from") or "2022-01-01")
    to = _parse_date(request.get("to") or str(date.today()))
    if frm > to:
        raise ValueError("'from' must be on or before 'to'")

    candles = _fetch_candles(underlying, frm, to)
    if not candles:
        raise ValueError(f"No historical candles for {underlying} in {frm}..{to}")

    # `params` is a free-form dict, so it bypasses CamelModel's alias generator
    # and arrives camelCase from the browser. Reuse the Strategies page's own
    # converter rather than growing a second one that can drift from it.
    from app.services.strategy_config_service import _params_to_snake

    params = _params_to_snake(request.get("params") or {})

    key = f"__adhoc__{uuid.uuid4().hex[:12]}"
    try:
        register_custom_strategy(key, f"{template} (ad hoc)", template, params)
        result = run_backtest(
            candles=candles,
            strategy=key,
            starting_capital=request.get("initial_capital", 100_000.0),
            position_size_lots=request.get("position_size_lots", 1),
            stop_loss_pct=request.get("stop_loss_pct", 1.5),
            target_pct=request.get("target_pct", 3.0),
            include_slippage_and_costs=request.get("include_slippage_and_costs", True),
            is_futures=is_futures,
            hold_mode=request.get("hold", "strategy"),
            custom_hold_days=request.get("hold_days", 5),
            symbol=underlying,
        )
    finally:
        unregister_custom_strategy(key)

    # Only trades that closed inside the requested window belong to it; the
    # extra leading candles exist purely to warm up CPR/volatility.
    trades = [t for t in result.trades if t.closed_at.date() >= frm]

    equity = result.starting_capital
    rows = []
    for i, t in enumerate(trades, start=1):
        equity += t.pnl
        rows.append({
            "n": i,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
            "days_held": (t.closed_at.date() - t.opened_at.date()).days,
            "label": t.label,
            "side": t.side,
            "strike": t.strike,
            "result": t.result,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "equity_after": round(equity, 2),
        })

    wins = sum(1 for t in trades if t.result == "WIN")
    gross_win = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = sum(-t.pnl for t in trades if t.pnl < 0)
    return {
        "template": template,
        "params": params,
        "instrument": request.get("instrument", "NIFTY50_OPTIONS"),
        "underlying": underlying,
        "from_date": str(frm),
        "to_date": str(to),
        "sessions": len(candles),
        "starting_capital": result.starting_capital,
        "net_profit": result.net_profit,
        "net_profit_pct": result.net_profit_pct,
        "total_trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "avg_win": round(gross_win / wins, 2) if wins else 0.0,
        "avg_loss": round(gross_loss / (len(trades) - wins), 2) if len(trades) - wins else 0.0,
        "payoff_ratio": round((gross_win / wins) / (gross_loss / (len(trades) - wins)), 2)
        if wins and (len(trades) - wins) and gross_loss else None,
        "profit_factor": result.profit_factor if result.profit_factor != float("inf") else None,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "source": "mock" if settings.broker_adapter == "mock" else "broker",
        "trades": rows,
    }


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: j["submitted_at"], reverse=True)
