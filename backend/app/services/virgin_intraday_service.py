"""
Virgin CPR intraday backtest orchestration.

Fetches the real intraday history the engine needs and hands it to
ai-engine's virgin_intraday module. Dhan serves intraday candles back to
roughly early 2022 (verified empirically — 5-year requests come back
empty), and rejects windows much beyond ~90 days per request, so history is
pulled in paced 90-day chunks and cached: a multi-year pull is ~16 requests
and must not be repeated on every page poll.
"""

import threading
import time
from datetime import date, datetime, timedelta, timezone

from niftyedge_ai_engine.virgin_intraday import (
    Bar,
    DailyBar,
    run_virgin_intraday_backtest,
)

from app.services import market_data

IST = timezone(timedelta(hours=5, minutes=30))
_CHUNK_DAYS = 90          # Dhan returns empty beyond ~90-day intraday windows
_CHUNK_PAUSE = 0.35       # pacing between chunk requests
_CACHE_TTL = 3600.0

_CACHE_LOCK = threading.RLock()
_CACHE: dict[tuple, tuple[float, object]] = {}


def _cached(key: tuple, fetch, ttl: float = _CACHE_TTL):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < ttl:
            return hit[1]
        data = fetch()
        _CACHE[key] = (time.monotonic(), data)
        return data


def _fetch_intraday(underlying: str, interval: str, frm: date, to: date) -> dict:
    """{session_date: [Bar,...]} of real intraday candles, chunked and paced."""
    def fetch():
        out: dict[date, list] = {}
        cursor = frm
        while cursor < to:
            chunk_end = min(cursor + timedelta(days=_CHUNK_DAYS), to)
            a = datetime.combine(cursor, datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
            b = datetime.combine(chunk_end, datetime.max.time().replace(microsecond=0), tzinfo=IST).astimezone(timezone.utc)
            try:
                candles = market_data.get_candles(underlying, interval, a, b)
            except Exception:
                candles = []
            for c in candles:
                ist = c.ts.astimezone(IST)
                out.setdefault(ist.date(), []).append(
                    Bar(ts=ist, open=c.open, high=c.high, low=c.low, close=c.close)
                )
            cursor = chunk_end
            time.sleep(_CHUNK_PAUSE)
        for d in out:
            out[d].sort(key=lambda b: b.ts)
        return out

    return _cached(("virgin_intraday", underlying, interval, str(frm), str(to)), fetch)


def _fetch_daily(underlying: str, frm: date, to: date) -> list:
    def fetch():
        a = datetime.combine(frm - timedelta(days=10), datetime.min.time(), tzinfo=timezone.utc)
        b = datetime.combine(to, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        raw = market_data.get_candles(underlying, "1d", a, b)
        seen: dict[date, DailyBar] = {}
        for c in sorted(raw, key=lambda c: c.ts):
            d = c.ts.astimezone(IST).date()
            seen[d] = DailyBar(dt=d, open=c.open, high=c.high, low=c.low, close=c.close)
        return list(seen.values())

    return _cached(("virgin_daily", underlying, str(frm), str(to)), fetch)


def run_virgin_backtest(
    underlying: str = "NIFTY50",
    years: float = 2.0,
    interval: str = "15m",
    stop_buffer_pct: float = 0.25,
    reward_multiple: float = 2.0,
    morning_window_min: int = 60,
) -> dict:
    underlying = underlying.upper().replace(" ", "")
    if interval not in ("5m", "15m", "30m"):
        raise ValueError("interval must be 5m, 15m or 30m")
    if not 0.25 <= years <= 4.5:
        raise ValueError("years must be between 0.25 and 4.5 (Dhan intraday history limit)")

    to = date.today()
    frm = to - timedelta(days=int(365 * years))

    days = _fetch_daily(underlying, frm, to)
    bars_by_day = _fetch_intraday(underlying, interval, frm, to)
    if not days or not bars_by_day:
        raise ValueError("No intraday history available for this range")

    result = run_virgin_intraday_backtest(
        days=days, bars_by_day=bars_by_day,
        stop_buffer_pct=stop_buffer_pct, reward_multiple=reward_multiple,
        morning_window_min=morning_window_min,
    )

    return {
        "underlying": underlying,
        "interval": interval,
        "from_date": str(days[0].dt) if days else None,
        "to_date": str(days[-1].dt) if days else None,
        "sessions_with_intraday": len(bars_by_day),
        "params": {
            "stop_buffer_pct": stop_buffer_pct,
            "reward_multiple": reward_multiple,
            "morning_window_min": morning_window_min,
        },
        "zone_stats": {
            "zones_created": result.zones_created,
            "zones_filled": result.zones_filled,
            "fill_rate_pct": result.fill_rate_pct,
            "fill_within_5_sessions_pct": result.fill_within_5_sessions_pct,
            "median_fill_age_sessions": result.median_fill_age_days,
        },
        "performance": {
            "total_trades": len(result.trades),
            "total_points": result.total_points,
            "win_rate_pct": result.win_rate_pct,
            "avg_win_points": result.avg_win_points,
            "avg_loss_points": result.avg_loss_points,
            "profit_factor": result.profit_factor,
            "max_drawdown_points": result.max_drawdown_points,
        },
        "trades": [
            {
                "enteredAt": t.entered_at.isoformat(), "exitedAt": t.exited_at.isoformat(),
                "side": t.side, "entry": t.entry, "exit": t.exit,
                "points": t.points, "returnPct": t.return_pct,
                "reason": t.reason, "zoneCreatedFor": str(t.zone_created_for),
            }
            for t in result.trades
        ],
    }
