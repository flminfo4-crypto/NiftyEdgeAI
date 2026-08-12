"""
Strike-ladder Greeks & OI — the ATM±N time grid behind frontend/greeks-oi.html.

Where atm_analysis_service follows only the rolling ATM strike, this walks the
whole near-money ladder: for each intraday bucket it resolves ATM from the real
index candle, then joins the real CE *and* PE premium candles for ATM, ITM1/2
and OTM1/2. Moneyness is resolved per side — a call is ITM *below* spot, a put
is ITM *above* it — so "CE ITM1" and "PE ITM1" are deliberately different
strikes, which is the whole point of comparing them.

Each leg's IV is solved from that bucket's own real traded premium and its
Greeks derived from that IV (same method as atm_analysis_service), so nothing
here is a model stacked on a model. Legs with no premium stay null.

Why GEX and not raw gamma
-------------------------
Raw per-contract gamma is scale-free: 0.0004 on a strike carrying 40 lakh OI
and 0.0004 on a dead strike plot as the same line, yet only one of them will
move the market. Gamma matters only once weighted by the open interest sitting
on it. GEX (gamma x OI x spot^2 x 1%) is that weighting — the rupee delta that
must be re-hedged per 1% index move — and its *sign* is what says whether
hedging flow will damp the next move or amplify it. That is why this module
exists alongside the plain Greeks chart, and why every gamma series here is
offered both raw and OI-weighted.

Sign convention (an assumption, not observed data): call OI counts positive
and put OI negative, the standard dealer-positioning convention. Real dealer
inventory is not published by any Indian exchange, so treat the flip level as
a well-known heuristic, not a measured fact — the API labels it as such.

Rate limits
-----------
One query fans out to (ladder width x 2 sides x distinct ATM strikes) Dhan
option-contract fetches — several times what the ATM-only grid needs. It
therefore reuses atm_analysis_service's *paced and cached* fetchers instead of
duplicating them (see the 429 history in market_data.py), and enforces both a
tighter max date range and a hard cap on distinct strikes per query.
"""

from datetime import date, datetime, timezone

from niftyedge_ai_engine.options_pricing import greeks as bs_greeks, implied_volatility

from app.config import settings
from app.services import market_data

# Private imports on purpose: the option-contract fetchers are paced, chunked
# and share one process-wide cache, and that pacing is what keeps Dhan from
# 429-ing. Re-implementing them here would quietly double the request rate.
from app.services.atm_analysis_service import (
    _GRID_CACHE_TTL,
    _STRIKE_STEP,
    _VALID_INTERVALS,
    _cached,
    _fetch_index_candles,
    _fetch_option_candles,
    IST,
)
from app.services.broker import get_broker

# Ladder slots, ordered deep-ITM -> ATM -> deep-OTM, and the strike offset (in
# strike steps from ATM) each one maps to per side. Calls gain moneyness as
# strikes fall, puts as they rise — hence the mirrored tables.
_SLOTS = ("ITM3", "ITM2", "ITM1", "ATM", "OTM1", "OTM2", "OTM3")
_CE_OFFSET = {"ITM3": -3, "ITM2": -2, "ITM1": -1, "ATM": 0, "OTM1": 1, "OTM2": 2, "OTM3": 3}
_PE_OFFSET = {"ITM3": 3, "ITM2": 2, "ITM1": 1, "ATM": 0, "OTM1": -1, "OTM2": -2, "OTM3": -3}

# Much tighter than atm_analysis_service's limits: this endpoint issues ~5x the
# contract fetches per distinct ATM strike, so the same 31-day 15m window would
# mean hundreds of Dhan calls.
_MAX_RANGE_DAYS = {"1m": 2, "5m": 5, "15m": 10, "30m": 10}
_MAX_DISTINCT_STRIKES = 18

# How many strikes either side of ATM the live gamma profile spans.
_PROFILE_WIDTH = 15

# A short leg quoted below this many points is not worth selling: it carries
# the full risk of the strike while contributing essentially no credit, and on
# a thin far-OTM strike it usually reflects a stale or one-sided quote rather
# than a real price. Structure selection skips those strikes outright.
_MIN_SHORT_PREMIUM = 0.5


def _slot_offset(side: str, slot: str) -> int:
    return (_CE_OFFSET if side == "CE" else _PE_OFFSET)[slot]


def _gex(gamma: float | None, oi: float | None, spot: float, side: str) -> float | None:
    """Gamma exposure per 1% move in the underlying, in the broker's own OI units.

    OI is used exactly as the broker reports it and no lot multiplier is
    applied, because whether a feed quotes OI in contracts or in underlying
    units differs by broker and is not discoverable from the payload. Getting
    that constant wrong rescales every strike identically, which leaves the
    sign, the flip level, the walls and the shape of the profile — everything
    this is actually read for — unchanged. Only the absolute magnitude is
    broker-relative, so the UI formats it as a compact figure rather than
    claiming a precise rupee amount.

    Calls positive, puts negative: the conventional dealer-positioning sign,
    documented at module level as an assumption rather than observed inventory.
    """
    if not gamma or not oi or not spot:
        return None
    value = gamma * oi * spot * spot * 0.01
    return round(value if side == "CE" else -value, 2)


def _expiry_datetime(expiry_iso: str | None) -> datetime | None:
    """Expiry settles at 15:30 IST on the expiry date."""
    if not expiry_iso:
        return None
    return datetime.combine(
        date.fromisoformat(expiry_iso), datetime.min.time().replace(hour=15, minute=30), tzinfo=IST
    )


def _leg_metrics(candle, strike: float, side: str, spot: float, ts: datetime,
                 expiry_dt: datetime | None) -> dict:
    """Solve IV from this bucket's real premium, then derive Greeks from it.

    Returns {} when the premium can't imply a sigma (traded below intrinsic,
    contract not listed, no print in the bucket) rather than substituting a
    guess — the caller renders those buckets as gaps.
    """
    if not candle or not expiry_dt:
        return {}
    t_years = max((expiry_dt - ts).total_seconds() / (365 * 86400), 1.0 / (365 * 24))
    iv = implied_volatility(candle.close, spot, strike, t_years, side)
    if not iv:
        return {}
    g = bs_greeks(spot, strike, t_years, iv, side)
    return {"iv": g.iv, "delta": g.delta, "gamma": g.gamma, "theta": g.theta, "vega": g.vega}


# -- ladder time series -------------------------------------------------------


def get_strike_greeks(underlying: str, frm_s: str, to_s: str, expiry_kind: str,
                      interval: str, depth: int = 2) -> dict:
    underlying = underlying.upper().replace(" ", "")
    if underlying not in _STRIKE_STEP:
        raise ValueError(f"Unsupported underlying '{underlying}' — use NIFTY50, NIFTYBANK or SENSEX")
    if interval not in _VALID_INTERVALS:
        raise ValueError(f"Unsupported interval '{interval}' — use 1m, 5m, 15m or 30m")
    if expiry_kind not in ("weekly", "monthly"):
        raise ValueError("expiry must be 'weekly' or 'monthly'")
    if depth not in (1, 2, 3):
        raise ValueError("depth must be 1, 2 or 3 strikes either side of ATM")

    frm_d, to_d = date.fromisoformat(frm_s), date.fromisoformat(to_s)
    if frm_d > to_d:
        raise ValueError("'from' must be on or before 'to'")
    max_days = _MAX_RANGE_DAYS[interval]
    if (to_d - frm_d).days > max_days:
        raise ValueError(
            f"Range too large for {interval} on the strike ladder — max {max_days} days. "
            f"The ladder fetches {(depth * 2 + 1) * 2} contracts per distinct ATM strike, so "
            f"wider windows would hit the broker's rate limit."
        )

    key = ("ladder", underlying, frm_s, to_s, expiry_kind, interval, depth)
    return _cached(
        key,
        lambda: _build_ladder(underlying, frm_d, to_d, expiry_kind, interval, depth),
        ttl=_GRID_CACHE_TTL,
    )


def _build_ladder(underlying: str, frm_d: date, to_d: date, expiry_kind: str,
                  interval: str, depth: int) -> dict:
    step = _STRIKE_STEP[underlying]
    slots = [s for s in _SLOTS if abs(_CE_OFFSET[s]) <= depth]
    frm = datetime.combine(frm_d, datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    to = datetime.combine(to_d, datetime.max.time().replace(microsecond=0), tzinfo=IST).astimezone(timezone.utc)

    try:
        expiry = get_broker().resolve_option_expiry(underlying, expiry_kind)
    except Exception:
        expiry = None
    expiry_dt = _expiry_datetime(expiry)

    spot_candles = _fetch_index_candles(underlying, interval, frm, to)
    atm_by_ts = {c.ts: round(c.close / step) * step for c in spot_candles}

    # Union of every strike any slot will need across the whole range — fetched
    # once per contract and shared by every bucket that references it.
    needed = sorted({atm + k * step for atm in atm_by_ts.values() for k in range(-depth, depth + 1)})
    if len(needed) > _MAX_DISTINCT_STRIKES:
        raise ValueError(
            f"Spot travelled across {len(needed)} strikes in this range — over the "
            f"{_MAX_DISTINCT_STRIKES}-strike cap for one query ({len(needed) * 2} contract fetches). "
            f"Narrow the dates or reduce the ladder depth."
        )

    candles_by_leg: dict[tuple[float, str], dict] = {}
    if expiry is not None:
        for strike in needed:
            for side in ("CE", "PE"):
                candles_by_leg[(strike, side)] = _fetch_option_candles(
                    underlying, strike, side, expiry_kind, interval, frm, to
                )
    premiums_available = any(candles_by_leg.values())

    # OI change is measured per contract against its own previous bucket, so a
    # rolling ATM never makes it compare two different books.
    prev_oi: dict[tuple[float, str], float] = {}

    buckets = []
    for c in spot_candles:
        atm = atm_by_ts[c.ts]
        ist_ts = c.ts.astimezone(IST)
        legs = {}
        call_gex = put_gex = 0.0
        has_gex = False
        peak_strike, peak_abs_gex = None, 0.0

        for side in ("CE", "PE"):
            for slot in slots:
                strike = atm + _slot_offset(side, slot) * step
                candle = candles_by_leg.get((strike, side), {}).get(c.ts)
                m = _leg_metrics(candle, strike, side, c.close, c.ts, expiry_dt)
                oi = round(candle.oi) if candle and candle.oi else None
                oi_change = None
                if oi is not None and prev_oi.get((strike, side)) is not None:
                    oi_change = oi - prev_oi[(strike, side)]
                gex = _gex(m.get("gamma"), oi, c.close, side)
                if gex is not None:
                    has_gex = True
                    if side == "CE":
                        call_gex += gex
                    else:
                        put_gex += gex
                    if abs(gex) > peak_abs_gex:
                        peak_abs_gex, peak_strike = abs(gex), strike

                legs[side + "_" + slot] = {
                    "side": side,
                    "slot": slot,
                    "strike": strike,
                    "ltp": round(candle.close, 2) if candle else None,
                    "volume": round(candle.volume) if candle else None,
                    "oi": oi,
                    "oi_change": oi_change,
                    "iv": m.get("iv"),
                    "delta": m.get("delta"),
                    "gamma": m.get("gamma"),
                    "theta": m.get("theta"),
                    "vega": m.get("vega"),
                    "gex": gex,
                }
                if oi is not None:
                    prev_oi[(strike, side)] = oi

        dte = None
        if expiry_dt:
            dte = round(max((expiry_dt - c.ts).total_seconds(), 0) / 86400, 3)

        buckets.append({
            "time": ist_ts.strftime("%d-%b %H:%M"),
            "ts": c.ts.isoformat(),
            "session_date": str(ist_ts.date()),
            "is_expiry_day": bool(expiry and str(ist_ts.date()) == expiry),
            "dte": dte,
            "spot_open": round(c.open, 2),
            "spot_high": round(c.high, 2),
            "spot_low": round(c.low, 2),
            "spot_close": round(c.close, 2),
            "atm_strike": atm,
            "legs": legs,
            "call_gex": round(call_gex, 2) if has_gex else None,
            "put_gex": round(put_gex, 2) if has_gex else None,
            "net_gex": round(call_gex + put_gex, 2) if has_gex else None,
            "peak_gamma_strike": peak_strike,
        })

    note = None
    if not premiums_available:
        note = ("No option premiums for this range — Dhan retains data only for currently-listed "
                "contracts (there is no expired-contract history), so nothing resolvable was listed "
                "over these dates. Spot and the ATM ladder are still real.")
    elif any(b["legs"].get("CE_ATM", {}).get("ltp") is None for b in buckets):
        note = ("Some buckets have no premium on some legs — those contracts weren't listed, or "
                "simply didn't print, in that bucket. Gaps are left empty rather than interpolated.")

    return {
        "underlying": underlying,
        "from_date": str(frm_d),
        "to_date": str(to_d),
        "expiry_kind": expiry_kind,
        "expiry_date": expiry,
        "interval": interval,
        "strike_step": step,
        "depth": depth,
        "slots": slots,
        "source": "mock" if settings.broker_adapter == "mock" else "broker",
        "note": note,
        "buckets": buckets,
    }


# -- live full-chain gamma profile -------------------------------------------


def _resolve_chain_greeks(chain, spot: float, expiry: str) -> tuple[dict, str]:
    """Per-leg Greeks for a whole chain, keyed (strike, side).

    Prefers the broker's own published Greeks; falls back to Black-Scholes
    from each row's REAL implied volatility when it publishes none (Dhan's
    mock adapter and some live responses omit them). Without this fallback
    every delta reads 0.0, which doesn't just blank a column — it silently
    breaks anything selecting strikes BY delta, which is most of the
    conventional constructions. The label it returns is surfaced so the UI
    never presents a derived Greek as a broker-published one.
    """
    expiry_dt = _expiry_datetime(expiry)
    now = datetime.now(timezone.utc)
    t_years = max((expiry_dt - now).total_seconds() / (365 * 86400), 1.0 / (365 * 24)) if expiry_dt else None
    broker_has = any(r.ce_gamma or r.pe_gamma or r.ce_delta or r.pe_delta for r in chain.rows)

    out: dict[tuple[float, str], dict] = {}
    for r in chain.rows:
        for side in ("CE", "PE"):
            if broker_has:
                pick = (lambda a, b: getattr(r, a if side == "CE" else b))
                out[(r.strike, side)] = {
                    "delta": pick("ce_delta", "pe_delta") or 0.0,
                    "gamma": pick("ce_gamma", "pe_gamma") or 0.0,
                    "theta": pick("ce_theta", "pe_theta") or 0.0,
                    "vega": pick("ce_vega", "pe_vega") or 0.0,
                }
                continue
            iv = (r.ce_iv if side == "CE" else r.pe_iv) or 0.0
            if not iv or not t_years:
                continue
            g = bs_greeks(spot, r.strike, t_years, iv / 100.0, side)
            out[(r.strike, side)] = {
                "delta": g.delta, "gamma": g.gamma, "theta": g.theta, "vega": g.vega,
            }
    label = "broker" if broker_has else "derived from chain IV (broker publishes no Greeks)"
    return out, label


def get_gamma_profile(underlying: str, expiry: str | None = None, width: int = _PROFILE_WIDTH) -> dict:
    """Gamma exposure by strike across the live chain, plus the flip level.

    The ladder above is a *time* view of five strikes; this is a *strike* view
    of the whole near-money chain at one instant. You need both: the ladder
    says how gamma built up through the session, the profile says where it now
    sits relative to spot.

    Gamma comes from the broker's own chain when it publishes it (Dhan does);
    otherwise it's derived by Black-Scholes from the row's real IV, which is
    disclosed in the response's `gamma_source`.
    """
    underlying = underlying.upper().replace(" ", "")
    if not expiry:
        expiries = market_data.get_expiries(underlying)
        if not expiries:
            raise ValueError(f"No expiries listed for '{underlying}'")
        expiry = expiries[0]

    chain = market_data.get_option_chain(underlying, expiry)
    spot = chain.spot_price
    greeks, gamma_source = _resolve_chain_greeks(chain, spot, expiry)

    def leg_gamma(row, side: str) -> float | None:
        return (greeks.get((row.strike, side)) or {}).get("gamma") or None

    rows = sorted(chain.rows, key=lambda r: r.strike)
    if rows and width:
        atm_idx = min(range(len(rows)), key=lambda i: abs(rows[i].strike - spot))
        rows = rows[max(0, atm_idx - width): atm_idx + width + 1]

    strikes = []
    for r in rows:
        ce = _gex(leg_gamma(r, "CE"), r.ce_oi, spot, "CE")
        pe = _gex(leg_gamma(r, "PE"), r.pe_oi, spot, "PE")
        strikes.append({
            "strike": r.strike,
            "ce_gamma": leg_gamma(r, "CE"),
            "pe_gamma": leg_gamma(r, "PE"),
            "ce_oi": r.ce_oi,
            "pe_oi": r.pe_oi,
            "ce_oi_change": r.ce_oi_change,
            "pe_oi_change": r.pe_oi_change,
            "ce_gex": ce,
            "pe_gex": pe,
            "net_gex": round((ce or 0) + (pe or 0), 2),
        })

    # Cumulative net GEX from the lowest strike upward; the flip is where it
    # crosses zero, linearly interpolated between the bracketing strikes. This
    # is the standard cumulative-crossing construction — an approximation of
    # the true "spot at which dealer gamma nets to zero", not a solve of it.
    running = 0.0
    crossings = []
    prev_strike = prev_cum = None
    for s in strikes:
        running += s["net_gex"]
        s["cumulative_gex"] = round(running, 2)
        # Sign change between consecutive strikes = the curve crossed zero
        # somewhere in between; interpolate to where.
        if prev_cum is not None and (prev_cum < 0 <= running or prev_cum > 0 >= running):
            span = running - prev_cum
            frac = (0 - prev_cum) / span if span else 0
            crossings.append(round(prev_strike + (s["strike"] - prev_strike) * frac, 2))
        prev_strike, prev_cum = s["strike"], running

    # A lumpy book can cross zero several times. The one that matters is the
    # boundary spot is actually trading against, so take the nearest — not
    # whichever happened to come last in strike order.
    flip = min(crossings, key=lambda k: abs(k - spot)) if crossings else None

    total_net = round(sum(s["net_gex"] for s in strikes), 2)
    call_wall = max((s for s in strikes if s["ce_gex"]), key=lambda s: s["ce_gex"], default=None)
    put_wall = min((s for s in strikes if s["pe_gex"]), key=lambda s: s["pe_gex"], default=None)

    return {
        "underlying": underlying,
        "expiry": expiry,
        "as_of": chain.as_of,
        "spot_price": spot,
        "net_gex": total_net,
        "gamma_regime": "POSITIVE" if total_net >= 0 else "NEGATIVE",
        "zero_gamma_strike": flip,
        "call_wall": call_wall["strike"] if call_wall else None,
        "put_wall": put_wall["strike"] if put_wall else None,
        "gamma_source": gamma_source,
        "source": "mock" if settings.broker_adapter == "mock" else "broker",
        "strikes": strikes,
    }


# -- premium-selling structure generator -------------------------------------
# Prices candidate short-premium structures off the LIVE chain — real traded
# premiums, real OI, real Greeks — anchored on the levels the gamma profile
# already identifies. This is structure arithmetic (credit, breakevens, capped
# loss, net Greeks), not a recommendation engine: it says what each structure
# WOULD be if opened now, and every candidate is returned regardless of
# whether it looks attractive, so the comparison is the output rather than a
# pick. Nothing here ranks, scores or suggests a trade.

# Exchange contract sizes, used only to express per-lot rupee figures
# alongside the unambiguous per-point ones. Exchanges revise these
# periodically (NIFTY moved 50 -> 75 in Nov 2024); the points columns are
# authoritative and unaffected if these drift.
_LOT_SIZE = {"NIFTY50": 75, "NIFTYBANK": 35, "SENSEX": 20}


def _leg(side: str, option_type: str, row, strike: float, greeks: dict) -> dict | None:
    """One structure leg priced from its real chain row. Position Greeks carry
    the leg's sign: a sold option is short gamma and long theta, which is the
    entire risk profile of premium selling and has to survive into the totals
    rather than being reported as the raw contract Greek."""
    if row is None:
        return None
    ltp = row.ce_ltp if option_type == "CE" else row.pe_ltp
    if not ltp:
        return None
    sign = 1.0 if side == "BUY" else -1.0
    g = (lambda a, b: getattr(row, a if option_type == "CE" else b))
    lg = greeks.get((strike, option_type)) or {}
    return {
        "side": side,
        "option_type": option_type,
        "strike": strike,
        "ltp": round(ltp, 2),
        "oi": g("ce_oi", "pe_oi"),
        "iv": g("ce_iv", "pe_iv"),
        "delta": round(sign * lg.get("delta", 0.0), 4),
        "gamma": round(sign * lg.get("gamma", 0.0), 6),
        "theta": round(sign * lg.get("theta", 0.0), 2),
        "vega": round(sign * lg.get("vega", 0.0), 2),
    }


def _structure(name: str, kind: str, legs: list, spot: float, lot: int,
               max_loss_points: float | None, breakevens: list, rationale: str) -> dict | None:
    """Assembles priced legs into a structure summary. Returns None when any
    leg failed to price — a partially-priced structure would understate its
    own risk, which is the one error worth refusing to make here."""
    if not legs or any(l is None for l in legs):
        return None
    # Credit is what's received net of what's paid for protection.
    credit = sum((-1 if l["side"] == "BUY" else 1) * l["ltp"] for l in legs)
    if credit <= 0:
        return None  # not a credit structure as configured; don't present it as one
    defined = max_loss_points is not None
    return {
        "name": name,
        "kind": kind,
        "defined_risk": defined,
        "legs": legs,
        "net_credit": round(credit, 2),
        "net_credit_rupees": round(credit * lot, 2),
        "max_profit": round(credit, 2),
        "max_profit_rupees": round(credit * lot, 2),
        "max_loss": round(max_loss_points, 2) if defined else None,
        "max_loss_rupees": round(max_loss_points * lot, 2) if defined else None,
        "risk_reward": round(max_loss_points / credit, 2) if defined and credit else None,
        "breakevens": [round(b, 2) for b in breakevens],
        "profit_zone_pct": round((max(breakevens) - min(breakevens)) / spot * 100, 2)
        if len(breakevens) > 1 else None,
        "net_delta": round(sum(l["delta"] for l in legs), 4),
        "net_gamma": round(sum(l["gamma"] for l in legs), 6),
        "net_theta": round(sum(l["theta"] for l in legs), 2),
        "net_vega": round(sum(l["vega"] for l in legs), 2),
        "rationale": rationale,
    }


def get_sell_candidates(underlying: str, expiry: str | None = None, width: int = _PROFILE_WIDTH) -> dict:
    """Candidate short-premium structures priced from the live chain, anchored
    on the gamma profile's own levels.

    Four constructions are always returned so they can be compared against
    each other: a wall-anchored naked strangle, a delta-anchored defined-risk
    iron condor, an ATM iron fly, and a one-sided vertical on whichever side
    of the zero-gamma flip spot is sitting. Undefined-risk structures are
    flagged as such and report `max_loss: null` rather than a comfortable
    number, because they genuinely do not have one.
    """
    underlying = underlying.upper().replace(" ", "")
    profile = get_gamma_profile(underlying, expiry, width)
    expiry = profile["expiry"]
    chain = market_data.get_option_chain(underlying, expiry)
    spot = profile["spot_price"]
    lot = _LOT_SIZE.get(underlying, 1)

    rows = sorted(chain.rows, key=lambda r: r.strike)
    by_strike = {r.strike: r for r in rows}
    if not rows:
        raise ValueError(f"No option chain rows for '{underlying}' {expiry}")
    strikes = [r.strike for r in rows]
    step = min((b - a for a, b in zip(strikes, strikes[1:])), default=50) or 50
    atm = min(strikes, key=lambda s: abs(s - spot))
    greeks, greeks_source = _resolve_chain_greeks(chain, spot, expiry)

    def _wing_partner(strike: float, option_type: str, wing: float) -> float:
        return strike + wing if option_type == "CE" else strike - wing

    def _sellable(strike: float, option_type: str) -> bool:
        """A strike worth SHORTING: it has to actually pay for the risk taken."""
        row = by_strike.get(strike)
        if row is None:
            return False
        ltp = row.ce_ltp if option_type == "CE" else row.pe_ltp
        return bool(ltp) and ltp >= _MIN_SHORT_PREMIUM

    def nearest_by_delta(option_type: str, target: float, wing: float = 0.0) -> float | None:
        """Strike whose |delta| is closest to `target`, on the OTM side only —
        selling an ITM option is a different trade with different intent.

        `wing` constrains the search to strikes that still leave room for a
        protective leg inside the chain. Without it the delta-selected short
        can land on the outermost strike, whose wing simply doesn't exist, and
        the whole defined-risk structure vanishes from the comparison with no
        explanation — which is exactly the structure a reader most needs to
        see next to the naked one.

        Falls back to a fixed offset when no leg has a usable delta at all (a
        chain with neither published Greeks nor usable IV).
        """
        best, err = None, float("inf")
        for r in rows:
            if option_type == "CE" and r.strike <= spot:
                continue
            if option_type == "PE" and r.strike >= spot:
                continue
            if wing and _wing_partner(r.strike, option_type, wing) not in by_strike:
                continue
            if not _sellable(r.strike, option_type):
                continue
            d = abs((greeks.get((r.strike, option_type)) or {}).get("delta") or 0.0)
            if not d:
                continue
            if abs(d - target) < err:
                err, best = abs(d - target), r.strike
        if best is not None:
            return best
        fallback = atm + step * 4 if option_type == "CE" else atm - step * 4
        if fallback in by_strike and (not wing or _wing_partner(fallback, option_type, wing) in by_strike):
            return fallback
        return None

    def otm_strike(preferred: float | None, option_type: str, fallback_delta: float) -> float | None:
        """Use the gamma wall when it actually sits OTM; otherwise fall back to
        a delta-selected strike. A wall on the wrong side of spot is real
        information, but it is not a strike you can sell into."""
        if preferred is not None and _sellable(preferred, option_type):
            if option_type == "CE" and preferred > spot:
                return preferred
            if option_type == "PE" and preferred < spot:
                return preferred
        return nearest_by_delta(option_type, fallback_delta)

    candidates = []

    # 1. Wall-anchored naked strangle -- shorts sat on the two strikes carrying
    #    the most call and put gamma exposure.
    ce_wall = otm_strike(profile.get("call_wall"), "CE", 0.20)
    pe_wall = otm_strike(profile.get("put_wall"), "PE", 0.20)
    if ce_wall and pe_wall:
        legs = [_leg("SELL", "CE", by_strike.get(ce_wall), ce_wall, greeks),
                _leg("SELL", "PE", by_strike.get(pe_wall), pe_wall, greeks)]
        if all(legs):
            credit = sum(l["ltp"] for l in legs)
            candidates.append(_structure(
                "Wall-anchored Short Strangle", "naked_strangle", legs, spot, lot,
                None, [pe_wall - credit, ce_wall + credit],
                "Shorts sit on the strikes carrying the most call and put gamma exposure, "
                "where dealer hedging tends to resist price. Undefined risk on both sides: "
                "a move past a wing is capped only by whatever stop is applied manually.",
            ))

    # 2. Delta-anchored iron condor -- the conventional defined-risk build.
    wing = step * 2
    ce_short = nearest_by_delta("CE", 0.20, wing)
    pe_short = nearest_by_delta("PE", 0.20, wing)
    if ce_short and pe_short:
        legs = [_leg("SELL", "CE", by_strike.get(ce_short), ce_short, greeks),
                _leg("BUY", "CE", by_strike.get(ce_short + wing), ce_short + wing, greeks),
                _leg("SELL", "PE", by_strike.get(pe_short), pe_short, greeks),
                _leg("BUY", "PE", by_strike.get(pe_short - wing), pe_short - wing, greeks)]
        if all(legs):
            credit = sum((-1 if l["side"] == "BUY" else 1) * l["ltp"] for l in legs)
            candidates.append(_structure(
                "Iron Condor (~0.20 delta)", "iron_condor", legs, spot, lot,
                wing - credit, [pe_short - credit, ce_short + credit],
                "Shorts chosen at roughly 0.20 delta with a protective wing bought beyond "
                f"each, {int(wing)} points out. Max loss is structurally capped at the wing "
                "width minus the credit, however far the underlying travels.",
            ))

    # 3. ATM iron fly -- most credit, narrowest zone.
    fly_wing = step * 4
    legs = [_leg("SELL", "CE", by_strike.get(atm), atm, greeks),
            _leg("SELL", "PE", by_strike.get(atm), atm, greeks),
            _leg("BUY", "CE", by_strike.get(atm + fly_wing), atm + fly_wing, greeks),
            _leg("BUY", "PE", by_strike.get(atm - fly_wing), atm - fly_wing, greeks)]
    if all(legs):
        credit = sum((-1 if l["side"] == "BUY" else 1) * l["ltp"] for l in legs)
        candidates.append(_structure(
            "Iron Fly (ATM)", "iron_fly", legs, spot, lot,
            fly_wing - credit, [atm - credit, atm + credit],
            "Sells the ATM straddle with a wing bought either side. Collects the most "
            "credit of the defined-risk builds for the narrowest zone in which it wins — "
            "it needs the underlying to sit still, not merely stay in a range.",
        ))

    # 4. One-sided vertical, chosen by which side of the gamma flip spot sits.
    #    Above the flip dealers are long gamma and dips get bought, so the put
    #    side is the one being defended; below it, the reverse.
    flip = profile.get("zero_gamma_strike")
    above_flip = flip is None or spot >= flip
    if above_flip and pe_short:
        legs = [_leg("SELL", "PE", by_strike.get(pe_short), pe_short, greeks),
                _leg("BUY", "PE", by_strike.get(pe_short - wing), pe_short - wing, greeks)]
        if all(legs):
            credit = sum((-1 if l["side"] == "BUY" else 1) * l["ltp"] for l in legs)
            where = "no flip level was found in range" if flip is None else f"spot is above the {flip:.0f} gamma flip"
            candidates.append(_structure(
                "Bull Put Spread", "bull_put_spread", legs, spot, lot,
                wing - credit, [pe_short - credit],
                f"One-sided and defined-risk: {where}, the regime in which hedging flow "
                "tends to damp downside. Risk is only below the short put.",
            ))
    elif ce_short:
        legs = [_leg("SELL", "CE", by_strike.get(ce_short), ce_short, greeks),
                _leg("BUY", "CE", by_strike.get(ce_short + wing), ce_short + wing, greeks)]
        if all(legs):
            credit = sum((-1 if l["side"] == "BUY" else 1) * l["ltp"] for l in legs)
            candidates.append(_structure(
                "Bear Call Spread", "bear_call_spread", legs, spot, lot,
                wing - credit, [ce_short + credit],
                f"One-sided and defined-risk: spot is below the {flip:.0f} gamma flip, where "
                "hedging flow tends to amplify moves rather than damp them, so upside is "
                "sold rather than downside. Risk is only above the short call.",
            ))

    return {
        "underlying": underlying,
        "expiry": expiry,
        "as_of": chain.as_of,
        "spot_price": spot,
        "atm_strike": atm,
        "strike_step": step,
        "lot_size": lot,
        "call_wall": profile.get("call_wall"),
        "put_wall": profile.get("put_wall"),
        "zero_gamma_strike": flip,
        "gamma_regime": profile["gamma_regime"],
        "source": profile["source"],
        "note": (
            "Structures priced from the live chain's real traded premiums, OI and Greeks. "
            "These are constructions for comparison, not recommendations — nothing here is "
            "ranked or suggested, and undefined-risk builds report no max loss because they "
            "genuinely have none. Premiums move continuously; a quote is stale the moment "
            "it is drawn."
        ),
        "candidates": [c for c in candidates if c],
    }
