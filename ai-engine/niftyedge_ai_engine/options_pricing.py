"""
Black-Scholes European option pricing — used by backtest.py to estimate
historical option premiums, since Dhan (and most Indian broker APIs) has no
historical option-chain/premium endpoint, only a live snapshot (see
broker_plugins/dhan/adapter.py). Premiums are therefore *modeled* from real
underlying price history rather than fetched — a standard approach when
historical option data isn't available, and the honest alternative to
fabricating trade outcomes outright.

IV input is realized_volatility() — the trailing annualized stdev of the
underlying's own real daily log returns — a defensible real-data proxy for
IV, not a guess. It will systematically understate true IV (which carries an
extra variance-risk premium over realized vol), so backtest premiums/results
here are directionally real but not a substitute for actual historical
option data.
"""

import math
from dataclasses import dataclass
from typing import Literal

RISK_FREE_RATE = 0.07  # India ~10Y G-Sec proxy; not fetched, a fixed assumption like risk_engine's mock leverage factor
MIN_T_YEARS = 1.0 / 365.0  # floor time-to-expiry so BS doesn't blow up on expiry day itself


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass
class OptionPrice:
    price: float
    delta: float


@dataclass
class Greeks:
    """Per-contract sensitivities. delta/gamma are per 1 point of underlying;
    theta is per calendar day; vega is per 1 volatility point (1%)."""
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float


def black_scholes(
    spot: float, strike: float, t_years: float, sigma: float,
    option_type: Literal["CE", "PE"], r: float = RISK_FREE_RATE,
) -> OptionPrice:
    t_years = max(t_years, MIN_T_YEARS)
    sigma = max(sigma, 0.01)
    d1 = (math.log(spot / strike) + (r + sigma ** 2 / 2) * t_years) / (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)
    if option_type == "CE":
        price = spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
        delta = _norm_cdf(d1)
    else:
        price = strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
    return OptionPrice(price=round(max(price, 0.05), 2), delta=round(delta, 4))


def implied_volatility(
    market_price: float, spot: float, strike: float, t_years: float,
    option_type: Literal["CE", "PE"], r: float = RISK_FREE_RATE,
) -> float:
    """Volatility that makes Black-Scholes reproduce a REAL traded premium.

    Unlike realized_volatility (a backward-looking proxy from the
    underlying's own history), this is the market's own forward-looking
    number, recovered from a price someone actually paid — so Greeks built
    on it describe the contract as it was really priced at that moment.

    Solved by bisection over 1%..300%: Black-Scholes is monotonic in sigma,
    so bisection always converges and can't diverge the way Newton-Raphson
    can near zero vega (deep ITM/OTM or on expiry day, both common here).
    Returns 0.0 when the premium is below intrinsic value or otherwise
    unsolvable — callers must treat that as "unavailable", not as zero vol.
    """
    t_years = max(t_years, MIN_T_YEARS)
    if market_price <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    intrinsic = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
    if market_price < intrinsic - 0.01:  # below intrinsic: no real sigma explains this
        return 0.0

    lo, hi = 0.01, 3.0
    if black_scholes(spot, strike, t_years, hi, option_type, r).price < market_price:
        return 0.0  # even 300% vol can't reach this price
    for _ in range(60):
        mid = (lo + hi) / 2
        if black_scholes(spot, strike, t_years, mid, option_type, r).price < market_price:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


def greeks(
    spot: float, strike: float, t_years: float, sigma: float,
    option_type: Literal["CE", "PE"], r: float = RISK_FREE_RATE,
) -> Greeks:
    """Analytic Black-Scholes Greeks. Feed it the sigma from
    implied_volatility() (solved from a real premium) and the result
    describes the contract as the market actually priced it."""
    t_years = max(t_years, MIN_T_YEARS)
    sigma = max(sigma, 0.01)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + sigma ** 2 / 2) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf_d1 = _norm_pdf(d1)

    gamma = pdf_d1 / (spot * sigma * sqrt_t)
    vega = spot * pdf_d1 * sqrt_t / 100.0  # per 1 vol point
    discount = math.exp(-r * t_years)
    if option_type == "CE":
        delta = _norm_cdf(d1)
        theta = (-spot * pdf_d1 * sigma / (2 * sqrt_t) - r * strike * discount * _norm_cdf(d2)) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-spot * pdf_d1 * sigma / (2 * sqrt_t) + r * strike * discount * _norm_cdf(-d2)) / 365.0
    return Greeks(
        delta=round(delta, 4), gamma=round(gamma, 6),
        theta=round(theta, 2), vega=round(vega, 2), iv=round(sigma * 100, 2),
    )


def realized_volatility(closes: list[float], window: int = 20) -> float:
    """Annualized stdev of daily log returns over the trailing `window` closes
    (last `window + 1` prices). Falls back to a broad-market-typical 15% if
    there isn't enough history yet, rather than dividing by zero."""
    recent = closes[-(window + 1):]
    if len(recent) < 3:
        return 0.15
    log_returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent)) if recent[i - 1] > 0]
    if len(log_returns) < 2:
        return 0.15
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_sigma = variance ** 0.5
    return round(daily_sigma * math.sqrt(252), 4)
