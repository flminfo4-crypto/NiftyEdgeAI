"""
Market Bias engine — powers the Dashboard's "Market Bias (AI)" card.

Deliberately a transparent, hand-tuned weighted-scoring rules engine rather
than a trained classifier (see ai-engine/README.md "Open questions"): every
factor that goes into the score is also shown to the user in the "Why?" list,
which a black-box classifier couldn't support as directly. This can be swapped
for a calibrated model later behind the same compute_bias() signature.

Factors (weights sum to 1.0):
  price vs value area   0.25   (Market Profile: is spot above/below VAH/VAL?)
  order flow / delta    0.20   (Footprint: cumulative buy/sell delta)
  OI buildup            0.15   (Open Interest: CE vs PE writing dominance)
  two-day CPR rel.      0.15   (Pivot Boss Ch. 6: CPR shift vs prior session)
  portfolio delta       0.15   (Greeks: net directional exposure)
  portfolio gamma       0.10   (Greeks: net convexity exposure)

score = sum(weight * sign) where sign is -1 (bearish) / 0 (neutral) / +1 (bullish)
confidence = 50 + 22 * abs(score)   -> ranges 50% (all neutral) to 72% (unanimous)
             capped at 95% to leave room for a future model to exceed the rules engine.
"""

from dataclasses import dataclass, field
from typing import Literal

from .features import Direction, compute_features

_WEIGHTS = {
    "price_vs_value": 0.25,
    "order_flow": 0.20,
    "oi_trend": 0.15,
    "cpr_two_day": 0.15,
    "delta": 0.15,
    "gamma": 0.10,
}

_SIGN = {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0}


@dataclass
class BiasFactor:
    key: str
    label: str
    value: str
    direction: Direction


@dataclass
class BiasResult:
    direction: Direction
    headline: str          # "BEARISH BELOW VAH 23,601"
    subtext: str            # "CE WRITING AT 23,600"
    confidence_pct: int      # 0-95
    score: float              # raw weighted score, -1.0 .. 1.0
    factors: list[BiasFactor] = field(default_factory=list)


def compute_bias(**feature_kwargs) -> BiasResult:
    f = compute_features(**feature_kwargs)
    pv, of, oi, gk = f["price_vs_value"], f["order_flow"], f["oi_trend"], f["greeks"]
    td = f["cpr_two_day"]

    score = (
        _WEIGHTS["price_vs_value"] * _SIGN[pv.direction]
        + _WEIGHTS["order_flow"] * _SIGN[of.direction]
        + _WEIGHTS["oi_trend"] * _SIGN[oi.direction]
        + _WEIGHTS["cpr_two_day"] * _SIGN[td.direction]
        + _WEIGHTS["delta"] * _SIGN[gk.delta_direction]
        + _WEIGHTS["gamma"] * _SIGN[gk.gamma_direction]
    )

    if score > 0.05:
        direction: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "BULLISH"
    elif score < -0.05:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    confidence = min(95, round(50 + 22 * abs(score)))

    headline = f"{direction} {pv.label.upper()} {pv.vah:,.0f}" if direction != "NEUTRAL" else f"NEUTRAL NEAR {pv.poc:,.0f}"
    subtext = f"{oi.dominant.upper()} AT {oi.strike:,.0f}"

    factors = [
        BiasFactor("price_vs_value", "Price vs Value", pv.label, pv.direction),
        BiasFactor("order_flow", "Order Flow", of.label, of.direction),
        BiasFactor("oi_trend", "OI Trend", oi.dominant, oi.direction),
        BiasFactor("cpr_two_day", "Two-Day CPR", td.label, td.direction),
        BiasFactor("delta", "Delta", "Negative" if gk.net_delta < 0 else "Positive" if gk.net_delta > 0 else "Flat", gk.delta_direction),
        BiasFactor("gamma", "Gamma", "Negative" if gk.net_gamma < 0 else "Positive" if gk.net_gamma > 0 else "Flat", gk.gamma_direction),
    ]

    return BiasResult(
        direction=direction,
        headline=headline,
        subtext=subtext,
        confidence_pct=confidence,
        score=round(score, 4),
        factors=factors,
    )
