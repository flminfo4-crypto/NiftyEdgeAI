"""
Pivot calculations: Floor Pivots, Central Pivot Range (CPR), and the
Camarilla Equation, plus the classification rules built on them
(CPR width regime, seven two-day CPR relationships).

Methodology follows *Secrets of a Pivot Boss* (F. Ochoa, 2010) — see
docs/PivotBoss-Roadmap.md for the full mapping and citation/licensing
conventions. Formulas themselves are public-domain trading math
(floor pivots: L. Williams 1979; pivot range: M. Fisher, "The Logical
Trader"; Camarilla: N. Stott 1989).

Everything here is a pure function of prior-session OHLC — deterministic,
shared by the live path (backend /market/cpr) and the backtester, so the
same day classifies identically in both. [Ochoa 2010, Ch. 5-7]
"""

from dataclasses import dataclass, field
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Floor Pivots — expanded formula [Ochoa 2010, Ch. 5, ~p.136 (standard),
# ~p.138 (expanded: adds R4/S4 and the pivot range TC/BC)]
# ---------------------------------------------------------------------------


@dataclass
class FloorPivots:
    pivot: float
    r1: float
    r2: float
    r3: float
    r4: float
    s1: float
    s2: float
    s3: float
    s4: float


def floor_pivots(high: float, low: float, close: float) -> FloorPivots:
    pivot = (high + low + close) / 3
    rng = high - low
    r1 = 2 * pivot - low
    r2 = pivot + rng
    r3 = r1 + rng
    s1 = 2 * pivot - high
    s2 = pivot - rng
    s3 = s1 - rng
    r4 = r3 + (r2 - r1)
    s4 = s3 - (s1 - s2)
    return FloorPivots(
        pivot=round(pivot, 2), r1=round(r1, 2), r2=round(r2, 2), r3=round(r3, 2), r4=round(r4, 2),
        s1=round(s1, 2), s2=round(s2, 2), s3=round(s3, 2), s4=round(s4, 2),
    )


# ---------------------------------------------------------------------------
# Central Pivot Range [Ochoa 2010, Ch. 6, ~p.164]
#   TC = (Pivot - BC) + Pivot ; Pivot = (H+L+C)/3 ; BC = (H+L)/2
# The formula can produce TC below BC depending on the prior session's
# shape; per the book, the *higher* value is always labeled TC.
# ---------------------------------------------------------------------------


@dataclass
class CPR:
    tc: float          # top central (always the higher of the two)
    pivot: float
    bc: float          # bottom central (always the lower)
    width: float        # tc - bc, absolute
    width_pct: float     # width as % of close (basis for regime classification)


def cpr(high: float, low: float, close: float) -> CPR:
    pivot = (high + low + close) / 3
    bc_raw = (high + low) / 2
    tc_raw = (pivot - bc_raw) + pivot
    tc, bc = (tc_raw, bc_raw) if tc_raw >= bc_raw else (bc_raw, tc_raw)
    width = tc - bc
    width_pct = (width / close * 100) if close else 0.0
    return CPR(
        tc=round(tc, 2), pivot=round(pivot, 2), bc=round(bc, 2),
        width=round(width, 2), width_pct=round(width_pct, 4),
    )


# ---------------------------------------------------------------------------
# CPR width regime → day-type forecast [Ochoa 2010, Ch. 6, ~p.184-185]
# Narrow CPR (quiet prior day) → trend / double-distribution day more likely.
# Wide CPR (wide-range prior day) → typical / trading-range / sideways day.
# Thresholds are OUR calibration knobs (the book describes the relationship
# qualitatively); defaults chosen for index-level % widths, revisit after
# backtesting on NIFTY data (Roadmap §5, Phase 3).
# ---------------------------------------------------------------------------

WidthRegime = Literal["NARROW", "NORMAL", "WIDE"]

NARROW_WIDTH_PCT = 0.20   # CPR width < 0.20% of price → narrow
WIDE_WIDTH_PCT = 0.55     # CPR width > 0.55% of price → wide


@dataclass
class WidthForecast:
    regime: WidthRegime
    forecast: str


def classify_width(c: CPR, narrow_pct: float = NARROW_WIDTH_PCT, wide_pct: float = WIDE_WIDTH_PCT) -> WidthForecast:
    if c.width_pct < narrow_pct:
        return WidthForecast(
            regime="NARROW",
            forecast="Trending session likely (Trend / Double-Distribution / Extended Typical day)",
        )
    if c.width_pct > wide_pct:
        return WidthForecast(
            regime="WIDE",
            forecast="Ranging session likely (Typical / Trading-Range / Sideways day)",
        )
    return WidthForecast(regime="NORMAL", forecast="No strong day-type edge from CPR width")


# ---------------------------------------------------------------------------
# Percentile-based width regime — an alternative to classify_width()'s fixed
# calibration knobs. Ranks today's real CPR width against a real trailing
# window of the underlying's own recent CPR widths (bottom 20th percentile =
# narrow, top 30th percentile = wide), so the classification adapts to the
# underlying's current volatility regime instead of a static % threshold.
# Requires real history — see pivot_service._trailing_width_history for how
# the caller builds `trailing_width_pcts` from real daily candles.
# ---------------------------------------------------------------------------

NARROW_PERCENTILE = 0.20
WIDE_PERCENTILE = 0.70


@dataclass
class PercentileWidthForecast:
    regime: WidthRegime
    percentile_rank: float
    p20_threshold: float
    p70_threshold: float


def classify_width_percentile(
    current_width_pct: float, trailing_width_pcts: list[float],
    narrow_percentile: float = NARROW_PERCENTILE, wide_percentile: float = WIDE_PERCENTILE,
) -> Optional[PercentileWidthForecast]:
    if not trailing_width_pcts:
        return None
    sorted_widths = sorted(trailing_width_pcts)
    n = len(sorted_widths)
    below = sum(1 for w in sorted_widths if w < current_width_pct)
    percentile_rank = below / n * 100
    p20 = sorted_widths[min(int(n * narrow_percentile), n - 1)]
    p70 = sorted_widths[min(int(n * wide_percentile), n - 1)]
    if current_width_pct <= p20:
        regime: WidthRegime = "NARROW"
    elif current_width_pct >= p70:
        regime = "WIDE"
    else:
        regime = "NORMAL"
    return PercentileWidthForecast(
        regime=regime, percentile_rank=round(percentile_rank, 1),
        p20_threshold=round(p20, 4), p70_threshold=round(p70, 4),
    )


# ---------------------------------------------------------------------------
# Two-day CPR relationships [Ochoa 2010, Ch. 6, ~p.168; bias table analogous
# to the value-area relationships of Ch. 4]. Seven classifications, each with
# a directional bias used as an input factor by bias.py.
# ---------------------------------------------------------------------------

Relationship = Literal[
    "HIGHER_VALUE", "OVERLAPPING_HIGHER_VALUE", "LOWER_VALUE",
    "OVERLAPPING_LOWER_VALUE", "UNCHANGED_VALUE", "OUTSIDE_VALUE", "INSIDE_VALUE",
]

_RELATIONSHIP_BIAS = {
    "HIGHER_VALUE": ("BULLISH", "Bullish"),
    "OVERLAPPING_HIGHER_VALUE": ("BULLISH", "Moderately Bullish"),
    "LOWER_VALUE": ("BEARISH", "Bearish"),
    "OVERLAPPING_LOWER_VALUE": ("BEARISH", "Moderately Bearish"),
    "UNCHANGED_VALUE": ("NEUTRAL", "Sideways / Breakout watch"),
    "OUTSIDE_VALUE": ("NEUTRAL", "Sideways"),
    "INSIDE_VALUE": ("NEUTRAL", "Breakout watch"),
}

# tolerance (as fraction of yesterday's width) within which the two ranges
# count as "virtually unchanged" [Ochoa 2010, Ch. 6, ~p.178]
_UNCHANGED_TOL = 0.15


@dataclass
class TwoDayRelationship:
    relationship: Relationship
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    label: str
    description: str


def classify_two_day(today: CPR, yesterday: CPR) -> TwoDayRelationship:
    tol = max(yesterday.width, 1e-9) * _UNCHANGED_TOL
    if abs(today.tc - yesterday.tc) <= tol and abs(today.bc - yesterday.bc) <= tol:
        rel: Relationship = "UNCHANGED_VALUE"
    elif today.bc > yesterday.tc:
        rel = "HIGHER_VALUE"
    elif today.tc < yesterday.bc:
        rel = "LOWER_VALUE"
    elif today.tc >= yesterday.tc and today.bc >= yesterday.bc:
        rel = "OVERLAPPING_HIGHER_VALUE"
    elif today.tc <= yesterday.tc and today.bc <= yesterday.bc:
        rel = "OVERLAPPING_LOWER_VALUE"
    elif today.tc > yesterday.tc and today.bc < yesterday.bc:
        rel = "OUTSIDE_VALUE"
    else:  # today entirely inside yesterday
        rel = "INSIDE_VALUE"

    direction, label = _RELATIONSHIP_BIAS[rel]
    descriptions = {
        "HIGHER_VALUE": "Today's CPR sits completely above yesterday's — most bullish two-day combination; buy pullbacks to the range.",
        "OVERLAPPING_HIGHER_VALUE": "Today's CPR overlaps but is shifted higher — moderately bullish.",
        "LOWER_VALUE": "Today's CPR sits completely below yesterday's — most bearish combination; sell rallies into the range.",
        "OVERLAPPING_LOWER_VALUE": "Today's CPR overlaps but is shifted lower — moderately bearish.",
        "UNCHANGED_VALUE": "CPR virtually unchanged — quiet range continuation, OR a breakout if the open prints beyond yesterday's range.",
        "OUTSIDE_VALUE": "Today's CPR engulfs yesterday's — balance; expect sideways trade.",
        "INSIDE_VALUE": "Today's CPR is inside yesterday's — compression; watch for a breakout.",
    }
    return TwoDayRelationship(relationship=rel, direction=direction, label=label, description=descriptions[rel])


# ---------------------------------------------------------------------------
# Camarilla Equation — standard formula [Ochoa 2010, Ch. 7, ~p.218]
#   Hn/Ln = Close ± RANGE * 1.1/{12, 6, 4, 2}
# Call to action per the book: H3/L3 are reversal (fade) levels; H4/L4 are
# breakout (go-with) levels. [Ochoa 2010, Ch. 7, ~p.219]
# ---------------------------------------------------------------------------


@dataclass
class Camarilla:
    h4: float
    h3: float
    h2: float
    h1: float
    l1: float
    l2: float
    l3: float
    l4: float


def camarilla(high: float, low: float, close: float) -> Camarilla:
    rng = high - low
    def lvl(mult: float, sign: int) -> float:
        return round(close + sign * rng * 1.1 / mult, 2)
    return Camarilla(
        h4=lvl(2, +1), h3=lvl(4, +1), h2=lvl(6, +1), h1=lvl(12, +1),
        l1=lvl(12, -1), l2=lvl(6, -1), l3=lvl(4, -1), l4=lvl(2, -1),
    )


# ---------------------------------------------------------------------------
# Convenience bundle for the /market/cpr endpoint and the pre-market report.
# ---------------------------------------------------------------------------


@dataclass
class DailyLevels:
    floor: FloorPivots
    cpr_today: CPR
    camarilla: Camarilla
    width: WidthForecast
    two_day: Optional[TwoDayRelationship] = None
    pdh: float = 0.0   # prior day high
    pdl: float = 0.0   # prior day low
    pdc: float = 0.0   # prior day close


def daily_levels(
    high: float,
    low: float,
    close: float,
    prev_high: Optional[float] = None,
    prev_low: Optional[float] = None,
    prev_close: Optional[float] = None,
) -> DailyLevels:
    """Compute all levels for the *upcoming* session from the prior session's
    OHLC (high/low/close). If the session before that is also given, the
    two-day CPR relationship is classified as well."""
    today_cpr = cpr(high, low, close)
    two_day = None
    if None not in (prev_high, prev_low, prev_close):
        yesterday_cpr = cpr(prev_high, prev_low, prev_close)
        two_day = classify_two_day(today_cpr, yesterday_cpr)
    return DailyLevels(
        floor=floor_pivots(high, low, close),
        cpr_today=today_cpr,
        camarilla=camarilla(high, low, close),
        width=classify_width(today_cpr),
        two_day=two_day,
        pdh=high, pdl=low, pdc=close,
    )


# ---------------------------------------------------------------------------
# Opening-print confirmation [Ochoa 2010, Ch. 6, ~pp.168-176]
#
# The book is explicit that a two-day relationship supplies only an INITIAL
# bias, and that the bias must be confirmed by the opening print: the seven
# relationships depend on two prices, the prior day's close and the current
# day's open. A bullish relationship whose open prints below the range is
# REJECTED, and the range then becomes a fade (short) opportunity rather than
# a place to buy pullbacks — the book notes some of the biggest rallies come
# from a bearish relationship being rejected the same way. Treating the
# relationship alone as the verdict, without this check, inverts the plan on
# exactly the days it matters most.
# ---------------------------------------------------------------------------

ConfirmationStatus = Literal["CONFIRMED", "REJECTED", "PENDING"]

_BULLISH_RELS = ("HIGHER_VALUE", "OVERLAPPING_HIGHER_VALUE")
_BEARISH_RELS = ("LOWER_VALUE", "OVERLAPPING_LOWER_VALUE")


@dataclass
class BiasConfirmation:
    status: ConfirmationStatus
    initial_direction: str                # from the two-day relationship alone
    effective_direction: str              # after the opening print is applied
    strong: bool                          # open beyond the far edge (highest conviction)
    prior_close_supports: Optional[bool]  # prior close outside the range in the bias direction
    guidance: str


def confirm_two_day_bias(
    two_day: Optional[TwoDayRelationship],
    today_cpr: CPR,
    open_price: Optional[float],
    prior_close: Optional[float] = None,
) -> Optional[BiasConfirmation]:
    """Apply the day's opening print to a two-day relationship's initial bias.

    Returns None when there is no relationship to judge, and PENDING when the
    session has not opened yet, since the confirmation genuinely is not
    knowable until the open prints.
    """
    if two_day is None:
        return None
    initial = two_day.direction
    tc, bc = today_cpr.tc, today_cpr.bc

    prior_supports: Optional[bool] = None
    if prior_close is not None:
        if initial == "BULLISH":
            prior_supports = prior_close > tc
        elif initial == "BEARISH":
            prior_supports = prior_close < bc

    if open_price is None:
        return BiasConfirmation(
            status="PENDING", initial_direction=initial, effective_direction=initial,
            strong=False, prior_close_supports=prior_supports,
            guidance=(
                f"{two_day.label} bias is provisional — it is confirmed or rejected "
                f"by where the session opens relative to {bc:.2f}-{tc:.2f}."
            ),
        )

    if two_day.relationship in _BULLISH_RELS:
        if open_price >= bc:
            strong = open_price > tc
            return BiasConfirmation(
                status="CONFIRMED", initial_direction=initial, effective_direction="BULLISH",
                strong=strong, prior_close_supports=prior_supports,
                guidance=(
                    "Open above the CPR confirms the bullish bias — buy pullbacks into the range."
                    if strong else
                    "Open inside the CPR but above its floor keeps the bullish bias — buy pullbacks to the range."
                ),
            )
        return BiasConfirmation(
            status="REJECTED", initial_direction=initial, effective_direction="BEARISH",
            strong=False, prior_close_supports=prior_supports,
            guidance=(
                f"Open below the CPR floor ({bc:.2f}) rejects the bullish bias — "
                "the range becomes resistance to fade, not support to buy."
            ),
        )

    if two_day.relationship in _BEARISH_RELS:
        if open_price <= tc:
            strong = open_price < bc
            return BiasConfirmation(
                status="CONFIRMED", initial_direction=initial, effective_direction="BEARISH",
                strong=strong, prior_close_supports=prior_supports,
                guidance=(
                    "Open below the CPR confirms the bearish bias — sell rallies into the range."
                    if strong else
                    "Open inside the CPR but below its top keeps the bearish bias — sell rallies to the range."
                ),
            )
        return BiasConfirmation(
            status="REJECTED", initial_direction=initial, effective_direction="BULLISH",
            strong=False, prior_close_supports=prior_supports,
            guidance=(
                f"Open above the CPR top ({tc:.2f}) rejects the bearish bias — sentiment "
                "shifted overnight; the sharpest rallies come from exactly this rejection."
            ),
        )

    # Neutral relationships (unchanged / outside / inside): the open decides
    # direction outright rather than confirming a pre-existing lean.
    if open_price > tc:
        eff, note = "BULLISH", "above"
    elif open_price < bc:
        eff, note = "BEARISH", "below"
    else:
        return BiasConfirmation(
            status="PENDING", initial_direction=initial, effective_direction="NEUTRAL",
            strong=False, prior_close_supports=prior_supports,
            guidance=(
                f"Neutral relationship and the open sits inside the CPR ({bc:.2f}-{tc:.2f}) — "
                "wait for a break of the range to pick a side."
            ),
        )
    return BiasConfirmation(
        status="CONFIRMED", initial_direction=initial, effective_direction=eff,
        strong=True, prior_close_supports=prior_supports,
        guidance=f"Neutral relationship, but the open printed {note} the CPR — trade in the direction of the break.",
    )


# ---------------------------------------------------------------------------
# Pivot Trend Analysis [Ochoa 2010, Ch. 5, ~pp.151-158]
#
# Per the book the market remains strictly above S1 in a bullish trend and
# below R1 in a bearish trend, and the trend persists until price CLOSES
# beyond that level, which flips the state. In an uptrend the tradeable zones
# are S1 and the CPR (buy pullbacks, target R1/R2); in a downtrend they are
# R1 and the CPR (sell rallies, target S1/S2).
# ---------------------------------------------------------------------------

TrendState = Literal["BULLISH", "BEARISH", "NEUTRAL"]


@dataclass
class PivotTrend:
    state: TrendState
    days_in_state: int
    flip_level: Optional[float]   # a close beyond this flips the trend
    buy_zones: list
    sell_zones: list
    targets: list
    guidance: str


def pivot_trend_state(sessions: list) -> Optional[PivotTrend]:
    """Walk real sessions to derive the current pivot trend.

    `sessions` is oldest-first; each entry is a dict holding that day's close
    plus the S1/R1 that were in force for it (i.e. computed from the day
    before): {"close": float, "s1": float, "r1": float}. Needs at least two.
    """
    if not sessions or len(sessions) < 2:
        return None
    state: TrendState = "NEUTRAL"
    days = 0
    for s in sessions:
        close, s1, r1 = s["close"], s["s1"], s["r1"]
        if state == "BULLISH":
            new_state: TrendState = "BEARISH" if close < s1 else "BULLISH"
        elif state == "BEARISH":
            new_state = "BULLISH" if close > r1 else "BEARISH"
        else:
            new_state = "BULLISH" if close > r1 else "BEARISH" if close < s1 else "NEUTRAL"
        days = days + 1 if new_state == state else 1
        state = new_state

    last = sessions[-1]
    if state == "BULLISH":
        return PivotTrend(
            state=state, days_in_state=days, flip_level=round(last["s1"], 2),
            buy_zones=["S1", "CPR"], sell_zones=[], targets=["R1", "R2"],
            guidance=(
                f"Uptrend intact while price closes above S1 ({last['s1']:.2f}). "
                "Buy pullbacks to S1 or the CPR; target R1/R2. A close below S1 flips the trend."
            ),
        )
    if state == "BEARISH":
        return PivotTrend(
            state=state, days_in_state=days, flip_level=round(last["r1"], 2),
            buy_zones=[], sell_zones=["R1", "CPR"], targets=["S1", "S2"],
            guidance=(
                f"Downtrend intact while price closes below R1 ({last['r1']:.2f}). "
                "Sell rallies to R1 or the CPR; target S1/S2. A close above R1 flips the trend."
            ),
        )
    return PivotTrend(
        state=state, days_in_state=days, flip_level=None,
        buy_zones=[], sell_zones=[], targets=[],
        guidance="No established pivot trend — price is closing between S1 and R1. Wait for a close beyond either to set the trend.",
    )
