"""
Data Detail Analysis — the ATM time grid behind frontend/data-analysis.html.

For each intraday time bucket in the requested range, computes the ROLLING
ATM strike from the real index candle, joins the real CE/PE premium candles
of that strike (nearest live weekly/monthly expiry), and derives a short
computed "reason" for the move (spot direction, delta-vs-theta attribution,
ATM rolls, PDH/PDL crossings).

Data honesty notes, enforced here rather than papered over:
- Dhan keeps NO data for expired option contracts, so premiums only populate
  for buckets inside the chosen contract's own listing window. Spot columns
  always fill; missing premiums stay null with the reason left factual.
- Dhan's intraday API has no 30-minute interval — 30m rows are aggregated
  server-side from real 15m candles (disclosed, not synthetic).

Every option-contract fetch is paced and cached: this is a click-triggered
page (no 2s polling), but one grid can still need several contract fetches —
see the 429 history in market_data.py for why pacing is non-negotiable.
"""

import threading
import time
from datetime import date, datetime, timedelta, timezone

from niftyedge_ai_engine.options_pricing import greeks as bs_greeks, implied_volatility

from app.config import settings
from app.services import market_data
from app.services.broker import get_broker

IST = timezone(timedelta(hours=5, minutes=30))

_STRIKE_STEP = {"NIFTY50": 50, "NIFTYBANK": 100, "SENSEX": 100}
_VALID_INTERVALS = {"1m", "5m", "15m", "30m"}
_CHUNK_DAYS = 5           # Dhan intraday requests are safest in <=5-day windows
_CONTRACT_CALL_DELAY = 0.2
_GRID_CACHE_TTL = 120.0
_MAX_RANGE_DAYS = {"1m": 7, "5m": 15, "15m": 31, "30m": 31}

# RLock, not Lock: the whole-grid cache entry's fetch runs nested per-contract
# _cached() calls on the same lock (same thread) — a plain Lock self-deadlocks.
_CACHE_LOCK = threading.RLock()
_CACHE: dict[tuple, tuple[float, object]] = {}


def _cached(key: tuple, fetch, ttl: float):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < ttl:
            return hit[1]
        data = fetch()
        _CACHE[key] = (time.monotonic(), data)
        return data


def _chunked_ranges(frm: datetime, to: datetime):
    cur = frm
    while cur < to:
        nxt = min(cur + timedelta(days=_CHUNK_DAYS), to)
        yield cur, nxt
        cur = nxt


def _fetch_index_candles(underlying: str, interval: str, frm: datetime, to: datetime):
    fetch_interval = "15m" if interval == "30m" else interval
    out = []
    for c_frm, c_to in _chunked_ranges(frm, to):
        out.extend(market_data.get_candles(underlying, fetch_interval, c_frm, c_to))
    seen = {}
    for c in sorted(out, key=lambda c: c.ts):
        seen[c.ts] = c
    candles = list(seen.values())
    if interval == "30m":
        candles = _aggregate_30m(candles)
    return candles


def _bucket_30m(ts: datetime) -> datetime:
    """Aligns a 15m candle timestamp onto its 30-minute bucket, anchored to
    the 09:15 IST session open (buckets: 09:15, 09:45, 10:15, ...)."""
    ist = ts.astimezone(IST)
    session_open = ist.replace(hour=9, minute=15, second=0, microsecond=0)
    if ist < session_open:
        return ts
    offset_min = int((ist - session_open).total_seconds() // 60)
    return (session_open + timedelta(minutes=(offset_min // 30) * 30)).astimezone(timezone.utc)


def _aggregate_30m(candles15: list) -> list:
    from broker_plugins.core.interface import Candle

    buckets: dict[datetime, list] = {}
    for c in candles15:
        buckets.setdefault(_bucket_30m(c.ts), []).append(c)
    out = []
    for ts in sorted(buckets):
        parts = sorted(buckets[ts], key=lambda c: c.ts)
        out.append(Candle(
            ts=ts, open=parts[0].open, high=max(p.high for p in parts),
            low=min(p.low for p in parts), close=parts[-1].close,
            volume=sum(p.volume for p in parts),
        ))
    return out


def _fetch_option_candles(underlying: str, strike: float, option_type: str,
                          expiry_kind: str, interval: str, frm: datetime, to: datetime) -> dict:
    """{bucket_ts: candle} for one contract; {} when the broker has nothing
    (mock disabled, contract listed after `frm`, etc.)."""
    fetch_interval = "15m" if interval == "30m" else interval

    def fetch():
        out = []
        try:
            for c_frm, c_to in _chunked_ranges(frm, to):
                time.sleep(_CONTRACT_CALL_DELAY)
                out.extend(get_broker().get_option_intraday_candles(
                    underlying, strike, option_type, expiry_kind, fetch_interval, c_frm, c_to,
                ))
        except Exception:
            return []
        return sorted({c.ts: c for c in out}.values(), key=lambda c: c.ts)

    key = ("opt_candles", underlying, int(strike), option_type, expiry_kind, fetch_interval,
           frm.strftime("%Y%m%d%H%M"), to.strftime("%Y%m%d%H%M"))
    candles = _cached(key, fetch, ttl=_GRID_CACHE_TTL)
    if interval == "30m":
        candles = _aggregate_30m(list(candles))
    return {c.ts: c for c in candles}


def _prior_day_levels(underlying: str, frm: datetime, to: datetime) -> dict[date, dict]:
    """date -> prior session's {pdh, pdl} from real daily candles, for the
    reason column's level-crossing checks."""
    try:
        daily = market_data.get_candles(underlying, "1d", frm - timedelta(days=10), to + timedelta(days=1))
    except Exception:
        return {}
    days = sorted({c.ts.astimezone(IST).date(): c for c in daily}.items())
    out = {}
    for i in range(1, len(days)):
        prior = days[i - 1][1]
        out[days[i][0]] = {"pdh": prior.high, "pdl": prior.low}
    return out


def _oi_signal(price_change: float, oi_change: float, side: str) -> str | None:
    """Standard OI + price interpretation matrix. Both inputs are real
    per-bucket deltas (Dhan's open_interest series), so this is a read of
    actual positioning, not an inference from price alone:
      price up   + OI up   = fresh longs being added   (long buildup)
      price down + OI up   = fresh shorts being added  (short buildup)
      price down + OI down = longs closing out         (long unwinding)
      price up   + OI down = shorts buying back        (short covering)
    """
    if not price_change or not oi_change:
        return None
    if price_change > 0 and oi_change > 0:
        return f"{side} long buildup"
    if price_change < 0 and oi_change > 0:
        return f"{side} short buildup"
    if price_change < 0 and oi_change < 0:
        return f"{side} long unwinding"
    return f"{side} short covering"


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or not old:
        return None
    return (new - old) / old * 100


def _reason(prev_row: dict | None, row: dict, levels: dict | None) -> str:
    notes = []
    if prev_row is None:
        return "Session/range start"
    if row["atm_strike"] != prev_row["atm_strike"]:
        notes.append(f"ATM rolled {int(prev_row['atm_strike'])}→{int(row['atm_strike'])}")

    prev_spot, spot = prev_row["spot_close"], row["spot_close"]
    spot_pct = (spot - prev_spot) / prev_spot * 100 if prev_spot else 0.0
    if spot_pct >= 0.12:
        notes.append(f"Spot rallied {spot_pct:+.2f}% — CE gains delta, PE bleeds")
    elif spot_pct <= -0.12:
        notes.append(f"Spot fell {spot_pct:+.2f}% — PE gains delta, CE bleeds")

    straddle, prev_straddle = row.get("straddle"), prev_row.get("straddle")
    if straddle is not None and prev_straddle:
        ds_pct = (straddle - prev_straddle) / prev_straddle * 100
        if abs(spot_pct) < 0.12:
            if ds_pct <= -1.5:
                notes.append(f"Flat spot, straddle {ds_pct:+.1f}% — theta/IV crush")
            elif ds_pct >= 1.5:
                notes.append(f"Flat spot, straddle {ds_pct:+.1f}% — IV expansion / hedging demand")
            elif not notes:
                notes.append("Rangebound — premiums drifting with time decay")
    elif not notes and abs(spot_pct) < 0.12:
        notes.append("Rangebound")

    # Real OI positioning — only meaningful when the ATM strike didn't roll
    # (a roll compares two different contracts' books, which says nothing).
    if row["atm_strike"] == prev_row["atm_strike"]:
        for side, close_k, oi_k in (("CE", "ce_close", "ce_oi"), ("PE", "pe_close", "pe_oi")):
            d_price = (row.get(close_k) or 0) - (prev_row.get(close_k) or 0)
            d_oi = (row.get(oi_k) or 0) - (prev_row.get(oi_k) or 0)
            if row.get(oi_k) and prev_row.get(oi_k):
                sig = _oi_signal(d_price, d_oi, side)
                if sig:
                    notes.append(sig)

    iv_shift = _pct(row.get("ce_iv"), prev_row.get("ce_iv"))
    if iv_shift is not None and abs(iv_shift) >= 5:
        notes.append(f"CE IV {iv_shift:+.1f}%")

    if levels:
        pdh, pdl = levels.get("pdh"), levels.get("pdl")
        if pdh and prev_spot < pdh <= row["spot_high"]:
            notes.append(f"Crossed PDH {pdh:.2f}")
        if pdl and prev_spot > pdl >= row["spot_low"]:
            notes.append(f"Broke PDL {pdl:.2f}")
    return "; ".join(notes) if notes else "—"


def get_atm_analysis(underlying: str, frm_s: str, to_s: str, expiry_kind: str, interval: str) -> dict:
    underlying = underlying.upper().replace(" ", "")
    if underlying not in _STRIKE_STEP:
        raise ValueError(f"Unsupported underlying '{underlying}' — use NIFTY50, NIFTYBANK or SENSEX")
    if interval not in _VALID_INTERVALS:
        raise ValueError(f"Unsupported interval '{interval}' — use 1m, 5m, 15m or 30m")
    if expiry_kind not in ("weekly", "monthly"):
        raise ValueError("expiry must be 'weekly' or 'monthly'")

    frm_d, to_d = date.fromisoformat(frm_s), date.fromisoformat(to_s)
    if frm_d > to_d:
        raise ValueError("'from' must be on or before 'to'")
    max_days = _MAX_RANGE_DAYS[interval]
    if (to_d - frm_d).days > max_days:
        raise ValueError(f"Range too large for {interval} — max {max_days} days per query")

    key = ("grid", underlying, frm_s, to_s, expiry_kind, interval)
    return _cached(key, lambda: _build_grid(underlying, frm_d, to_d, expiry_kind, interval), ttl=_GRID_CACHE_TTL)


def _build_grid(underlying: str, frm_d: date, to_d: date, expiry_kind: str, interval: str) -> dict:
    step = _STRIKE_STEP[underlying]
    frm = datetime.combine(frm_d, datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    to = datetime.combine(to_d, datetime.max.time().replace(microsecond=0), tzinfo=IST).astimezone(timezone.utc)

    try:
        expiry = get_broker().resolve_option_expiry(underlying, expiry_kind)
    except Exception:
        expiry = None

    spot_candles = _fetch_index_candles(underlying, interval, frm, to)
    level_by_date = _prior_day_levels(underlying, frm, to)

    atm_by_ts = {c.ts: round(c.close / step) * step for c in spot_candles}
    unique_strikes = sorted(set(atm_by_ts.values()))

    ce_by_strike: dict[float, dict] = {}
    pe_by_strike: dict[float, dict] = {}
    premiums_available = expiry is not None
    if premiums_available:
        for strike in unique_strikes:
            ce_by_strike[strike] = _fetch_option_candles(underlying, strike, "CE", expiry_kind, interval, frm, to)
            pe_by_strike[strike] = _fetch_option_candles(underlying, strike, "PE", expiry_kind, interval, frm, to)
        premiums_available = any(ce_by_strike[s] or pe_by_strike[s] for s in unique_strikes)

    # Expiry settles at 15:30 IST on the expiry date; time-to-expiry per
    # bucket drives both the IV solve and the Greeks.
    expiry_dt = None
    if expiry:
        expiry_dt = datetime.combine(
            date.fromisoformat(expiry), datetime.min.time().replace(hour=15, minute=30), tzinfo=IST
        )

    def _metrics(candle, strike: float, opt: str, spot: float, ts: datetime) -> dict:
        """Solve IV from this bucket's REAL premium, then derive Greeks from
        it — so both describe the contract as the market actually priced it.
        Returns empty values when the premium can't imply a sigma (below
        intrinsic, expired, untraded) rather than substituting a guess."""
        if not candle or not expiry_dt:
            return {}
        t_years = max((expiry_dt - ts).total_seconds() / (365 * 86400), 1.0 / (365 * 24))
        iv = implied_volatility(candle.close, spot, strike, t_years, opt)
        if not iv:
            return {}
        g = bs_greeks(spot, strike, t_years, iv, opt)
        return {"iv": g.iv, "delta": g.delta, "gamma": g.gamma, "theta": g.theta, "vega": g.vega}

    rows = []
    prev_row = None
    for c in spot_candles:
        atm = atm_by_ts[c.ts]
        ce = ce_by_strike.get(atm, {}).get(c.ts)
        pe = pe_by_strike.get(atm, {}).get(c.ts)
        ce_m = _metrics(ce, atm, "CE", c.close, c.ts)
        pe_m = _metrics(pe, atm, "PE", c.close, c.ts)
        row = {
            "time": c.ts.astimezone(IST).strftime("%d-%b %H:%M"),
            "ts": c.ts.isoformat(),
            "spot_close": round(c.close, 2),
            "spot_high": round(c.high, 2),
            "spot_low": round(c.low, 2),
            "atm_strike": atm,
            "ce_high": round(ce.high, 2) if ce else None,
            "ce_low": round(ce.low, 2) if ce else None,
            "ce_close": round(ce.close, 2) if ce else None,
            "pe_high": round(pe.high, 2) if pe else None,
            "pe_low": round(pe.low, 2) if pe else None,
            "pe_close": round(pe.close, 2) if pe else None,
            "straddle": round(ce.close + pe.close, 2) if ce and pe else None,
            "ce_volume": round(ce.volume) if ce else None,
            "pe_volume": round(pe.volume) if pe else None,
            "ce_oi": round(ce.oi) if ce and ce.oi else None,
            "pe_oi": round(pe.oi) if pe and pe.oi else None,
            "ce_iv": ce_m.get("iv"), "ce_delta": ce_m.get("delta"), "ce_gamma": ce_m.get("gamma"),
            "ce_theta": ce_m.get("theta"), "ce_vega": ce_m.get("vega"),
            "pe_iv": pe_m.get("iv"), "pe_delta": pe_m.get("delta"), "pe_gamma": pe_m.get("gamma"),
            "pe_theta": pe_m.get("theta"), "pe_vega": pe_m.get("vega"),
        }
        # OI change vs the previous bucket, only when the strike is unchanged
        same_strike = prev_row is not None and prev_row["atm_strike"] == atm
        for k in ("ce_oi", "pe_oi"):
            row[k + "_change"] = (
                row[k] - prev_row[k] if same_strike and row.get(k) and prev_row.get(k) else None
            )
        row["reason"] = _reason(prev_row, row, level_by_date.get(c.ts.astimezone(IST).date()))
        rows.append(row)
        prev_row = row

    note = None
    if not premiums_available:
        note = ("Option premiums unavailable for this range — Dhan only retains data for "
                "currently-listed contracts (no expired-contract history), and nothing "
                "resolvable was listed over the requested dates. Spot analysis is still real.")
    elif any(r["ce_close"] is None for r in rows):
        note = ("Some rows have no premium — the contract wasn't listed (or didn't trade) "
                "during those buckets. Dhan keeps no expired-contract history.")

    return {
        "underlying": underlying,
        "from_date": str(frm_d),
        "to_date": str(to_d),
        "expiry_kind": expiry_kind,
        "expiry_date": expiry,
        "interval": interval,
        "strike_step": step,
        "source": "mock" if settings.broker_adapter == "mock" else "broker",
        "note": note,
        "rows": rows,
    }
