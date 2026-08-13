"""
Strategy Lab — evaluates every registered strategy against the app's stated
goal (1-2% profit per week) over ~5-6 years of real Dhan daily data.

Method, and why it is built this way:

- Every strategy runs on the SAME candles and the SAME parameters, so the
  ranking reflects the strategies themselves rather than tuning effort.
- The window is split into IN-SAMPLE and OUT-OF-SAMPLE periods. Strategies
  are ranked on in-sample results, then re-scored on the untouched
  out-of-sample period. A strategy that only works in-sample is overfit,
  and this surfaces that instead of hiding it — the single most common way
  backtests mislead.
- Scoring targets weekly consistency (share of weeks at/above 1%, share of
  weeks profitable, median week), NOT total return. A strategy that makes
  its money in two explosive weeks does not meet a "1-2% per week" goal
  however good its headline number looks.

Caveats that belong with any result out of this module: option premiums are
modeled via Black-Scholes on realized vol (Dhan retains no expired-contract
prices — see backtest.py), fills are assumed at daily open/close with no
slippage beyond the flat cost model, and past performance does not
establish future returns.
"""

import threading
import time
from datetime import date, datetime, timedelta, timezone

from niftyedge_ai_engine import STRATEGY_REGISTRY, run_backtest

from app.services.backtest_service import _fetch_candles

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, object]] = {}
_CACHE_TTL = 1800.0

# Split point: everything before is used for ranking, everything after is
# held back to test whether the ranking generalizes.
DEFAULT_SPLIT = date(2025, 1, 1)


def _cached(key: tuple, fetch):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        data = fetch()
        _CACHE[key] = (time.monotonic(), data)
        return data


def _slice(candles, frm: date, to: date):
    return [c for c in candles if frm <= c.dt <= to]


def _summarize(result) -> dict:
    w = result.weekly
    return {
        "net_profit_pct": result.net_profit_pct,
        "total_trades": result.total_trades,
        "win_rate_pct": result.win_rate_pct,
        "profit_factor": result.profit_factor if result.profit_factor != float("inf") else None,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "avg_weekly_pct": w.avg_return_pct if w else None,
        "median_weekly_pct": w.median_return_pct if w else None,
        "pct_weeks_profitable": w.pct_weeks_profitable if w else None,
        "pct_weeks_hit_1pct": w.pct_weeks_hit_1pct if w else None,
        "pct_weeks_hit_2pct": w.pct_weeks_hit_2pct if w else None,
        "worst_week_pct": w.worst_week_pct if w else None,
        "best_week_pct": w.best_week_pct if w else None,
        "max_consecutive_losing_weeks": w.max_consecutive_losing_weeks if w else None,
        "total_weeks": w.total_weeks if w else 0,
    }


def _goal_score(summary: dict) -> float:
    """Fitness for a '1-2% per week' objective.

    Deliberately dominated by consistency rather than total return, and
    penalised for deep drawdowns — a strategy that must survive to keep
    compounding weekly cannot afford a -80% equity hole regardless of what
    it earns afterwards. Returns 0 for strategies with too few trades to
    judge (a 3-trade sample is noise, not evidence)."""
    if not summary.get("total_weeks") or summary.get("total_trades", 0) < 20:
        return 0.0
    hit1 = summary.get("pct_weeks_hit_1pct") or 0.0
    profitable = summary.get("pct_weeks_profitable") or 0.0
    median = summary.get("median_weekly_pct") or 0.0
    dd = abs(summary.get("max_drawdown_pct") or 0.0)
    score = hit1 * 1.0 + profitable * 0.5 + max(median, 0) * 10
    if dd > 50:
        score *= 0.5
    if dd > 80:
        score *= 0.4
    return round(score, 1)


def run_sweep(
    instrument: str = "NIFTY50_OPTIONS",
    years: int = 6,
    split: date | None = None,
    starting_capital: float = 100_000.0,
    position_size_lots: int = 3,
    stop_loss_pct: float = 1.5,
    target_pct: float = 3.0,
) -> dict:
    split = split or DEFAULT_SPLIT
    underlying, is_futures = {
        "NIFTY50_OPTIONS": ("NIFTY50", False),
        "BANKNIFTY_OPTIONS": ("NIFTYBANK", False),
        "NIFTY_FUTURES": ("NIFTY50", True),
    }.get(instrument, ("NIFTY50", False))

    to = date.today()
    frm = to - timedelta(days=365 * years)
    candles = _cached(("lab_candles", underlying, years), lambda: _fetch_candles(underlying, frm, to))
    if not candles:
        raise ValueError(f"No historical candles for {underlying}")

    full_start, full_end = candles[0].dt, candles[-1].dt
    in_sample = _slice(candles, full_start, split - timedelta(days=1))
    out_sample = _slice(candles, split, full_end)

    def _run(series, strategy):
        if len(series) < 60:
            return None
        return run_backtest(
            candles=series, strategy=strategy, starting_capital=starting_capital,
            position_size_lots=position_size_lots, stop_loss_pct=stop_loss_pct,
            target_pct=target_pct, include_slippage_and_costs=True, is_futures=is_futures,
            symbol=underlying,
        )

    from app.services import strategy_config_service

    rows = []
    for key, sdef in STRATEGY_REGISTRY.items():
        if not strategy_config_service.is_active(key):
            continue
        try:
            r_in = _run(in_sample, key)
            r_out = _run(out_sample, key)
        except Exception:
            continue
        if not r_in or not r_out:
            continue
        s_in, s_out = _summarize(r_in), _summarize(r_out)
        rows.append({
            "key": key, "label": sdef.label,
            "in_sample": s_in, "out_sample": s_out,
            "in_score": _goal_score(s_in), "out_score": _goal_score(s_out),
            # Positive = held up (or improved) on unseen data; strongly
            # negative = the in-sample result did not generalize.
            "generalization": round((_goal_score(s_out) - _goal_score(s_in)), 1),
        })

    rows.sort(key=lambda r: r["out_score"], reverse=True)
    return {
        "instrument": instrument, "underlying": underlying,
        "data_from": str(full_start), "data_to": str(full_end),
        "split_date": str(split),
        "in_sample_days": len(in_sample), "out_sample_days": len(out_sample),
        "starting_capital": starting_capital,
        "position_size_lots": position_size_lots,
        "stop_loss_pct": stop_loss_pct, "target_pct": target_pct,
        "strategies": rows,
    }
