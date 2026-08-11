"""
Shared real-data calculations used across several pages/endpoints, so each
one doesn't reimplement the same math against broker_plugins types.

Nothing here talks to a broker directly — callers pass in candles/positions/
option-chain snapshots already fetched via app.services.market_data / broker.
"""

from collections import defaultdict

from broker_plugins.core.interface import BrokerPosition, Candle, OptionChainSnapshot, TradeHistoryEntry

# Standard Value Area target: one standard deviation of a normal distribution
# covers 68.26% of observations; every major platform (CBOT, Sierra Chart,
# TradingView) rounds that in practice to 70%.
VALUE_AREA_PCT = 0.70


def _expand_value_area(prices_sorted: list[float], weight_by_price: dict[float, float],
                        poc: float, pct: float = VALUE_AREA_PCT) -> tuple[float, float]:
    """Standard Market/Volume Profile Value Area construction: starting at the
    POC, repeatedly compare the PAIR of rows just above the current band to
    the PAIR just below, add whichever pair carries more weight, and repeat
    until >= `pct` of total weight is enclosed (stopping on either side once
    it runs out of rows). This is the CBOT/Sierra Chart/TradingView
    convention — comparing one row at a time instead of two, or picking the
    top-N highest-weight rows regardless of position, both silently produce
    the wrong (or non-contiguous) VAH/VAL on multi-modal profiles.

    Returns (val, vah) — both inclusive, always a contiguous band around the POC.
    """
    n = len(prices_sorted)
    poc_i = prices_sorted.index(poc)
    lo_i = hi_i = poc_i
    total = sum(weight_by_price.values())
    target = total * pct
    acc = weight_by_price[poc]

    while acc < target and (lo_i > 0 or hi_i < n - 1):
        above_idx = range(hi_i + 1, min(hi_i + 3, n))
        below_idx = range(max(lo_i - 2, 0), lo_i)
        above = sum(weight_by_price[prices_sorted[i]] for i in above_idx)
        below = sum(weight_by_price[prices_sorted[i]] for i in below_idx)
        have_above, have_below = hi_i < n - 1, lo_i > 0
        if have_above and (above >= below or not have_below):
            hi_i = min(hi_i + 2, n - 1)
            acc += above
        else:
            lo_i = max(lo_i - 2, 0)
            acc += below

    return prices_sorted[lo_i], prices_sorted[hi_i]


def market_profile(candles: list[Candle]) -> tuple[float, float, float]:
    """Returns (vah, val, poc) from an intraday profile of the given candles.

    Indices (NIFTY50/BANKNIFTY/...) have no real traded volume of their own
    (only their derivatives trade), so this weights each candle's price range
    by time-in-range (a TPO-style profile) rather than requiring true traded
    volume — a standard Market Profile variant, not a fabricated substitute.
    """
    if not candles:
        raise ValueError("no candles to build a market profile from")
    tick = 5.0
    weight_by_price: dict[float, float] = defaultdict(float)
    for c in candles:
        lo = round(c.low / tick) * tick
        hi = round(c.high / tick) * tick
        if hi <= lo:
            hi = lo + tick
        levels = int(round((hi - lo) / tick)) + 1
        w = 1.0 / levels
        price = lo
        for _ in range(levels):
            weight_by_price[price] += w
            price += tick

    poc = max(weight_by_price, key=weight_by_price.get)
    val, vah = _expand_value_area(sorted(weight_by_price), weight_by_price, poc)
    return vah, val, poc


def volume_profile(candles: list[Candle], tick: float = 6.0) -> dict:
    """A real volume-by-price histogram — each candle's actual traded volume
    (not a time-weighted proxy) spread evenly across the price levels between
    its low and high, bucketed at `tick`-wide levels. Returns rows sorted by
    price plus the VAH/VAL/POC/total derived from that same real histogram.
    """
    if not candles:
        raise ValueError("no candles to build a volume profile from")
    volume_by_price: dict[float, float] = defaultdict(float)
    for c in candles:
        lo = round(c.low / tick) * tick
        hi = round(c.high / tick) * tick
        if hi <= lo:
            hi = lo + tick
        levels = int(round((hi - lo) / tick)) + 1
        vol_per_level = c.volume / levels
        price = lo
        for _ in range(levels):
            volume_by_price[price] += vol_per_level
            price += tick

    total = sum(volume_by_price.values())
    poc = max(volume_by_price, key=volume_by_price.get)
    val, vah = _expand_value_area(sorted(volume_by_price), volume_by_price, poc)

    rows = [{"price": p, "volume": v} for p, v in sorted(volume_by_price.items())]
    return {"rows": rows, "vah": vah, "val": val, "poc": poc, "total_volume": total}


def cpr_levels(prev_day: Candle) -> dict:
    """Standard Central Pivot Range + R1-R3/S1-S3, from the prior trading day's OHLC."""
    h, l, c = prev_day.high, prev_day.low, prev_day.close
    pivot = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * pivot - bc
    rng = h - l
    return {
        "pivot": pivot,
        "bc": min(bc, tc),
        "tc": max(bc, tc),
        "r1": 2 * pivot - l,
        "r2": pivot + rng,
        "r3": (2 * pivot - l) + rng,
        "s1": 2 * pivot - h,
        "s2": pivot - rng,
        "s3": (2 * pivot - h) - rng,
    }


def cpr_width_label(cpr: dict) -> tuple[float, str]:
    """Standard CPR-width classification: (TC-BC) as a % of Pivot."""
    width_pct = ((cpr["tc"] - cpr["bc"]) / cpr["pivot"] * 100) if cpr["pivot"] else 0.0
    if width_pct < 0.3:
        return width_pct, "Narrow"
    if width_pct < 0.5:
        return width_pct, "Medium"
    return width_pct, "Wide"


def pcr(chain: OptionChainSnapshot) -> float:
    """Put/Call ratio by open interest across the whole chain."""
    total_ce = sum(r.ce_oi for r in chain.rows)
    total_pe = sum(r.pe_oi for r in chain.rows)
    return round(total_pe / total_ce, 2) if total_ce else 0.0


def max_pain(chain: OptionChainSnapshot) -> float:
    """The strike at which option *writers* collectively owe the least — i.e.
    where option buyers' aggregate payout is minimized. Standard max-pain calc."""
    if not chain.rows:
        raise ValueError("no option chain rows to compute max pain from")
    strikes = [r.strike for r in chain.rows]
    best_strike, best_payout = strikes[0], None
    for candidate in strikes:
        payout = 0.0
        for r in chain.rows:
            payout += r.ce_oi * max(0.0, candidate - r.strike)
            payout += r.pe_oi * max(0.0, r.strike - candidate)
        if best_payout is None or payout < best_payout:
            best_strike, best_payout = candidate, payout
    return best_strike


def oi_walls(chain: OptionChainSnapshot, top_n: int = 3) -> dict:
    """Strikes with the heaviest CE OI (resistance) / PE OI (support)."""
    by_ce = sorted(chain.rows, key=lambda r: -r.ce_oi)[:top_n]
    by_pe = sorted(chain.rows, key=lambda r: -r.pe_oi)[:top_n]
    return {
        "resistance": [{"strike": r.strike, "oi": r.ce_oi} for r in by_ce],
        "support": [{"strike": r.strike, "oi": r.pe_oi} for r in by_pe],
    }


def match_closed_trades(trades: list[TradeHistoryEntry]) -> list[dict]:
    """FIFO-matches raw fills per instrument into closed round-trips: {instrument,
    side (of the closing fill), quantity, entry_price, exit_price, pnl, closed_at}.
    Still-open remainders aren't returned — this is only "what actually closed."
    Shared by the real trade-history log (signal_service) and the real closed-
    positions list (order_service) so the matching logic lives in one place.
    """
    open_lots: dict[str, list[dict]] = defaultdict(list)
    closed = []
    for t in sorted(trades, key=lambda t: t.traded_at):
        book = open_lots[t.instrument]
        opposite_side = "SELL" if t.side == "BUY" else "BUY"
        remaining = t.quantity
        while remaining > 0 and book and book[0]["side"] == opposite_side:
            lot = book[0]
            matched_qty = min(remaining, lot["qty"])
            direction = 1 if lot["side"] == "BUY" else -1
            pnl = direction * (t.traded_price - lot["price"]) * matched_qty
            closed.append(
                {
                    "instrument": t.instrument,
                    "side": t.side,
                    "quantity": matched_qty,
                    "entry_price": lot["price"],
                    "exit_price": t.traded_price,
                    "pnl": round(pnl, 2),
                    "closed_at": t.traded_at,
                }
            )
            lot["qty"] -= matched_qty
            remaining -= matched_qty
            if lot["qty"] <= 0:
                book.pop(0)
        if remaining > 0:
            book.append({"side": t.side, "qty": remaining, "price": t.traded_price})
    closed.reverse()
    return closed


def sum_charges(trades: list[TradeHistoryEntry]) -> dict:
    """Real per-category charge totals across all fills (not just closed round-trips —
    charges apply to every fill, opening or closing)."""
    totals = {"brokerage": 0.0, "stt": 0.0, "exchange_charges": 0.0, "gst": 0.0, "sebi_stamp_duty": 0.0}
    for t in trades:
        totals["brokerage"] += t.brokerage
        totals["stt"] += t.stt
        totals["exchange_charges"] += t.exchange_charges
        totals["gst"] += t.gst
        totals["sebi_stamp_duty"] += t.sebi_stamp_duty
    totals["total"] = sum(totals.values())
    return totals


def daily_realized_pnl(closed_trades: list[dict]) -> list[dict]:
    """Groups match_closed_trades() output by calendar date (IST) into a real
    daily P&L series, for a P&L-by-day bar chart and winning/losing day counts."""
    from collections import defaultdict as _dd
    from datetime import timedelta as _td

    by_day: dict = _dd(float)
    for c in closed_trades:
        ist_date = (c["closed_at"] + _td(hours=5, minutes=30)).date()
        by_day[ist_date] += c["pnl"]
    return [{"date": d.isoformat(), "pnl": round(pnl, 2)} for d, pnl in sorted(by_day.items())]


def classify_oi_buildup(chain: OptionChainSnapshot, previous: dict[float, dict] | None) -> list[dict]:
    """Per-strike OI buildup classification (Long/Short Buildup, Short Covering,
    Long Unwinding) from real price-change + OI-change between two chain
    snapshots. Dhan's own chain response has no OI-change field and this is
    inherently a *between-snapshots* metric — on the first call for a given
    underlying+expiry (no `previous`), every row reports "Insufficient Data"
    rather than a fabricated classification.
    """
    rows = []
    for r in chain.rows:
        prev = previous.get(r.strike) if previous else None
        if prev is None:
            rows.append(
                {
                    "strike": r.strike,
                    "ce_oi_change": 0.0, "ce_ltp_change_pct": 0.0, "ce_signal": "Insufficient Data",
                    "pe_oi_change": 0.0, "pe_ltp_change_pct": 0.0, "pe_signal": "Insufficient Data",
                }
            )
            continue

        def _signal(oi_delta: float, price_delta_pct: float) -> str:
            if oi_delta > 0 and price_delta_pct > 0:
                return "Long Buildup"
            if oi_delta > 0 and price_delta_pct < 0:
                return "Short Buildup"
            if oi_delta < 0 and price_delta_pct > 0:
                return "Short Covering"
            if oi_delta < 0 and price_delta_pct < 0:
                return "Long Unwinding"
            return "Neutral"

        ce_oi_delta = r.ce_oi - prev["ce_oi"]
        pe_oi_delta = r.pe_oi - prev["pe_oi"]
        ce_ltp_pct = ((r.ce_ltp - prev["ce_ltp"]) / prev["ce_ltp"] * 100) if prev["ce_ltp"] else 0.0
        pe_ltp_pct = ((r.pe_ltp - prev["pe_ltp"]) / prev["pe_ltp"] * 100) if prev["pe_ltp"] else 0.0
        rows.append(
            {
                "strike": r.strike,
                "ce_oi_change": ce_oi_delta, "ce_ltp_change_pct": ce_ltp_pct, "ce_signal": _signal(ce_oi_delta, ce_ltp_pct),
                "pe_oi_change": pe_oi_delta, "pe_ltp_change_pct": pe_ltp_pct, "pe_signal": _signal(pe_oi_delta, pe_ltp_pct),
            }
        )
    return rows


def portfolio_greeks_detail(positions: list[BrokerPosition], chain: OptionChainSnapshot) -> dict:
    """Per-position Greeks (matched against the option chain's per-strike Greeks)
    plus net totals. Positions that can't be matched to a chain row (wrong
    underlying/expiry, or the chain doesn't cover that strike) are skipped from
    both the rows and the totals rather than guessed at."""
    import re

    strike_re = re.compile(r"(\d+(?:\.\d+)?)(CE|PE)$")
    by_strike = {r.strike: r for r in chain.rows}
    rows = []
    net = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    for p in positions:
        m = strike_re.search(p.instrument.upper())
        if not m:
            continue
        row = by_strike.get(float(m.group(1)))
        if not row:
            continue
        is_ce = m.group(2) == "CE"
        sign = 1 if p.side == "LONG" else -1
        greeks = {
            "delta": row.ce_delta if is_ce else row.pe_delta,
            "gamma": row.ce_gamma if is_ce else row.pe_gamma,
            "theta": row.ce_theta if is_ce else row.pe_theta,
            "vega": row.ce_vega if is_ce else row.pe_vega,
            "rho": row.ce_rho if is_ce else row.pe_rho,
        }
        position_greeks = {k: sign * v * p.quantity_lots for k, v in greeks.items()}
        rows.append(
            {
                "instrument": p.instrument,
                "side": p.side,
                "quantity_lots": p.quantity_lots,
                **greeks,
                "position_delta": position_greeks["delta"],
                "position_gamma": position_greeks["gamma"],
                "position_theta": position_greeks["theta"],
                "position_vega": position_greeks["vega"],
                "position_rho": position_greeks["rho"],
            }
        )
        for k in net:
            net[k] += position_greeks[k]
    return {
        "positions": rows,
        "net_delta": net["delta"], "net_gamma": net["gamma"],
        "net_theta": net["theta"], "net_vega": net["vega"], "net_rho": net["rho"],
    }


# -- Full TPO market profile ---------------------------------------------------
#
# Market Profile proper (Steidlmayer; day types as catalogued in Dalton and in
# Ochoa 2010, Ch. 1): the session is cut into fixed time brackets, each labelled
# with a letter, and every price the market traded during a bracket gets one
# TPO (Time Price Opportunity) at that level. The resulting distribution shows
# where the market spent time — i.e. which prices it accepted.
#
# Indices carry no traded volume of their own (only their derivatives trade),
# so a TPO/time-based profile is the correct construction here rather than a
# volume profile — see volume_profile() for the traded-volume view of an
# instrument that does have real volume.

_TPO_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SESSION_START_MIN = 9 * 60 + 15   # 09:15 IST
_IB_MINUTES = 60                   # Initial Balance = first hour


def _bracket_index(ts, bracket_minutes: int) -> int:
    """Which time bracket a candle belongs to, anchored at the 09:15 open."""
    ist = ts.astimezone(_IST) if ts.tzinfo else ts
    minutes = ist.hour * 60 + ist.minute
    return max(0, (minutes - _SESSION_START_MIN) // bracket_minutes)


from datetime import timedelta, timezone as _tz

_IST = _tz(timedelta(hours=5, minutes=30))


def tpo_profile(candles: list[Candle], tick: float = 10.0, bracket_minutes: int = 30) -> dict:
    """Build the full TPO profile for one session.

    Returns the letter grid, POC, value area, Initial Balance, range
    extension, single prints and poor high/low — everything the Market
    Profile page needs, all derived from the real intraday candles passed in.
    """
    if not candles:
        raise ValueError("no candles to build a market profile from")

    ordered = sorted(candles, key=lambda c: c.ts)
    letters_by_price: dict[float, list] = defaultdict(list)
    bracket_high: dict[int, float] = {}
    bracket_low: dict[int, float] = {}
    for c in ordered:
        b = _bracket_index(c.ts, bracket_minutes)
        letter = _TPO_LETTERS[b] if b < len(_TPO_LETTERS) else _TPO_LETTERS[-1]
        bracket_high[b] = max(bracket_high.get(b, c.high), c.high)
        bracket_low[b] = min(bracket_low.get(b, c.low), c.low)
        lo = round(c.low / tick) * tick
        hi = round(c.high / tick) * tick
        if hi < lo:
            lo, hi = hi, lo
        price = lo
        while price <= hi + 1e-9:
            if letter not in letters_by_price[price]:
                letters_by_price[price].append(letter)
            price += tick

    if not letters_by_price:
        raise ValueError("profile produced no price levels")

    counts = {p: len(ls) for p, ls in letters_by_price.items()}
    poc = max(counts, key=lambda p: (counts[p], -abs(p - ordered[-1].close)))

    # Value area: expand out from the POC until ~70% of all TPOs are covered.
    prices_sorted = sorted(counts)
    val, vah = _expand_value_area(prices_sorted, counts, poc)

    # Initial Balance — the first hour, which sets the session's reference range
    session_open = ordered[0].ts.astimezone(_IST)
    ib_cut = session_open + timedelta(minutes=_IB_MINUTES)
    ib = [c for c in ordered if c.ts.astimezone(_IST) < ib_cut]
    ib_high = max((c.high for c in ib), default=ordered[0].high)
    ib_low = min((c.low for c in ib), default=ordered[0].low)

    day_high = max(c.high for c in ordered)
    day_low = min(c.low for c in ordered)

    # Single prints: a level touched in exactly one bracket, and not at the
    # profile's own edges (edge singles are just the tails of the day).
    singles = sorted(
        p for p in counts
        if counts[p] == 1 and prices_sorted[0] < p < prices_sorted[-1]
    )

    # Poor high/low: the extreme has several TPOs rather than a single-print
    # tail, meaning the move stalled rather than being rejected — these tend
    # to get revisited.
    poor_high = counts[prices_sorted[-1]] >= 2
    poor_low = counts[prices_sorted[0]] >= 2

    rows = [
        {
            "price": round(p, 2),
            "letters": "".join(letters_by_price[p]),
            "count": counts[p],
            "is_poc": p == poc,
            "in_value_area": val <= p <= vah,
            "is_single_print": p in singles,
        }
        for p in sorted(counts, reverse=True)
    ]

    return {
        "rows": rows,
        "poc": round(poc, 2), "vah": round(vah, 2), "val": round(val, 2),
        "day_high": round(day_high, 2), "day_low": round(day_low, 2),
        "ib_high": round(ib_high, 2), "ib_low": round(ib_low, 2),
        "ib_range": round(ib_high - ib_low, 2),
        "day_range": round(day_high - day_low, 2),
        "range_extension_up": round(max(0.0, day_high - ib_high), 2),
        "range_extension_down": round(max(0.0, ib_low - day_low), 2),
        "single_prints": [round(p, 2) for p in singles],
        "poor_high": poor_high, "poor_low": poor_low,
        "open_price": round(ordered[0].open, 2),
        "close_price": round(ordered[-1].close, 2),
        "bracket_minutes": bracket_minutes,
        "brackets": len({_bracket_index(c.ts, bracket_minutes) for c in ordered}),
        "tick": tick,
        # per-bracket high/low, in bracket order — used by classify_bar_structure()
        # to compare this session against the previous one (not part of the
        # page's own display, so TpoProfileOut doesn't declare it).
        "bracket_ranges": [
            {"letter": _TPO_LETTERS[b] if b < len(_TPO_LETTERS) else _TPO_LETTERS[-1],
             "high": bracket_high[b], "low": bracket_low[b]}
            for b in sorted(bracket_high)
        ],
    }


def classify_day_type(profile: dict) -> dict:
    """Name the session from its own geometry.

    The Initial Balance versus the full day's range is what separates the
    classic day types: a day that barely leaves its first hour is balanced,
    one that doubles it is trending. Extension on both sides means the market
    probed each way and rejected both, which is the Neutral day. The reasoning
    string is returned alongside the label so the classification can be
    audited rather than taken on trust.
    """
    ib_range = profile["ib_range"]
    day_range = profile["day_range"]
    ext_up = profile["range_extension_up"]
    ext_dn = profile["range_extension_down"]
    if ib_range <= 0 or day_range <= 0:
        return {"day_type": "Unclassified", "reasoning": "Initial Balance or day range is zero.", "bias": "NEUTRAL"}

    ratio = day_range / ib_range
    both_sides = ext_up > 0 and ext_dn > 0
    ext_ratio_up = ext_up / ib_range
    ext_ratio_dn = ext_dn / ib_range
    close_pos = (profile["close_price"] - profile["day_low"]) / day_range  # 0 = at low, 1 = at high

    if ratio >= 2.0 and not both_sides:
        direction = "BULLISH" if ext_up > ext_dn else "BEARISH"
        near_extreme = close_pos > 0.75 if direction == "BULLISH" else close_pos < 0.25
        label = "Trend Day" if near_extreme else "Double-Distribution Trend Day"
        why = (f"Day range is {ratio:.1f}x the Initial Balance with one-sided extension "
               f"({'up' if direction == 'BULLISH' else 'down'}), and the close sits "
               f"{close_pos * 100:.0f}% up the range.")
        return {"day_type": label, "reasoning": why, "bias": direction}

    if both_sides and ext_ratio_up > 0.15 and ext_ratio_dn > 0.15:
        return {
            "day_type": "Neutral Day",
            "reasoning": (f"Range extended both ways ({ext_up:.0f} pts up, {ext_dn:.0f} pts down) — "
                          "the market probed each side of the Initial Balance and was rejected."),
            "bias": "NEUTRAL",
        }

    if ratio <= 1.15:
        return {
            "day_type": "Normal Day",
            "reasoning": (f"Day range is only {ratio:.2f}x the Initial Balance — the first hour "
                          "contained almost the whole session."),
            "bias": "NEUTRAL",
        }

    if ratio <= 2.0:
        direction = "BULLISH" if ext_up > ext_dn else "BEARISH" if ext_dn > ext_up else "NEUTRAL"
        return {
            "day_type": "Normal Variation Day",
            "reasoning": (f"Day range is {ratio:.1f}x the Initial Balance with extension mainly "
                          f"{'higher' if direction == 'BULLISH' else 'lower' if direction == 'BEARISH' else 'balanced'} — "
                          "a single range extension beyond a contained open."),
            "bias": direction,
        }

    return {
        "day_type": "Trading Range Day",
        "reasoning": f"Day range is {ratio:.1f}x the Initial Balance with two-sided rotation and no sustained extension.",
        "bias": "NEUTRAL",
    }


def value_area_relationship(today: dict, prev: dict) -> dict:
    """How today's value area sits against the previous session's — the same
    seven-way read used for CPR, applied to the value area."""
    t_vah, t_val = today["vah"], today["val"]
    p_vah, p_val = prev["vah"], prev["val"]
    if t_val > p_vah:
        rel, bias = "HIGHER_VALUE", "BULLISH"
    elif t_vah < p_val:
        rel, bias = "LOWER_VALUE", "BEARISH"
    elif t_vah > p_vah and t_val > p_val:
        rel, bias = "OVERLAPPING_HIGHER_VALUE", "BULLISH"
    elif t_vah < p_vah and t_val < p_val:
        rel, bias = "OVERLAPPING_LOWER_VALUE", "BEARISH"
    elif t_vah >= p_vah and t_val <= p_val:
        rel, bias = "OUTSIDE_VALUE", "NEUTRAL"
    elif t_vah <= p_vah and t_val >= p_val:
        rel, bias = "INSIDE_VALUE", "NEUTRAL"
    else:
        rel, bias = "UNCHANGED_VALUE", "NEUTRAL"

    overlap_hi = min(t_vah, p_vah)
    overlap_lo = max(t_val, p_val)
    overlap = max(0.0, overlap_hi - overlap_lo)
    union = max(t_vah, p_vah) - min(t_val, p_val)
    return {
        "relationship": rel,
        "bias": bias,
        "overlap_pct": round(overlap / union * 100, 1) if union else 0.0,
        "poc_migration": round(today["poc"] - prev["poc"], 2),
    }


# -- Open type and excess tails ------------------------------------------------
#
# Two more structural reads that Market Profile draws from the same TPO data.
#
# The OPEN TYPE grades the conviction behind the session's start (Dalton's
# four openings). Where price goes relative to its own opening print in the
# first brackets says whether one side arrived with intent or the market is
# merely auctioning: an open that drives away and never trades back through
# itself is the strongest, one that oscillates around the open is the weakest.
#
# An EXCESS TAIL is a run of single TPOs at an extreme — price went there,
# found nobody, and left. That is rejection, and it marks an end the market
# has already agreed on. It is the opposite of a poor high/low, where the
# extreme has several TPOs and no rejection happened, which is why those tend
# to get revisited.


def classify_open_type(candles: list[Candle], profile: dict, bracket_minutes: int = 30) -> dict:
    """Grade the session's opening conviction from real early-bracket action."""
    if not candles:
        return {"open_type": "Unclassified", "open_reasoning": "No candles."}
    ordered = sorted(candles, key=lambda c: c.ts)
    open_px = ordered[0].open
    day_range = profile["day_range"]
    if day_range <= 0:
        return {"open_type": "Unclassified", "open_reasoning": "Zero day range."}

    # first two brackets define the "opening" for this purpose
    first = [c for c in ordered if _bracket_index(c.ts, bracket_minutes) < 2]
    if not first:
        first = ordered[:4]
    hi = max(c.high for c in first)
    lo = min(c.low for c in first)
    up_from_open = hi - open_px
    down_from_open = open_px - lo
    # did price trade back through its own open after the first bracket?
    later = [c for c in ordered if _bracket_index(c.ts, bracket_minutes) >= 1]
    crossed_back = any(c.low <= open_px <= c.high for c in later)
    drift = profile["close_price"] - open_px
    one_sided = min(up_from_open, down_from_open) < day_range * 0.12

    if one_sided and not crossed_back and abs(drift) > day_range * 0.35:
        return {
            "open_type": "Open-Drive",
            "open_reasoning": (
                f"Price left the open ({open_px:.0f}) decisively "
                f"{'higher' if drift > 0 else 'lower'} and never traded back through it — "
                "strongest opening conviction."
            ),
        }
    if crossed_back and abs(drift) > day_range * 0.30:
        probed_up = up_from_open > down_from_open
        drove_up = drift > 0
        if probed_up != drove_up:
            return {
                "open_type": "Open-Test-Drive",
                "open_reasoning": (
                    f"The open probed {'higher' if probed_up else 'lower'}, failed, then drove "
                    f"{'higher' if drove_up else 'lower'} for the session — the initial probe was a test."
                ),
            }
        return {
            "open_type": "Open-Rejection-Reverse",
            "open_reasoning": (
                f"Price moved {'up' if probed_up else 'down'} off the open, was rejected, and "
                "reversed back through the opening print."
            ),
        }
    return {
        "open_type": "Open-Auction",
        "open_reasoning": (
            f"Price rotated around the opening print ({open_px:.0f}) without one side taking "
            "control — low opening conviction, balanced start."
        ),
    }


def find_excess_tails(profile: dict) -> dict:
    """Runs of single TPOs at either extreme — real rejection, not a poor end."""
    rows = profile["rows"]  # already sorted high -> low
    if len(rows) < 3:
        return {"selling_tail": [], "buying_tail": []}

    selling = []
    for r in rows:                       # from the high downward
        if r["count"] == 1:
            selling.append(r["price"])
        else:
            break
    buying = []
    for r in reversed(rows):             # from the low upward
        if r["count"] == 1:
            buying.append(r["price"])
        else:
            break
    # a single lone TPO is noise; a tail needs at least two stacked
    return {
        "selling_tail": sorted(selling, reverse=True) if len(selling) >= 2 else [],
        "buying_tail": sorted(buying) if len(buying) >= 2 else [],
    }


def find_virgin_pocs(session_profiles: list, current_price: float, max_age: int = 20) -> list:
    """Prior-session POCs that price has not traded back to since.

    The book treats an untested point of control as a magnet — the fairest
    price of that session, left unvisited — and notes such levels tend to be
    revisited [Ochoa 2010, Ch. 4, ~pp.116-121]. `session_profiles` is
    oldest-first, each {"date": date, "poc": float, "high": float, "low": float}.
    A POC stays virgin until a LATER session's range covers it.
    """
    virgins = []
    for i, s in enumerate(session_profiles[:-1]):   # the newest session cannot be tested yet
        poc = s["poc"]
        tested = False
        age = 0
        for later in session_profiles[i + 1:]:
            age += 1
            if later["low"] <= poc <= later["high"]:
                tested = True
                break
        if not tested and age <= max_age:
            virgins.append({
                "date": str(s["date"]),
                "poc": round(poc, 2),
                "sessions_ago": len(session_profiles) - 1 - i,
                "distance": round(poc - current_price, 2),
                "above": poc > current_price,
            })
    virgins.sort(key=lambda v: abs(v["distance"]))
    return virgins


def classify_bar_structure(prev: dict, today: dict) -> str:
    """Best-effort reconstruction of the Inside/Outside-Bar + IB-break
    annotations professional Market Profile tools print above each session
    (e.g. "OneTime framing - Down (3) - Broken (in A)"). There's no public
    spec for this notation — this is a disclosed heuristic, not a faithful
    reproduction of any specific vendor's exact algorithm — built from the
    same `tpo_profile()` output (day_high/day_low/bracket_ranges) used
    elsewhere on this page:
      - Inside Bar: today's full range sits inside yesterday's range.
      - Outside Bar: today's range engulfs yesterday's range.
      - Otherwise "OneTime framing - Up/Down (N)": a one-directional range
        extension, N = number of TPO brackets that printed beyond today's own
        Initial Balance on the extending side.
      - "Broken (in X)" / "Broken BothSides (in X)": X = the first bracket
        letter whose own high/low took out yesterday's high, yesterday's
        low, or both.
    """
    prev_high, prev_low = prev["day_high"], prev["day_low"]
    day_high, day_low = today["day_high"], today["day_low"]

    if day_high <= prev_high and day_low >= prev_low:
        bar_type = "Inside Bar (" + ("U" if today["close_price"] >= today["open_price"] else "D") + ")"
    elif day_high > prev_high and day_low < prev_low:
        bar_type = "Outside Bar"
    else:
        ext_up, ext_down = today["range_extension_up"], today["range_extension_down"]
        if ext_up >= ext_down:
            n = sum(1 for b in today["bracket_ranges"] if b["high"] > today["ib_high"])
            bar_type = "OneTime framing - Up (%d)" % max(n, 1)
        else:
            n = sum(1 for b in today["bracket_ranges"] if b["low"] < today["ib_low"])
            bar_type = "OneTime framing - Down (%d)" % max(n, 1)

    broken_high = broken_low = None
    for b in today["bracket_ranges"]:
        if broken_high is None and b["high"] > prev_high:
            broken_high = b["letter"]
        if broken_low is None and b["low"] < prev_low:
            broken_low = b["letter"]
        if broken_high is not None and broken_low is not None:
            break

    if broken_high and broken_low:
        broken = "Broken BothSides (in %s)" % (broken_high if broken_high == broken_low else min(broken_high, broken_low))
    elif broken_high:
        broken = "Broken (in %s)" % broken_high
    elif broken_low:
        broken = "Broken (in %s)" % broken_low
    else:
        broken = None

    return bar_type + (" - " + broken if broken else "")
