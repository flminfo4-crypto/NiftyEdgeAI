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


@dataclass
class OptionPrice:
    price: float
    delta: float


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
