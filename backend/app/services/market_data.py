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
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, object]] = {}


def _cached(key: tuple, ttl: float, fetch):
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < ttl:
            return cached[1]
        data = fetch()
        _CACHE[key] = (time.monotonic(), data)
        return data


_QUOTE_CACHE_TTL = 2.0
_OPTION_CHAIN_CACHE_TTL = 3.5
_CANDLE_CACHE_TTL = 2.0
_CANDLE_BUCKET_SECONDS = 5  # groups frm/to falling in the same few-second window

# Previous polled OI/LTP snapshot per (underlying, expiry), used only as a
# fallback when the broker doesn't supply its own previous-session baseline
# (see get_oi_buildup).
_prev_oi_snapshot: dict[tuple[str, str], dict[float, dict]] = {}


def get_quotes(symbols: list[str]):
    key = ("quotes", tuple(sorted(s.upper() for s in symbols)))
    return _cached(key, _QUOTE_CACHE_TTL, lambda: get_broker().get_quote(symbols))


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
    if not candles:
        # Market hasn't printed any candles yet today (pre-market / holiday) —
        # fall back to the last completed session so the page isn't empty.
        candles = _session_candles(underlying, _prev_trading_day(today))
    return analytics.volume_profile(candles)


def get_market_profile(underlying: str, previous: bool = False) -> dict:
    today = datetime.now(IST).date()
    session_date = _prev_trading_day(today) if previous else today
    candles = _session_candles(underlying, session_date)
    if not candles and not previous:
        session_date = _prev_trading_day(today)
        candles = _session_candles(underlying, session_date)
    session_start, _ = _session_bounds(session_date)
    return analytics.market_profile_detail(candles, session_start)


def get_market_profile_history(underlying: str, days: int = 5) -> list[dict]:
    """VAH/VAL/POC + day range for each of the last `days` completed trading
    sessions (default 5 — a full trading week), oldest first."""
    today = datetime.now(IST).date()
    sessions = []
    d = today
    while len(sessions) < days:
        d = _prev_trading_day(d)
        sessions.append(d)
    sessions.reverse()

    rows = []
    for session_date in sessions:
        candles = _session_candles(underlying, session_date)
        if not candles:
            continue
        vah, val, poc = analytics.market_profile(candles)
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        rows.append({
            "session_date": session_date.isoformat(),
            "vah": vah, "val": val, "poc": poc,
            "range_pts": round(max(highs) - min(lows), 2),
        })
    return rows


def _last_n_trading_days(end_date: date, n: int) -> list[date]:
    days = [end_date]
    d = end_date
    while len(days) < n:
        d = _prev_trading_day(d)
        days.append(d)
    days.reverse()
    return days


def get_market_profile_composite(underlying: str, sessions: int = 5) -> list[dict]:
    """Multi-session composite: the last `sessions` trading days (today's
    in-progress session included when it has candles), each with its full
    TPO letter-grid, IB/VA, poor high/low, a trailing volume average, and a
    best-effort bar-structure label vs. the prior session — the data behind
    market-profile.html's composite chart.

    Fetches one extra day before the earliest displayed session purely as a
    structure-classification baseline (so even the first displayed column
    gets a "vs yesterday" label); that extra day isn't itself returned.
    """
    today = datetime.now(IST).date()
    anchor = today if _session_candles(underlying, today) else _prev_trading_day(today)
    fetch_days = _last_n_trading_days(anchor, sessions + 1)

    details: dict[date, dict] = {}
    volumes: dict[date, float] = {}
    for session_date in fetch_days:
        candles = _session_candles(underlying, session_date)
        if not candles:
            continue
        volumes[session_date] = sum(c.volume for c in candles)
        session_start, _ = _session_bounds(session_date)
        details[session_date] = analytics.market_profile_detail(candles, session_start)

    available_days = [d for d in fetch_days if d in details]
    displayed_days = available_days[-sessions:]

    rows = []
    for session_date in displayed_days:
        i = available_days.index(session_date)
        prev_date = available_days[i - 1] if i > 0 else None
        detail = details[session_date]

        ma_days = available_days[: i + 1][-10:]  # trailing avg, bounded by what was actually fetched
        vol_ma = sum(volumes[d] for d in ma_days) / len(ma_days)

        rows.append({
            "session_date": session_date.isoformat(),
            "rows": detail["rows"],
            "vah": detail["vah"], "val": detail["val"], "poc": detail["poc"],
            "ib_high": detail["ib_high"], "ib_low": detail["ib_low"], "ib_range": detail["ib_range"],
            "session_high": detail["session_high"], "session_low": detail["session_low"],
            "poor_high": detail["poor_high"], "poor_low": detail["poor_low"],
            "excess_high": detail["excess_high"], "excess_low": detail["excess_low"],
            "volume": volumes[session_date],
            "vol_ma": round(vol_ma, 0),
            "vol_ma_window": len(ma_days),
            "structure_label": analytics.classify_bar_structure(details[prev_date], detail) if prev_date else None,
        })
    return rows


def get_volume_profile_composite(underlying: str, sessions: int = 5) -> list[dict]:
    """Multi-session composite of the real volume-by-price histogram (see
    get_volume_profile) — the last `sessions` trading days, each with its own
    rows/VAH/VAL/POC plus a trailing volume average."""
    today = datetime.now(IST).date()
    anchor = today if _session_candles(underlying, today) else _prev_trading_day(today)
    fetch_days = _last_n_trading_days(anchor, sessions)

    profiles: dict[date, dict] = {}
    volumes: dict[date, float] = {}
    highs: dict[date, float] = {}
    lows: dict[date, float] = {}
    for session_date in fetch_days:
        candles = _session_candles(underlying, session_date)
        if not candles:
            continue
        volumes[session_date] = sum(c.volume for c in candles)
        highs[session_date] = max(c.high for c in candles)
        lows[session_date] = min(c.low for c in candles)
        profiles[session_date] = analytics.volume_profile(candles)

    available_days = [d for d in fetch_days if d in profiles]
    displayed_days = available_days[-sessions:]

    rows = []
    for session_date in displayed_days:
        i = available_days.index(session_date)
        ma_days = available_days[: i + 1][-10:]
        vol_ma = sum(volumes[d] for d in ma_days) / len(ma_days)
        profile = profiles[session_date]

        rows.append({
            "session_date": session_date.isoformat(),
            "rows": profile["rows"],
            "vah": profile["vah"], "val": profile["val"], "poc": profile["poc"],
            "session_high": highs[session_date], "session_low": lows[session_date],
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
