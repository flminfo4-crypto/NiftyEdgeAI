"""
Shared real-data calculations used across several pages/endpoints, so each
one doesn't reimplement the same math against broker_plugins types.

Nothing here talks to a broker directly — callers pass in candles/positions/
option-chain snapshots already fetched via app.services.market_data / broker.
"""

from collections import defaultdict

from broker_plugins.core.interface import BrokerPosition, Candle, OptionChainSnapshot, TradeHistoryEntry


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
    total = sum(weight_by_price.values())
    target = total * 0.68
    by_weight_desc = sorted(weight_by_price, key=lambda p: -weight_by_price[p])
    included = []
    acc = 0.0
    for p in by_weight_desc:
        included.append(p)
        acc += weight_by_price[p]
        if acc >= target:
            break
    return max(included), min(included), poc


def _period_letter(idx: int) -> str:
    return chr(65 + idx) if idx < 26 else chr(65 + (idx % 26))


def _classify_day_type(ib_range: float, day_range: float, ext_up: float, ext_down: float) -> str:
    """Simplified reading of the standard Dalton (Mind Over Markets) day-type
    taxonomy, driven off real IB/range-extension numbers rather than the
    tick-by-tick rotation detail a full classifier would need."""
    if ib_range <= 0:
        return "Insufficient Data"
    if ext_up > 0 and ext_down > 0:
        return "Neutral Day"
    ratio = day_range / ib_range
    if ratio <= 1.15:
        return "Normal Day"
    if ratio <= 2.0:
        return "Normal Variation Day"
    return "Trend Day"


def _classify_open_type(period0_candles: list[Candle], open_price: float) -> str:
    """Simplified reading of Dalton's four open types (Open-Drive/Test-Drive/
    Rejection-Reverse/Auction), using only the first TPO period's real
    high/low relative to the real session open — not full tick-by-tick
    order-flow, which isn't available from this data source."""
    if not period0_candles:
        return "Open-Auction"
    period0_high = max(c.high for c in period0_candles)
    period0_low = min(c.low for c in period0_candles)
    upper_room = period0_high - open_price
    lower_room = open_price - period0_low
    tol = 3.0
    if lower_room <= tol and upper_room > tol:
        return "Open-Drive (Bullish)"
    if upper_room <= tol and lower_room > tol:
        return "Open-Drive (Bearish)"
    if upper_room > tol and lower_room > tol:
        return "Open-Auction"
    return "Open-Test-Drive"


def market_profile_detail(candles: list[Candle], session_start, tick: float = 5.0, period_minutes: int = 30) -> dict:
    """Full TPO (Time Price Opportunity) profile: real per-price letters
    binned into `period_minutes`-wide brackets from `session_start`, plus the
    derived Initial Balance, day/open type, poor high/low and excess-tail
    reads that market-profile.html shows. Unlike `market_profile()` above
    (which only returns vah/val/poc), this drives the letter grid itself so
    nothing on the page is left as static placeholder content.
    """
    if not candles:
        raise ValueError("no candles to build a market profile from")
    sorted_candles = sorted(candles, key=lambda c: c.ts)
    period_seconds = period_minutes * 60

    letters_by_price: dict[float, set] = defaultdict(set)
    period_high: dict[int, float] = {}
    period_low: dict[int, float] = {}
    period0_candles: list[Candle] = []
    for c in sorted_candles:
        idx = max(0, int((c.ts - session_start).total_seconds() // period_seconds))
        period_high[idx] = max(period_high.get(idx, c.high), c.high)
        period_low[idx] = min(period_low.get(idx, c.low), c.low)
        if idx == 0:
            period0_candles.append(c)
        letter = _period_letter(idx)
        lo = round(c.low / tick) * tick
        hi = round(c.high / tick) * tick
        if hi <= lo:
            hi = lo + tick
        levels = int(round((hi - lo) / tick)) + 1
        price = lo
        for _ in range(levels):
            letters_by_price[price].add(letter)
            price += tick

    tpo_count = {p: len(ls) for p, ls in letters_by_price.items()}
    poc = max(tpo_count, key=tpo_count.get)
    total = sum(tpo_count.values())
    target = total * 0.68
    by_count_desc = sorted(tpo_count, key=lambda p: -tpo_count[p])
    included, acc = [], 0.0
    for p in by_count_desc:
        included.append(p)
        acc += tpo_count[p]
        if acc >= target:
            break
    vah, val = max(included), min(included)

    rows = [
        {"price": p, "letters": "".join(sorted(letters_by_price[p])), "tpo_count": tpo_count[p]}
        for p in sorted(tpo_count)
    ]

    top_price, bottom_price = max(tpo_count), min(tpo_count)
    ib_periods = [idx for idx in (0, 1) if idx in period_high]
    ib_high = max(period_high[idx] for idx in ib_periods) if ib_periods else top_price
    ib_low = min(period_low[idx] for idx in ib_periods) if ib_periods else bottom_price
    ib_range = ib_high - ib_low

    day_high = max(c.high for c in candles)
    day_low = min(c.low for c in candles)
    day_range = day_high - day_low
    ext_up = max(0.0, day_high - ib_high)
    ext_down = max(0.0, ib_low - day_low)

    # Per-period high/low, in period order — used by classify_bar_structure()
    # to compare this session against the previous one (which period broke
    # the prior day's high/low, whether it broke one side or both, etc).
    period_ranges = [
        {"letter": _period_letter(idx), "high": period_high[idx], "low": period_low[idx]}
        for idx in sorted(period_high)
    ]

    return {
        "rows": rows,
        "vah": vah, "val": val, "poc": poc,
        "session_high": day_high, "session_low": day_low,
        "ib_high": ib_high, "ib_low": ib_low, "ib_range": ib_range,
        "range_extension_up": ext_up, "range_extension_down": ext_down,
        "day_type": _classify_day_type(ib_range, day_range, ext_up, ext_down),
        "open_type": _classify_open_type(period0_candles, sorted_candles[0].open),
        # A level with only one TPO letter is a "single print" — at the
        # session extreme that means a poorly-tested, quickly-rejected tail
        # (excess); 2+ letters at the extreme means the auction kept
        # returning there without resolving it (a "poor" high/low).
        "poor_high": top_price if tpo_count[top_price] >= 2 else None,
        "poor_low": bottom_price if tpo_count[bottom_price] >= 2 else None,
        "excess_high": top_price if tpo_count[top_price] == 1 else None,
        "excess_low": bottom_price if tpo_count[bottom_price] == 1 else None,
        "period_ranges": period_ranges,
        "open_price": sorted_candles[0].open,
        "close_price": sorted_candles[-1].close,
    }


def classify_bar_structure(prev: dict, today: dict) -> str:
    """Best-effort reconstruction of the Inside/Outside-Bar + IB-break
    annotations professional Market Profile tools print above each session
    (e.g. "OneTime framing - Down (3) - Broken (in A)"). There's no public
    spec for this notation — this is a disclosed heuristic, not a faithful
    reproduction of any specific vendor's exact algorithm:
      - Inside Bar: today's full range sits inside yesterday's range.
      - Outside Bar: today's range engulfs yesterday's range.
      - Otherwise "OneTime framing - Up/Down (N)": a one-directional range
        extension, N = number of TPO periods that printed beyond today's own
        Initial Balance on the extending side.
      - "Broken (in X)" / "Broken BothSides (in X)": X = the first period
        letter whose own high/low took out yesterday's high, yesterday's
        low, or both.
    """
    prev_high, prev_low = prev["session_high"], prev["session_low"]
    day_high, day_low = today["session_high"], today["session_low"]

    if day_high <= prev_high and day_low >= prev_low:
        bar_type = "Inside Bar (" + ("U" if today["close_price"] >= today["open_price"] else "D") + ")"
    elif day_high > prev_high and day_low < prev_low:
        bar_type = "Outside Bar"
    else:
        ext_up, ext_down = today["range_extension_up"], today["range_extension_down"]
        if ext_up >= ext_down:
            n = sum(1 for p in today["period_ranges"] if p["high"] > today["ib_high"])
            bar_type = "OneTime framing - Up (%d)" % max(n, 1)
        else:
            n = sum(1 for p in today["period_ranges"] if p["low"] < today["ib_low"])
            bar_type = "OneTime framing - Down (%d)" % max(n, 1)

    broken_high = broken_low = None
    for p in today["period_ranges"]:
        if broken_high is None and p["high"] > prev_high:
            broken_high = p["letter"]
        if broken_low is None and p["low"] < prev_low:
            broken_low = p["letter"]
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
    target = total * 0.68
    by_volume_desc = sorted(volume_by_price, key=lambda p: -volume_by_price[p])
    included, acc = [], 0.0
    for p in by_volume_desc:
        included.append(p)
        acc += volume_by_price[p]
        if acc >= target:
            break

    rows = [{"price": p, "volume": v} for p, v in sorted(volume_by_price.items())]
    return {"rows": rows, "vah": max(included), "val": min(included), "poc": poc, "total_volume": total}


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
