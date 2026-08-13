"""
Market data gateway (Business Service Layer). Normalizes calls to the
active broker adapter into the shapes the API layer returns. This is
also the natural place to add Redis caching later (see backend/README.md) —
callers don't need to change when that lands.
"""

import threading
import time
from datetime import date, datetime, timedelta, timezone

from app.services import analytics
from app.services.broker import get_broker

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION_START = (9, 15)
_SESSION_END = (15, 30)

# Dhan rate-limits both /optionchain (documented: 1 request / 3s per
# underlying+expiry) and /marketfeed/quote (undocumented, but hit in practice
# once several pages started auto-refreshing every 2s from multiple tabs —
# Dhan returned "429 Too many requests... may result in the user being
# blocked"). Every broker call the frontend can poll frequently goes through
# a short shared TTL cache below, keyed and locked so N concurrent
# tabs/widgets polling the same thing collapse into one real Dhan call.
# RLock, not Lock: some cached builders (e.g. _session_pocs) themselves call
# other cached helpers like get_candles, so the lock is re-entered on the same
# thread. A plain Lock deadlocks there — silently, with the request hanging
# forever rather than erroring.
_CACHE_LOCK = threading.RLock()
_CACHE: dict[tuple, tuple[float, object]] = {}


# When the broker says "429 too many requests", the worst possible response is
# to try again immediately — Dhan's own message warns that continuing "may
# result in the user being blocked". So a rate-limit response opens a circuit:
# for the cooldown window every cached endpoint serves its last known value and
# NO broker call is made at all. Stale data beats a blocked account.
_RATE_LIMIT_COOLDOWN = 30.0
_rate_limited_until = 0.0


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "Too many requests" in text


def rate_limit_state() -> dict:
    """Whether the broker circuit is currently open, for /system/status and
    for surfacing 'showing cached data' in the UI instead of pretending the
    numbers are live."""
    remaining = max(0.0, _rate_limited_until - time.monotonic())
    return {"rate_limited": remaining > 0, "cooldown_seconds_remaining": round(remaining, 1)}


def _cached(key: tuple, ttl: float, fetch):
    global _rate_limited_until
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < ttl:
            return cached[1]
        # Circuit open: serve whatever we last had rather than adding to the
        # pile of requests that got us rate-limited.
        if now < _rate_limited_until and cached:
            return cached[1]
        try:
            data = fetch()
        except Exception as exc:
            if _is_rate_limited(exc):
                _rate_limited_until = time.monotonic() + _RATE_LIMIT_COOLDOWN
            # A stale quote is far more useful than a 500, and refusing to
            # retry is the whole point of the cooldown.
            if cached:
                return cached[1]
            raise
        _CACHE[key] = (time.monotonic(), data)
        return data


# MUST exceed the frontend's ticker poll interval (2000ms in every *-page.js),
# or the cache does nothing: at TTL 2.0 each poll arrived exactly as the entry
# expired, so almost every one missed and went to the broker — which is how a
# "cached" ticker still produced a sustained ~1 request/second and a 429. The
# margin here is deliberate; do not lower it to match the poll interval again.
_QUOTE_CACHE_TTL = 6.0
_OPTION_CHAIN_CACHE_TTL = 3.5
_CANDLE_CACHE_TTL = 2.0
_CANDLE_BUCKET_SECONDS = 5  # groups frm/to falling in the same few-second window

# Previous polled OI/LTP snapshot per (underlying, expiry), used only as a
# fallback when the broker doesn't supply its own previous-session baseline
# (see get_oi_buildup).
_prev_oi_snapshot: dict[tuple[str, str], dict[float, dict]] = {}


# The index tickers any page can ask for. Requests that fall inside this set
# are widened to the whole set and served from ONE cache entry, then sliced
# back down to what the caller asked for.
#
# Without this, the header ticker on most pages (NIFTY50,INDIAVIX) and the
# dashboard's own ticker (NIFTY50,NIFTYBANK,FINNIFTY,SENSEX,INDIAVIX) hash to
# different cache keys and become two independent streams of broker calls, so
# two open tabs doubled the request rate against a limit that was already
# being hit. Dhan's quote endpoint is batched — asking for five symbols costs
# the same one call as asking for two — so widening is free and de-duplicating
# is pure win.
_QUOTE_UNIVERSE = ("NIFTY50", "NIFTYBANK", "FINNIFTY", "SENSEX", "INDIAVIX")


def get_quotes(symbols: list[str]):
    wanted = [s.upper() for s in symbols]
    if set(wanted) <= set(_QUOTE_UNIVERSE):
        fetch_symbols = list(_QUOTE_UNIVERSE)
    else:
        # Something outside the known universe — fetch exactly what was asked
        # for rather than silently dropping it.
        fetch_symbols = wanted
    key = ("quotes", tuple(sorted(fetch_symbols)))
    quotes = _cached(key, _QUOTE_CACHE_TTL, lambda: get_broker().get_quote(fetch_symbols))
    if fetch_symbols == wanted:
        return quotes
    by_symbol = {q.symbol: q for q in quotes}
    return [by_symbol[s] for s in wanted if s in by_symbol]


def get_option_chain(underlying: str, expiry: str):
    key = ("chain", underlying, expiry)
    return _cached(key, _OPTION_CHAIN_CACHE_TTL, lambda: get_broker().get_option_chain(underlying, expiry))


def get_candles(symbol: str, interval: str, frm: datetime, to: datetime):
    frm_bucket = int(frm.timestamp() // _CANDLE_BUCKET_SECONDS)
    to_bucket = int(to.timestamp() // _CANDLE_BUCKET_SECONDS)
    key = ("candles", symbol, interval, frm_bucket, to_bucket)
    return _cached(key, _CANDLE_CACHE_TTL, lambda: get_broker().get_historical_candles(symbol, interval, frm, to))


def get_expiries(underlying: str) -> list[str]:
    return get_broker().get_expiry_list(underlying)


# -- OI analytics -------------------------------------------------------------


def get_oi_summary(underlying: str, expiry: str) -> dict:
    chain = get_option_chain(underlying, expiry)
    return {"pcr": analytics.pcr(chain), "max_pain": analytics.max_pain(chain)}


def get_oi_buildup(underlying: str, expiry: str) -> list[dict]:
    chain = get_option_chain(underlying, expiry)
    key = (underlying, expiry)

    # Prefer the broker's own previous-session baseline (Dhan's oi_change/
    # prev_ltp are already derived from it — see broker_plugins/dhan/adapter.py)
    # so buildup signals are meaningful from the very first call, not just
    # after this process has polled twice.
    has_broker_baseline = any(r.ce_prev_ltp or r.pe_prev_ltp for r in chain.rows)
    if has_broker_baseline:
        previous = {
            r.strike: {
                "ce_oi": r.ce_oi - r.ce_oi_change,
                "pe_oi": r.pe_oi - r.pe_oi_change,
                "ce_ltp": r.ce_prev_ltp,
                "pe_ltp": r.pe_prev_ltp,
            }
            for r in chain.rows
        }
    else:
        previous = _prev_oi_snapshot.get(key)

    rows = analytics.classify_oi_buildup(chain, previous)
    _prev_oi_snapshot[key] = {
        r.strike: {"ce_oi": r.ce_oi, "pe_oi": r.pe_oi, "ce_ltp": r.ce_ltp, "pe_ltp": r.pe_ltp}
        for r in chain.rows
    }
    return rows


# -- Market / Volume profile ---------------------------------------------------


def _prev_trading_day(d: date) -> date:
    """Walks back to the previous weekday. Doesn't account for exchange
    holidays — a documented simplification, same spirit as pivot_service's
    mock-session fallback."""
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:  # Sat/Sun
        prev -= timedelta(days=1)
    return prev


def _session_bounds(session_date: date) -> tuple[datetime, datetime]:
    now_ist = datetime.now(IST)
    start = datetime.combine(session_date, datetime.min.time(), tzinfo=IST).replace(
        hour=_SESSION_START[0], minute=_SESSION_START[1]
    )
    end = datetime.combine(session_date, datetime.min.time(), tzinfo=IST).replace(
        hour=_SESSION_END[0], minute=_SESSION_END[1]
    )
    if session_date == now_ist.date():
        end = min(end, now_ist)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _session_candles(underlying: str, session_date: date):
    frm, to = _session_bounds(session_date)
    return get_candles(underlying, "5m", frm, to)


def get_volume_profile(underlying: str) -> dict:
    today = datetime.now(IST).date()
    candles = _session_candles(underlying, today)
    # Market hasn't printed candles yet today (pre-market / weekend / holiday)
    # — walk back through recent sessions rather than only one, since a single
    # step lands on a weekend day from a Saturday and still finds nothing.
    day = today
    for _ in range(5):
        if candles:
            break
        day = _prev_trading_day(day)
        candles = _session_candles(underlying, day)
    return analytics.volume_profile(candles)


def get_market_profile(underlying: str, previous: bool = False) -> dict:
    today = datetime.now(IST).date()
    session_date = _prev_trading_day(today) if previous else today
    candles = _session_candles(underlying, session_date)
    if not candles and not previous:
        candles = _session_candles(underlying, _prev_trading_day(today))
    vah, val, poc = analytics.market_profile(candles)
    return {"vah": vah, "val": val, "poc": poc}


def get_tpo_profile(underlying: str, previous: bool = False, bracket_minutes: int = 30) -> dict:
    """Full TPO profile for a session: letter grid, value area, Initial
    Balance, day type, single prints and (when the previous session is also
    available) the two-day value-area relationship."""
    today = datetime.now(IST).date()
    session_date = _prev_trading_day(today) if previous else today
    candles = _session_candles(underlying, session_date)
    day = session_date
    for _ in range(5):
        if candles:
            break
        day = _prev_trading_day(day)
        candles = _session_candles(underlying, day)
    if not candles:
        raise ValueError(f"No intraday candles available for '{underlying}'")

    tick = 10.0 if underlying.upper().startswith("NIFTY5") else 20.0
    profile = analytics.tpo_profile(candles, tick=tick, bracket_minutes=bracket_minutes)
    profile["session_date"] = str(day)
    profile.update(analytics.classify_day_type(profile))
    profile.update(analytics.classify_open_type(candles, profile, bracket_minutes))
    profile.update(analytics.find_excess_tails(profile))

    # two-day value migration, when the prior session is fetchable
    prev_rel = None
    prev_day = _prev_trading_day(day)
    for _ in range(5):
        prev_candles = _session_candles(underlying, prev_day)
        if prev_candles:
            prev_profile = analytics.tpo_profile(prev_candles, tick=tick, bracket_minutes=bracket_minutes)
            prev_rel = analytics.value_area_relationship(profile, prev_profile)
            prev_rel["prev_vah"] = prev_profile["vah"]
            prev_rel["prev_val"] = prev_profile["val"]
            prev_rel["prev_poc"] = prev_profile["poc"]
            prev_rel["prev_session_date"] = str(prev_day)
            break
        prev_day = _prev_trading_day(prev_day)
    profile["previous_session"] = prev_rel

    # Virgin POCs are deliberately NOT computed here: each prior session needs
    # its own intraday fetch, which is far too slow for an endpoint the page
    # polls every 2s. They live behind get_virgin_pocs() / GET
    # /market/virgin-pocs, which the page loads once on a slow cadence.
    profile["virgin_pocs"] = []
    return profile


_VIRGIN_POC_SESSIONS = 8
# Each session needs its own intraday fetch, so this is 12 broker calls. The
# Market Profile page polls every 2s — without a long cache that is 12 calls
# every two seconds, which trips Dhan's rate limiter immediately (DH-904).
# The underlying data only changes once per session, so cache for an hour and
# pace the cold-build.
_VIRGIN_POC_CACHE_TTL = 3600.0
_VIRGIN_POC_CALL_DELAY = 0.25


def _session_pocs(underlying: str, upto: date, tick: float, bracket_minutes: int) -> list:
    """POC/high/low per session for the trailing window, oldest first.

    Fetched as ONE multi-day intraday request and grouped by session date.
    Dhan happily returns weeks of intraday in a single call, and it throttles
    per-request rather than per-candle — asking session by session took over
    four minutes for eight days, while one window takes a couple of seconds.
    """
    frm = datetime.combine(upto - timedelta(days=_VIRGIN_POC_SESSIONS * 2),
                           datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    to = datetime.combine(upto, datetime.max.time().replace(microsecond=0),
                          tzinfo=IST).astimezone(timezone.utc)
    try:
        candles = get_candles(underlying, "5m", frm, to)
    except Exception:
        return []

    by_day: dict[date, list] = {}
    for c in candles:
        by_day.setdefault(c.ts.astimezone(IST).date(), []).append(c)

    sessions = []
    for d in sorted(by_day)[-_VIRGIN_POC_SESSIONS:]:
        bars = by_day[d]
        if len(bars) < 5:
            continue  # partial/holiday session — not a real profile
        prof = analytics.tpo_profile(bars, tick=tick, bracket_minutes=bracket_minutes)
        sessions.append({
            "date": d, "poc": prof["poc"],
            "high": prof["day_high"], "low": prof["day_low"],
        })
    return sessions  # already oldest-first


def _virgin_pocs(underlying: str, upto: date, tick: float, bracket_minutes: int,
                 current_price: float) -> list:
    """POCs of recent sessions that no later session has traded into."""
    key = ("session_pocs", underlying, str(upto), tick, bracket_minutes)
    sessions = _cached(key, _VIRGIN_POC_CACHE_TTL,
                       lambda: _session_pocs(underlying, upto, tick, bracket_minutes))
    if len(sessions) < 2:
        return []
    # current_price stays outside the cache — only the distance column moves
    # with it, and recomputing that is free.
    return analytics.find_virgin_pocs(sessions, current_price)


def get_virgin_pocs(underlying: str, bracket_minutes: int = 30) -> dict:
    """Untested points of control from recent sessions.

    Separate from get_tpo_profile because building it needs one intraday
    fetch per prior session — slow and rate-limit-sensitive, so it is loaded
    on its own slow cadence rather than on the profile page's 2s poll."""
    tick = 10.0 if underlying.upper().startswith("NIFTY5") else 20.0
    today = datetime.now(IST).date()
    # One multi-day fetch supplies every session in the window — no day-by-day
    # probing for "which was the last trading day", which cost one slow
    # request per non-trading day walked back.
    sessions = _cached(
        ("session_pocs", underlying, str(today), tick, bracket_minutes),
        _VIRGIN_POC_CACHE_TTL,
        lambda: _session_pocs(underlying, today, tick, bracket_minutes),
    )
    if len(sessions) < 2:
        return {
            "underlying": underlying, "as_of_session": str(today),
            "current_price": 0.0, "sessions_scanned": 0, "virgin_pocs": [],
        }

    try:
        quotes = get_quotes([underlying])
        current = quotes[0].ltp if quotes else sessions[-1]["poc"]
    except Exception:
        current = sessions[-1]["poc"]

    return {
        "underlying": underlying,
        "as_of_session": str(sessions[-1]["date"]),
        "current_price": current,
        "sessions_scanned": len(sessions),
        "virgin_pocs": analytics.find_virgin_pocs(sessions, current),
    }


# -- Composite (multi-session) profiles ----------------------------------------
#
# The single-session TPO/volume profile above answers "what did today look
# like"; the composite view answers "how did the last few sessions build on
# each other" — POC migration, whether a poor high/low ever got resolved,
# and (TPO only) a best-effort Inside/Outside-Bar read vs the prior session.
# Both reuse the same one-multi-day-fetch-then-group-by-day trick as
# _session_pocs() above, for the same reason: a call per displayed day would
# be `sessions` separate Dhan requests instead of one.


def get_tpo_profile_composite(underlying: str, sessions: int = 5, bracket_minutes: int = 30, offset: int = 0) -> list[dict]:
    """The last `sessions` trading days, each with its full TPO letter-grid,
    IB/VA, poor high/low, a trailing volume average, and a bar-structure
    label vs. the prior session (see analytics.classify_bar_structure).
    Fetches one extra day before the earliest displayed session purely as
    that structure-classification baseline.

    `offset` skips the `offset` most recent trading days before counting off
    `sessions` — the page's Older/Newer buttons page through history with it
    rather than every view being pinned to today."""
    tick = 10.0 if underlying.upper().startswith("NIFTY5") else 20.0
    today = datetime.now(IST).date()
    window = sessions + offset + 1

    frm = datetime.combine(today - timedelta(days=window * 2),
                           datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    to = datetime.combine(today, datetime.max.time().replace(microsecond=0),
                          tzinfo=IST).astimezone(timezone.utc)
    candles = get_candles(underlying, "5m", frm, to)

    by_day: dict[date, list] = {}
    for c in candles:
        by_day.setdefault(c.ts.astimezone(IST).date(), []).append(c)

    fetch_days = sorted(d for d, bars in by_day.items() if len(bars) >= 5)[-window:]
    if not fetch_days:
        return []

    profiles: dict[date, dict] = {}
    for d in fetch_days:
        prof = analytics.tpo_profile(by_day[d], tick=tick, bracket_minutes=bracket_minutes)
        prof.update(analytics.classify_day_type(prof))
        profiles[d] = prof

    end_idx = len(fetch_days) - offset
    start_idx = max(0, end_idx - sessions)
    displayed_days = fetch_days[start_idx:end_idx]
    volumes = {d: sum(c.volume for c in by_day[d]) for d in fetch_days}

    rows = []
    for d in displayed_days:
        i = fetch_days.index(d)
        prev_day = fetch_days[i - 1] if i > 0 else None
        prof = profiles[d]

        ma_days = fetch_days[: i + 1][-10:]  # trailing avg, bounded by what was actually fetched
        vol_ma = sum(volumes[x] for x in ma_days) / len(ma_days)

        rows.append({
            "session_date": str(d),
            "rows": prof["rows"],
            "vah": prof["vah"], "val": prof["val"], "poc": prof["poc"],
            "ib_high": prof["ib_high"], "ib_low": prof["ib_low"], "ib_range": prof["ib_range"],
            "session_high": prof["day_high"], "session_low": prof["day_low"],
            "poor_high": prof["day_high"] if prof["poor_high"] else None,
            "poor_low": prof["day_low"] if prof["poor_low"] else None,
            "single_prints": prof["single_prints"],
            "volume": volumes[d],
            "vol_ma": round(vol_ma, 0),
            "vol_ma_window": len(ma_days),
            "structure_label": analytics.classify_bar_structure(profiles[prev_day], prof) if prev_day else None,
        })
    return rows


def get_volume_profile_composite(underlying: str, sessions: int = 5, offset: int = 0) -> list[dict]:
    """The last `sessions` trading days of the real volume-by-price
    histogram (see get_volume_profile), each with its own rows/VAH/VAL/POC
    plus a trailing volume average. `offset` pages back through history the
    same way as get_tpo_profile_composite()."""
    today = datetime.now(IST).date()
    window = sessions + offset

    frm = datetime.combine(today - timedelta(days=window * 2),
                           datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    to = datetime.combine(today, datetime.max.time().replace(microsecond=0),
                          tzinfo=IST).astimezone(timezone.utc)
    candles = get_candles(underlying, "5m", frm, to)

    by_day: dict[date, list] = {}
    for c in candles:
        by_day.setdefault(c.ts.astimezone(IST).date(), []).append(c)

    fetch_days = sorted(d for d, bars in by_day.items() if len(bars) >= 5)[-window:] if window else []
    end_idx = len(fetch_days) - offset
    start_idx = max(0, end_idx - sessions)
    displayed_days = fetch_days[start_idx:end_idx]
    if not displayed_days:
        return []

    volumes = {d: sum(c.volume for c in by_day[d]) for d in displayed_days}

    rows = []
    for i, d in enumerate(displayed_days):
        profile = analytics.volume_profile(by_day[d])
        ma_days = displayed_days[: i + 1][-10:]
        vol_ma = sum(volumes[x] for x in ma_days) / len(ma_days)

        rows.append({
            "session_date": str(d),
            "rows": profile["rows"],
            "vah": profile["vah"], "val": profile["val"], "poc": profile["poc"],
            "session_high": max(c.high for c in by_day[d]), "session_low": min(c.low for c in by_day[d]),
            "total_volume": profile["total_volume"],
            "vol_ma": round(vol_ma, 0),
            "vol_ma_window": len(ma_days),
        })
    return rows


# -- CPR dashboard --------------------------------------------------------------


def get_cpr_dashboard(underlying: str) -> dict | None:
    from app.services import pivot_service

    levels = pivot_service.get_cpr_levels(underlying)
    if not levels:
        return None
    quotes = get_quotes([underlying])
    if not quotes:
        return None
    q = quotes[0]
    lv = levels["levels"]
    two_day = lv.two_day
    relationship_label = {
        "HIGHER_VALUE": "Ascending", "OVERLAPPING_HIGHER_VALUE": "Ascending",
        "LOWER_VALUE": "Descending", "OVERLAPPING_LOWER_VALUE": "Descending",
    }.get(two_day.relationship if two_day else "", "Neutral")

    return {
        "ltp": q.ltp, "change": q.change, "change_pct": q.change_pct,
        "cpr_width_label": lv.width.regime.capitalize(),
        "cpr_width_pct": lv.cpr_today.width_pct,
        "cpr_relationship": relationship_label,
        "day_range": lv.pdh - lv.pdl,
        "tc": lv.cpr_today.tc, "pivot": lv.cpr_today.pivot, "bc": lv.cpr_today.bc,
        "r1": lv.floor.r1, "r2": lv.floor.r2, "s1": lv.floor.s1, "s2": lv.floor.s2,
        "pdh": lv.pdh, "pdl": lv.pdl,
    }


# -- Market breadth ---------------------------------------------------------

# A much longer TTL than the plain 2-symbol quote cache: this is a single
# 48-security batched quote call, fired every poll cycle alongside the
# regular NIFTY/VIX quote call — at the same 2s TTL the two together tripped
# Dhan's undocumented marketfeed/quote rate limit (429, "may result in the
# user being blocked") in testing, repeatedly. Breadth also doesn't need
# sub-minute freshness — NIFTY 50 advance/decline doesn't meaningfully change
# that fast, so this errs conservative given the real-account rate-limit risk.
_BREADTH_CACHE_TTL = 60.0


def get_market_breadth() -> dict:
    """Cached — this is one batched Dhan quote call for 48 securities under
    the hood (see broker_plugins/dhan/adapter.py get_market_breadth)."""
    b = _cached(("breadth",), _BREADTH_CACHE_TTL, lambda: get_broker().get_market_breadth())
    return {
        "advancing": b.advancing, "declining": b.declining, "unchanged": b.unchanged,
        "new_highs": b.new_highs, "new_lows": b.new_lows,
        "universe_size": b.universe_size, "universe_label": b.universe_label,
    }


# -- IV Rank / Percentile ----------------------------------------------------

_IV_HISTORY_LOOKBACK_DAYS = 380  # ~1 trading year + buffer for the realized-vol window


def get_iv_rank(underlying: str, expiry: str) -> dict:
    """Dhan has no historical option-chain/IV endpoint (only a live snapshot),
    so there's no real historical IV series to rank against. This ranks
    today's real live ATM IV against a real trailing-realized-volatility
    series computed from real daily closes — the same "realized vol as IV
    proxy" technique already used (and disclosed) in the backtest engine.
    It will run systematically low vs true historical IV (which carries an
    extra variance-risk premium over realized vol), so treat this as
    directionally real, not an exact IV-rank feed."""
    from niftyedge_ai_engine.options_pricing import realized_volatility

    chain = get_option_chain(underlying, expiry)
    if not chain.rows:
        return {"iv_rank": 0.0, "iv_percentile": 0.0, "current_iv": 0.0, "history": []}
    atm = min(chain.rows, key=lambda r: abs(r.strike - chain.spot_price))
    current_iv = (atm.ce_iv + atm.pe_iv) / 2

    to = datetime.now(timezone.utc)
    frm = to - timedelta(days=_IV_HISTORY_LOOKBACK_DAYS)
    candles = sorted(get_candles(underlying, "1d", frm, to), key=lambda c: c.ts)
    closes = [c.close for c in candles]

    window = 20
    history = [round(realized_volatility(closes[: i + 1]) * 100, 2) for i in range(window, len(closes))]
    if not history:
        return {"iv_rank": 50.0, "iv_percentile": 50.0, "current_iv": round(current_iv, 2), "history": []}

    lo, hi = min(history), max(history)
    iv_rank = ((current_iv - lo) / (hi - lo) * 100) if hi > lo else 50.0
    iv_rank = max(0.0, min(100.0, iv_rank))
    iv_percentile = sum(1 for h in history if h < current_iv) / len(history) * 100
    return {
        "iv_rank": round(iv_rank, 1), "iv_percentile": round(iv_percentile, 1),
        "current_iv": round(current_iv, 2), "history": history[-60:],
    }


# -- CE/PE option pressure ----------------------------------------------------
#
# "Pressure" = what option writers and buyers are actually doing right now,
# read from two real fields per leg: the change in open interest (new
# positions opened vs closed) and the change in premium (ltp vs the leg's
# previous close). The standard four-way reading:
#
#   price up   + OI up   -> longs being opened      (buying)
#   price down + OI up   -> shorts being opened     (writing)
#   price down + OI down -> longs closing           (unwinding)
#   price up   + OI down -> shorts closing          (covering)
#
# Directional meaning differs by side: heavy CALL writing caps upside
# (bearish), heavy PUT writing defends downside (bullish), and the reverse
# for covering. Nothing here is modeled — every input is a real exchange
# number off the live chain.

_PRESSURE_STRIKE_WINDOW = 5  # strikes either side of ATM to aggregate


def _classify_leg(price_change: float, oi_change: float) -> str:
    if oi_change > 0:
        return "LONG_BUILDUP" if price_change > 0 else "SHORT_BUILDUP"
    return "SHORT_COVERING" if price_change > 0 else "LONG_UNWINDING"


_ACTIVITY_LABEL = {
    "LONG_BUILDUP": "buying", "SHORT_BUILDUP": "writing",
    "LONG_UNWINDING": "unwinding", "SHORT_COVERING": "covering",
}

# Bullishness of each activity, per side. Call writing is bearish, put
# writing is bullish, etc. — the sign flips between CE and PE.
_CE_BIAS = {"LONG_BUILDUP": 1, "SHORT_BUILDUP": -1, "LONG_UNWINDING": -1, "SHORT_COVERING": 1}
_PE_BIAS = {"LONG_BUILDUP": -1, "SHORT_BUILDUP": 1, "LONG_UNWINDING": 1, "SHORT_COVERING": -1}


def get_option_pressure(underlying: str, expiry: str) -> dict:
    """Net CE vs PE pressure from the live chain's real OI and premium
    changes across strikes around ATM. Score is -100..+100 (negative =
    bearish pressure, positive = bullish)."""
    chain = get_option_chain(underlying, expiry)
    spot = chain.spot_price
    if not chain.rows or not spot:
        raise ValueError("empty option chain")

    near = sorted(chain.rows, key=lambda r: abs(r.strike - spot))[: _PRESSURE_STRIKE_WINDOW * 2 + 1]
    near = sorted(near, key=lambda r: r.strike)

    ce_score = pe_score = 0.0
    ce_weight = pe_weight = 0.0
    ce_counts: dict[str, float] = {}
    pe_counts: dict[str, float] = {}
    ce_oi_added = pe_oi_added = 0.0
    strikes = []
    ce_written: list[tuple[float, float]] = []  # (strike, oi added) where calls are WRITTEN
    pe_written: list[tuple[float, float]] = []  # (strike, oi added) where puts are WRITTEN

    for r in near:
        ce_dp = r.ce_ltp - r.ce_prev_ltp if r.ce_prev_ltp else 0.0
        pe_dp = r.pe_ltp - r.pe_prev_ltp if r.pe_prev_ltp else 0.0
        ce_act = _classify_leg(ce_dp, r.ce_oi_change) if (r.ce_oi_change and ce_dp) else None
        pe_act = _classify_leg(pe_dp, r.pe_oi_change) if (r.pe_oi_change and pe_dp) else None

        # weight by capital actually committed: magnitude of the OI change
        if ce_act:
            w = abs(r.ce_oi_change)
            ce_score += _CE_BIAS[ce_act] * w
            ce_weight += w
            ce_counts[ce_act] = ce_counts.get(ce_act, 0) + w
            if ce_act == "SHORT_BUILDUP":
                ce_written.append((r.strike, r.ce_oi_change))
        if pe_act:
            w = abs(r.pe_oi_change)
            pe_score += _PE_BIAS[pe_act] * w
            pe_weight += w
            pe_counts[pe_act] = pe_counts.get(pe_act, 0) + w
            if pe_act == "SHORT_BUILDUP":
                pe_written.append((r.strike, r.pe_oi_change))

        ce_oi_added += max(0.0, r.ce_oi_change)
        pe_oi_added += max(0.0, r.pe_oi_change)
        strikes.append({
            "strike": r.strike,
            "ce_oi_change": r.ce_oi_change, "pe_oi_change": r.pe_oi_change,
            "ce_activity": _ACTIVITY_LABEL.get(ce_act) if ce_act else None,
            "pe_activity": _ACTIVITY_LABEL.get(pe_act) if pe_act else None,
        })

    ce_pressure = round(ce_score / ce_weight * 100, 1) if ce_weight else 0.0
    pe_pressure = round(pe_score / pe_weight * 100, 1) if pe_weight else 0.0
    total_w = ce_weight + pe_weight
    net = round((ce_score + pe_score) / total_w * 100, 1) if total_w else 0.0

    if net >= 25:
        label, direction = "Bullish pressure", "BULLISH"
    elif net <= -25:
        label, direction = "Bearish pressure", "BEARISH"
    else:
        label, direction = "Balanced / no clear pressure", "NEUTRAL"

    def _dominant(counts: dict) -> str | None:
        if not counts:
            return None
        return _ACTIVITY_LABEL[max(counts, key=counts.get)]

    # Heaviest fresh WRITING marks the levels the market is defending —
    # strikes where OI rose on call *buying* are demand, not resistance, so
    # only SHORT_BUILDUP strikes qualify.
    resistance = max(ce_written, key=lambda t: t[1])[0] if ce_written else None
    support = max(pe_written, key=lambda t: t[1])[0] if pe_written else None

    total_ce_oi = sum(r.ce_oi for r in chain.rows)
    total_pe_oi = sum(r.pe_oi for r in chain.rows)
    total_ce_vol = sum(r.ce_volume for r in chain.rows)
    total_pe_vol = sum(r.pe_volume for r in chain.rows)

    return {
        "underlying": underlying, "expiry": expiry, "spot_price": spot,
        "net_score": net, "direction": direction, "label": label,
        "ce_pressure": ce_pressure, "pe_pressure": pe_pressure,
        "ce_dominant": _dominant(ce_counts), "pe_dominant": _dominant(pe_counts),
        "ce_oi_added": round(ce_oi_added), "pe_oi_added": round(pe_oi_added),
        "pcr_oi": round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0.0,
        "pcr_volume": round(total_pe_vol / total_ce_vol, 2) if total_ce_vol else 0.0,
        "support_strike": support, "resistance_strike": resistance,
        "strikes_analyzed": len(near),
        "strikes": strikes,
    }


# -- Cumulative Volume Delta --------------------------------------------------


def get_cvd(underlying: str) -> dict:
    """True order-flow CVD needs tick-level buy/sell-aggressor data, which
    isn't available from Dhan's REST API (only a WebSocket tick feed would
    carry that, and this app doesn't stream). This is a disclosed proxy: each
    real intraday candle's real volume is signed by that candle's own
    direction (close >= open -> counted as buying volume) and cumulatively
    summed — a standard approximation, not tick-accurate order flow."""
    today = datetime.now(IST).date()
    candles = sorted(_session_candles(underlying, today), key=lambda c: c.ts)
    if not candles:
        candles = sorted(_session_candles(underlying, _prev_trading_day(today)), key=lambda c: c.ts)

    cumulative = 0.0
    points = []
    for c in candles:
        cumulative += c.volume if c.close >= c.open else -c.volume
        points.append({"ts": c.ts.isoformat(), "cvd": round(cumulative, 0)})
    return {"points": points, "cumulative": round(cumulative, 0)}
