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

from niftyedge_ai_engine import Candle, run_backtest

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
    )
    record = {
        "job_id": job_id,
        "status": "COMPLETE",
        "request": request,
        "submitted_at": datetime.now(timezone.utc),
        "result": result,
    }
    _jobs[job_id] = record
    return record


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: j["submitted_at"], reverse=True)
