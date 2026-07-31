"""
Pivot/CPR service — computes the pre-market level set for an underlying via
ai-engine's pivots module (see docs/PivotBoss-Roadmap.md).

Mock mode: prior-session OHLC comes from fixed values consistent with the
rest of the mock stack (frontend ticker, mock adapter quotes) rather than a
real EOD store. When Postgres lands (Roadmap Phase 3), this reads the last
two rows of the daily-OHLC table instead — the ai-engine call is unchanged.
"""

import threading
import time
from datetime import date, datetime, timedelta, timezone

from niftyedge_ai_engine.pivots import classify_width, classify_width_percentile, cpr as calc_cpr, daily_levels

from app.config import settings

# Daily-candle history barely moves intraday (today's still-forming candle is
# always excluded below), but both get_cpr_levels() and get_cpr_analysis()
# call the broker's historical-candles endpoint directly and get polled every
# ~2s by cpr-dashboard.html. Without a cache this doubles/triples the
# uncached-Dhan-call rate that already caused 429s elsewhere in this app —
# see market_data.py's _CACHE for the same pattern.
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, object]] = {}
_HISTORY_CACHE_TTL = 300.0


def _cached(key: tuple, fetch, ttl: float = _HISTORY_CACHE_TTL):
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < ttl:
            return cached[1]
        data = fetch()
        _CACHE[key] = (time.monotonic(), data)
        return data

# Prior two sessions' OHLC per underlying (mock). Chosen so NIFTY50's numbers
# sit consistently around the mock spot of 23,532.45.
_MOCK_SESSIONS = {
    "NIFTY50": {
        # prior session (drives today's levels)
        "high": 23580.00, "low": 23400.00, "close": 23532.45,
        # session before that (drives the two-day CPR relationship) — sits
        # higher, so today's CPR classifies LOWER_VALUE (bearish), consistent
        # with the mock stack's overall "BEARISH BELOW VAH" story.
        "prev_high": 23700.00, "prev_low": 23520.00, "prev_close": 23600.00,
    },
    "SENSEX": {
        "high": 77450.00, "low": 76900.00, "close": 77259.01,
        "prev_high": 77100.00, "prev_low": 76500.00, "prev_close": 76980.00,
    },
    "NIFTYBANK": {
        "high": 50320.00, "low": 49890.00, "close": 50145.80,
        "prev_high": 50050.00, "prev_low": 49600.00, "prev_close": 49910.00,
    },
}


def _next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:  # Sat/Sun — doesn't account for exchange holidays
        nxt += timedelta(days=1)
    return nxt


def _sessions_from_broker(underlying: str) -> dict | None:
    """When a real adapter (e.g. dhan) is active, pull the last two completed
    daily candles and use them as the CPR inputs. Returns None on any failure
    so the caller can fall back to mock sessions."""
    try:
        from app.services.broker import get_broker

        to = datetime.now(timezone.utc)
        candles = _cached(
            ("cpr_sessions", underlying),
            lambda: get_broker().get_historical_candles(underlying, "1d", to - timedelta(days=14), to),
        )
        # drop today's still-forming candle if the market is open
        completed = [c for c in candles if c.ts.date() < date.today()]
        if len(completed) < 2:
            return None
        prior, before = completed[-1], completed[-2]
        return {
            "high": prior.high, "low": prior.low, "close": prior.close,
            "prev_high": before.high, "prev_low": before.low, "prev_close": before.close,
            "source": "broker",
        }
    except Exception:
        return None


def get_cpr_levels(underlying: str, session_date: str | None = None) -> dict | None:
    data = None
    source = "mock"
    if settings.broker_adapter != "mock":
        data = _sessions_from_broker(underlying)
        if data:
            source = "broker"
    if data is None:
        data = _MOCK_SESSIONS.get(underlying.upper().replace(" ", ""))
    if not data:
        return None
    levels = daily_levels(
        data["high"], data["low"], data["close"],
        data["prev_high"], data["prev_low"], data["prev_close"],
    )
    return {
        "underlying": underlying,
        "session_date": session_date or str(_next_trading_day(date.today())),
        "levels": levels,
        "source": source,
    }


# -- CPR analysis: percentile regime, breakout flag, clusters, trade plan ----
#
# Everything below builds on get_cpr_levels() with real trailing daily-candle
# history (~25 real sessions), so it only produces meaningful output when a
# real broker (Dhan) is active — under the mock adapter it degrades to the
# same fixed-threshold regime with no percentile/flag data, which callers
# should treat as "not available" rather than fabricate.

_TRAILING_SESSIONS = 20
_TRAILING_LOOKBACK_DAYS = 45  # calendar days fetched to reliably get 20+1 trading sessions


def _trailing_daily_days(underlying: str) -> list[dict] | None:
    """Real daily O/H/L/C per real IST trading day, oldest first. Returns
    None if no real broker/history is available (mock mode, or a fetch
    failure) so callers can degrade gracefully rather than fabricate."""
    if settings.broker_adapter == "mock":
        return None
    try:
        from app.services.broker import get_broker

        to = datetime.now(timezone.utc)
        frm = to - timedelta(days=_TRAILING_LOOKBACK_DAYS)
        candles = _cached(
            ("cpr_trailing", underlying),
            lambda: get_broker().get_historical_candles(underlying, "1d", frm, to),
        )
    except Exception:
        return None
    if not candles:
        return None
    IST = timezone(timedelta(hours=5, minutes=30))
    days = []
    for c in sorted(candles, key=lambda c: c.ts):
        ist_date = (c.ts.astimezone(IST)).date()
        days.append({"date": ist_date, "h": c.high, "l": c.low, "c": c.close})
    return days


def _width_history(days: list[dict]) -> list[dict]:
    """Each day i>=1's CPR width_pct, computed from day i-1's real OHLC —
    the same methodology get_cpr_levels() uses for "today's" levels, just
    walked across real history."""
    out = []
    for i in range(1, len(days)):
        prior = days[i - 1]
        c = calc_cpr(prior["h"], prior["l"], prior["c"])
        out.append({"date": days[i]["date"], "width_pct": c.width_pct, "tc": c.tc, "bc": c.bc})
    return out


def _consecutive_narrow_flag(days: list[dict]) -> bool:
    """True if the most recently COMPLETED session's own CPR (computed from
    the session before that) was narrow, AND that session's real H/L stayed
    inside it (no breakout) — the setup this app's spec calls an elevated-
    breakout-probability day for the upcoming session."""
    if len(days) < 3:
        return False
    last, prior_to_last = days[-1], days[-2]
    last_cpr = calc_cpr(prior_to_last["h"], prior_to_last["l"], prior_to_last["c"])
    history = _width_history(days[:-1])
    widths = [d["width_pct"] for d in history[-_TRAILING_SESSIONS:]]
    regime = classify_width_percentile(last_cpr.width_pct, widths)
    if not regime or regime.regime != "NARROW":
        return False
    stayed_inside = last["h"] <= last_cpr.tc and last["l"] >= last_cpr.bc
    return stayed_inside


def _build_trade_plan(levels, current_ltp: float | None, percentile_regime: str | None) -> dict:
    """Composite Long/Short/Range-Bound/No-Trade plan per the CPR execution
    rules: Ascending+Narrow(-ish) breaking the R1/PDH cluster -> long;
    Descending+Narrow(-ish) breaking the S1/PDL cluster -> short; price
    locked inside a Wide CPR -> absolute no-trade lock. "Narrow(-ish)" uses
    the percentile regime when real history is available (not WIDE), since
    that's the more reliable read — see classify_width_percentile."""
    floor, cpr_today, two_day = levels.floor, levels.cpr_today, levels.two_day
    upper, lower = cpr_today.tc, cpr_today.bc
    resistance_cluster = {"lower": min(floor.r1, levels.pdh), "upper": max(floor.r1, levels.pdh)}
    support_cluster = {"lower": min(floor.s1, levels.pdl), "upper": max(floor.s1, levels.pdl)}

    is_ascending = two_day is not None and two_day.relationship in ("HIGHER_VALUE", "OVERLAPPING_HIGHER_VALUE")
    is_descending = two_day is not None and two_day.relationship in ("LOWER_VALUE", "OVERLAPPING_LOWER_VALUE")
    is_wide = percentile_regime == "WIDE"

    no_trade_zone = is_wide and current_ltp is not None and lower <= current_ltp <= upper

    if no_trade_zone:
        return {
            "bias": "No-Trade", "side": None,
            "entry_trigger": f"Locked — price is inside a Wide CPR ({lower:.2f}-{upper:.2f}). No trade until it breaks the range.",
            "stop_loss": None, "target1": None, "target2": None,
        }

    broke_above_resistance = current_ltp is not None and current_ltp > resistance_cluster["upper"]
    broke_below_support = current_ltp is not None and current_ltp < support_cluster["lower"]

    if is_ascending and not is_wide and (broke_above_resistance or (current_ltp is not None and current_ltp > upper)):
        t1, t2 = (floor.r2, floor.r3) if current_ltp and current_ltp >= floor.r1 else (floor.r1, floor.r2)
        return {
            "bias": "Bullish", "side": "LONG",
            "entry_trigger": f"Confirm next session opens/holds above {resistance_cluster['upper']:.2f} (R1/PDH cluster).",
            "stop_loss": round(lower, 2), "target1": round(t1, 2), "target2": round(t2, 2),
        }

    if is_descending and not is_wide and (broke_below_support or (current_ltp is not None and current_ltp < lower)):
        t1, t2 = (floor.s2, floor.s3) if current_ltp and current_ltp <= floor.s1 else (floor.s1, floor.s2)
        return {
            "bias": "Bearish", "side": "SHORT",
            "entry_trigger": f"Confirm next session opens/holds below {support_cluster['lower']:.2f} (S1/PDL cluster).",
            "stop_loss": round(upper, 2), "target1": round(t1, 2), "target2": round(t2, 2),
        }

    return {
        "bias": "Range-Bound", "side": None,
        "entry_trigger": f"No edge yet — wait for a decisive break of {upper:.2f} (long) or {lower:.2f} (short).",
        "stop_loss": None, "target1": None, "target2": None,
    }


def get_cpr_analysis(underlying: str) -> dict | None:
    base = get_cpr_levels(underlying)
    if not base:
        return None
    levels = base["levels"]

    days = _trailing_daily_days(underlying)
    percentile = None
    consecutive_narrow_flag = False
    if days and len(days) >= 3:
        history = _width_history(days)
        trailing = [d["width_pct"] for d in history[-_TRAILING_SESSIONS:]]
        percentile = classify_width_percentile(levels.cpr_today.width_pct, trailing)
        consecutive_narrow_flag = _consecutive_narrow_flag(days)

    current_ltp = None
    try:
        from app.services import market_data

        quotes = market_data.get_quotes([underlying])
        if quotes:
            current_ltp = quotes[0].ltp
    except Exception:
        pass

    floor = levels.floor
    resistance_cluster = {"lower": round(min(floor.r1, levels.pdh), 2), "upper": round(max(floor.r1, levels.pdh), 2)}
    support_cluster = {"lower": round(min(floor.s1, levels.pdl), 2), "upper": round(max(floor.s1, levels.pdl), 2)}

    trade_plan = _build_trade_plan(levels, current_ltp, percentile.regime if percentile else None)

    return {
        **base,
        "current_ltp": current_ltp,
        "percentile_regime": percentile.regime if percentile else None,
        "percentile_rank": percentile.percentile_rank if percentile else None,
        "p20_threshold": percentile.p20_threshold if percentile else None,
        "p70_threshold": percentile.p70_threshold if percentile else None,
        "consecutive_narrow_flag": consecutive_narrow_flag,
        "resistance_cluster": resistance_cluster,
        "support_cluster": support_cluster,
        "trade_plan": trade_plan,
    }


# -- Top Narrow CPR Stocks ----------------------------------------------------
#
# Ranks the broker's stock universe (NIFTY 50 constituents for Dhan) by
# today's CPR width, narrowest first. This needs one historical-candles call
# per stock (no batch endpoint exists), so results are cached for a long time
# — prior-day OHLC is fixed until the next session closes, there's no reason
# to recompute this every 2s poll — and the per-stock calls are paced rather
# than fired back-to-back, to stay well clear of Dhan's undocumented
# marketfeed/historical rate limits (see market_data.py's _CACHE comment for
# the 429 this app already hit once from unpaced polling).
#
# Deliberately does NOT fetch a live quote for the winners: testing showed
# that a *third* concurrent /marketfeed/quote call (on top of the NIFTY50 +
# SENSEX calls this page already makes every 2s) reliably tripped Dhan's 429,
# even staggered by several seconds. The prior-session close from the same
# historical candle used for the width calc is a real, already-fetched price
# — reusing it avoids the extra call entirely rather than racing the limit.

_TOP_NARROW_LIMIT = 3
_TOP_NARROW_CACHE_TTL = 1800.0
_UNIVERSE_CALL_DELAY = 0.15


def _stock_width(get_broker, symbol: str) -> dict | None:
    try:
        to = datetime.now(timezone.utc)
        candles = get_broker().get_historical_candles(symbol, "1d", to - timedelta(days=14), to)
        completed = [c for c in candles if c.ts.date() < date.today()]
        if len(completed) < 2:
            return None
        prior = completed[-1]
        c = calc_cpr(prior.high, prior.low, prior.close)
        w = classify_width(c)
        return {"symbol": symbol, "width_pct": c.width_pct, "regime": w.regime, "close": prior.close}
    except Exception:
        return None


def _compute_top_narrow_stocks() -> list[dict]:
    from app.services.broker import get_broker

    symbols = get_broker().get_universe_symbols()
    rows = []
    for i, sym in enumerate(symbols):
        if i:
            time.sleep(_UNIVERSE_CALL_DELAY)
        row = _stock_width(get_broker, sym)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: r["width_pct"])
    return rows[:_TOP_NARROW_LIMIT]


def get_top_narrow_stocks() -> dict:
    rows = _cached(("top_narrow_stocks",), _compute_top_narrow_stocks, ttl=_TOP_NARROW_CACHE_TTL)
    return {
        "source": "mock" if settings.broker_adapter == "mock" else "broker",
        "stocks": [
            {"symbol": r["symbol"], "width_pct": r["width_pct"], "regime": r["regime"], "close": r["close"]}
            for r in rows
        ],
    }
