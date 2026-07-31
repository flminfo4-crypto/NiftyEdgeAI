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
