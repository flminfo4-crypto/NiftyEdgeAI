"""
Feature computation, shared by bias.py (live) and backtest.py (historical replay)
so a strategy behaves identically whether it's being scored live or backtested.

Inputs here are plain dicts/lists rather than broker_plugins dataclasses, so this
package has zero dependency on broker-plugins or backend — it's a pure analytics
library the Business Service Layer calls into (in-process, for now).

With no live feed, every function has a `mock` default: called with no arguments,
each returns the same numbers baked into the frontend prototype (dashboard's
"Market Bias (AI)" card, market-profile.html, footprint.html, open-interest.html)
so the AI layer and the UI agree with each other.
"""

from dataclasses import dataclass
from typing import Literal, Optional

Direction = Literal["BULLISH", "BEARISH", "NEUTRAL"]


@dataclass
class PriceVsValue:
    spot: float
    vah: float
    val: float
    poc: float
    label: str          # e.g. "Below VAH"
    direction: Direction


@dataclass
class OrderFlow:
    cumulative_delta: float
    label: str           # "Selling Pressure" / "Buying Pressure" / "Balanced"
    direction: Direction


@dataclass
class OiTrend:
    dominant: str         # "CE Writing" / "PE Writing" / "CE Unwinding" / "PE Unwinding"
    strike: float
    direction: Direction


@dataclass
class PortfolioGreeks:
    net_delta: float
    net_gamma: float
    delta_direction: Direction
    gamma_direction: Direction


def price_vs_value(
    spot: float = 23532.45,
    vah: float = 23601.25,
    val: float = 23499.50,
    poc: float = 23548.75,
) -> PriceVsValue:
    if spot > vah:
        label, direction = "Above VAH", "BULLISH"
    elif spot < val:
        label, direction = "Below VAL", "BEARISH"
    else:
        label, direction = "Below VAH", "BEARISH" if spot < poc else "BULLISH"
    return PriceVsValue(spot=spot, vah=vah, val=val, poc=poc, label=label, direction=direction)


def order_flow(cumulative_delta: float = -18420.0) -> OrderFlow:
    if cumulative_delta <= -5000:
        label, direction = "Selling Pressure", "BEARISH"
    elif cumulative_delta >= 5000:
        label, direction = "Buying Pressure", "BULLISH"
    else:
        label, direction = "Balanced", "NEUTRAL"
    return OrderFlow(cumulative_delta=cumulative_delta, label=label, direction=direction)


def oi_trend(ce_oi_change: float = 24.6, pe_oi_change: float = 5.5, strike: float = 23600.0) -> OiTrend:
    # Positive OI change dominated by calls near/above spot => resistance building (bearish);
    # positive OI change dominated by puts below spot => support building (bullish).
    if ce_oi_change >= pe_oi_change:
        return OiTrend(dominant="CE Writing", strike=strike, direction="BEARISH")
    return OiTrend(dominant="PE Writing", strike=strike, direction="BULLISH")


def cpr_two_day(
    high: float = 23580.00, low: float = 23400.00, close: float = 23532.45,
    prev_high: float = 23700.00, prev_low: float = 23520.00, prev_close: float = 23600.00,
):
    """Two-day CPR relationship factor [Ochoa 2010, Ch. 6] — defaults mirror
    backend/app/services/pivot_service.py's mock sessions so /market/cpr and
    /signals/bias tell the same story until a real EOD store exists."""
    from .pivots import classify_two_day, cpr

    return classify_two_day(cpr(high, low, close), cpr(prev_high, prev_low, prev_close))


def portfolio_greeks(net_delta: float = -812.5, net_gamma: float = -6.4) -> PortfolioGreeks:
    return PortfolioGreeks(
        net_delta=net_delta,
        net_gamma=net_gamma,
        delta_direction="BEARISH" if net_delta < 0 else "BULLISH" if net_delta > 0 else "NEUTRAL",
        gamma_direction="BEARISH" if net_gamma < 0 else "BULLISH" if net_gamma > 0 else "NEUTRAL",
    )


def compute_features(
    spot: Optional[float] = None,
    vah: Optional[float] = None,
    val: Optional[float] = None,
    poc: Optional[float] = None,
    cumulative_delta: Optional[float] = None,
    ce_oi_change: Optional[float] = None,
    pe_oi_change: Optional[float] = None,
    oi_strike: Optional[float] = None,
    net_delta: Optional[float] = None,
    net_gamma: Optional[float] = None,
) -> dict:
    """Bundle all five bias inputs. Any omitted argument falls back to the mock default."""
    kwargs_pv = {k: v for k, v in dict(spot=spot, vah=vah, val=val, poc=poc).items() if v is not None}
    kwargs_of = {k: v for k, v in dict(cumulative_delta=cumulative_delta).items() if v is not None}
    kwargs_oi = {
        k: v
        for k, v in dict(ce_oi_change=ce_oi_change, pe_oi_change=pe_oi_change, strike=oi_strike).items()
        if v is not None
    }
    kwargs_gk = {k: v for k, v in dict(net_delta=net_delta, net_gamma=net_gamma).items() if v is not None}

    return {
        "price_vs_value": price_vs_value(**kwargs_pv),
        "order_flow": order_flow(**kwargs_of),
        "oi_trend": oi_trend(**kwargs_oi),
        "greeks": portfolio_greeks(**kwargs_gk),
        "cpr_two_day": cpr_two_day(),
    }
