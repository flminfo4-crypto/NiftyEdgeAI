"""
Backtesting engine — powers backtester.html.

Runs over real historical underlying OHLC (Dhan provides ~5-6 years of daily
NIFTY50/BANKNIFTY candles — see broker_plugins/dhan/adapter.py). Entry/exit
rules are driven by real CPR levels computed from real prior-day OHLC (see
pivots.py — the same calculation backing /market/cpr). There is no historical
option-chain data source (Dhan only exposes a live snapshot), so option-
strategy premiums are *modeled* with Black-Scholes using realized volatility
from the real price series as the IV input (see options_pricing.py) rather
than fabricated outright — a disclosed approximation, not fake data. Futures
strategies need no such modeling: their P&L is the real spot move itself.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from .options_pricing import black_scholes, realized_volatility
from .pivots import daily_levels

LOT_SIZE = 75  # NIFTY standard lot (see niftyedge_ai_engine equivalents elsewhere)
STRIKE_STEP = 50
# Rough all-in cost (brokerage + STT + exchange + GST + stamp duty) as a
# fraction of premium turnover — same order of magnitude as
# order_service._estimate_charges on the backend, kept local since ai-engine
# has no backend dependency.
COST_FRACTION = 0.0015


@dataclass
class Candle:
    dt: date
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    opened_at: datetime
    closed_at: datetime
    label: str
    pnl: float
    result: str  # "WIN" | "LOSS"
    entry_price: float = 0.0
    exit_price: float = 0.0


@dataclass
class BacktestResult:
    starting_capital: float
    net_profit: float
    net_profit_pct: float
    win_rate_pct: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    volatility_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    equity_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)


_STRATEGY_LABELS = {
    "ai-bias-ce-writing-below-vah": "SELL CALL (CE Writing)",
    "mean-reversion-at-val": "BUY CALL (Mean Reversion)",
    "breakout-above-vah": "BUY CALL (Breakout)",
    "iron-condor-weekly": "IRON CONDOR",
}


def _round_strike(spot: float) -> float:
    return round(spot / STRIKE_STEP) * STRIKE_STEP


def _next_thursday(d: date) -> date:
    offset = (3 - d.weekday()) % 7  # Mon=0 .. Thu=3 .. Sun=6
    return d + timedelta(days=offset or 7)


def _signal(strategy: str, entry_spot: float, levels) -> Optional[str]:
    """Returns a strike-selection hint ('CALL_OTM_WRITE', 'CALL_ATM_BUY',
    'CONDOR') or None if today's real CPR levels don't meet the strategy's
    entry condition."""
    cpr = levels.cpr_today
    two_day = levels.two_day
    bearish_or_flat = two_day is None or two_day.direction != "BULLISH"

    if strategy == "ai-bias-ce-writing-below-vah":
        if entry_spot < cpr.tc and bearish_or_flat:
            return "CALL_OTM_WRITE"
    elif strategy == "mean-reversion-at-val":
        if entry_spot <= cpr.bc * 1.002:
            return "CALL_ATM_BUY"
    elif strategy == "breakout-above-vah":
        if entry_spot > cpr.tc:
            return "CALL_ATM_BUY"
    elif strategy == "iron-condor-weekly":
        if levels.width.regime in ("NORMAL", "WIDE"):
            return "CONDOR"
    return None


def _leg_pnl(
    entry_spot: float, strike: float, option_type: Literal["CE", "PE"], side: Literal["BUY", "SELL"],
    entry_date: date, expiry: date, path: list[Candle], sigma: float,
    stop_loss_pct: float, target_pct: float, direction: int,
) -> tuple[float, float, date]:
    """Simulates one option leg day-by-day (re-pricing via Black-Scholes on
    each subsequent real close) until stop/target trips or expiry is reached.

    stop_loss_pct/target_pct are checked against the *underlying's* real move
    from entry (the standard convention — "1.5% SL" means a 1.5% move in
    NIFTY, not in the option premium, which is far more volatile day-to-day
    and would blow through a same-sized threshold almost immediately on the
    next daily close). `direction` is +1 if the position profits from the
    underlying rising (long calls) or -1 if it profits from the underlying
    NOT rising (short calls) — it decides which way a move counts as
    favorable vs. adverse. Realized P&L still comes from the real premium
    (via Black-Scholes) at whichever day the exit condition fires.
    Returns (entry_premium, exit_premium, closed_date)."""
    entry_t = max((expiry - entry_date).days / 365.0, 1.0 / 365.0)
    entry_price = black_scholes(entry_spot, strike, entry_t, sigma, option_type).price

    exit_price = entry_price
    closed = expiry
    for c in path:
        if c.dt <= entry_date:
            continue
        t = max((expiry - c.dt).days / 365.0, 1.0 / 365.0)
        px = black_scholes(c.close, strike, t, sigma, option_type).price
        spot_move_pct = direction * (c.close - entry_spot) / entry_spot * 100
        exit_price = px
        closed = c.dt
        if spot_move_pct >= target_pct or spot_move_pct <= -stop_loss_pct or c.dt >= expiry:
            break
    return entry_price, exit_price, closed


def _condor_pnl(
    entry_spot: float, ce_strike: float, pe_strike: float, entry_date: date, expiry: date,
    path: list[Candle], sigma: float, stop_loss_pct: float, target_pct: float,
) -> tuple[float, float, date]:
    """Iron condor is a range bet, not directional, so its stop isn't "the
    underlying moved against me" in a single direction — it's "the underlying
    threatened a wing" (an absolute move past stop_loss_pct from entry either
    way). Target is combined-premium decay (the normal way a credit spread's
    profit is realized) rather than a further underlying move."""
    entry_t = max((expiry - entry_date).days / 365.0, 1.0 / 365.0)
    ce_entry = black_scholes(entry_spot, ce_strike, entry_t, sigma, "CE").price
    pe_entry = black_scholes(entry_spot, pe_strike, entry_t, sigma, "PE").price
    entry_premium = ce_entry + pe_entry

    exit_premium = entry_premium
    closed = expiry
    for c in path:
        if c.dt <= entry_date:
            continue
        t = max((expiry - c.dt).days / 365.0, 1.0 / 365.0)
        ce_px = black_scholes(c.close, ce_strike, t, sigma, "CE").price
        pe_px = black_scholes(c.close, pe_strike, t, sigma, "PE").price
        exit_premium = ce_px + pe_px
        closed = c.dt
        breach_pct = abs(c.close - entry_spot) / entry_spot * 100
        decay_pct = (entry_premium - exit_premium) / entry_premium * 100
        if breach_pct >= stop_loss_pct or decay_pct >= target_pct or c.dt >= expiry:
            break
    return entry_premium, exit_premium, closed


def _simulate_trades(
    candles: list[Candle], strategy: str, position_size_lots: int,
    stop_loss_pct: float, target_pct: float, include_costs: bool, starting_capital: float,
) -> list[Trade]:
    trades: list[Trade] = []
    closes = [c.close for c in candles]
    last_exit_date: Optional[date] = None
    equity = starting_capital

    for i in range(2, len(candles) - 1):
        if equity <= 0:
            break  # account wiped out — a real trader stops here, not carries on
        today = candles[i]
        if last_exit_date and today.dt <= last_exit_date:
            continue  # one position at a time
        prior, before = candles[i - 1], candles[i - 2]
        levels = daily_levels(prior.high, prior.low, prior.close, before.high, before.low, before.close)
        entry_spot = today.open
        hint = _signal(strategy, entry_spot, levels)
        if not hint:
            continue

        expiry = _next_thursday(today.dt)
        path = [c for c in candles[i:] if c.dt <= expiry + timedelta(days=1)]
        sigma = realized_volatility(closes[: i + 1])
        label = _STRATEGY_LABELS[strategy]

        if hint == "CONDOR":
            ce_strike = _round_strike(entry_spot) + 200
            pe_strike = _round_strike(entry_spot) - 200
            entry_p, exit_p, closed_date = _condor_pnl(
                entry_spot, ce_strike, pe_strike, today.dt, expiry, path, sigma, stop_loss_pct, target_pct
            )
            pnl = (entry_p - exit_p) * LOT_SIZE * position_size_lots
            turnover = (entry_p + exit_p) * LOT_SIZE * position_size_lots
        elif hint == "CALL_OTM_WRITE":
            strike = _round_strike(entry_spot) + 100
            entry_p, exit_p, closed_date = _leg_pnl(entry_spot, strike, "CE", "SELL", today.dt, expiry, path, sigma, stop_loss_pct, target_pct, direction=-1)
            pnl = (entry_p - exit_p) * LOT_SIZE * position_size_lots
            turnover = (entry_p + exit_p) * LOT_SIZE * position_size_lots
        else:  # CALL_ATM_BUY
            strike = _round_strike(entry_spot)
            entry_p, exit_p, closed_date = _leg_pnl(entry_spot, strike, "CE", "BUY", today.dt, expiry, path, sigma, stop_loss_pct, target_pct, direction=1)
            pnl = (exit_p - entry_p) * LOT_SIZE * position_size_lots
            turnover = (entry_p + exit_p) * LOT_SIZE * position_size_lots

        if include_costs:
            pnl -= turnover * COST_FRACTION

        pnl = round(pnl, 2)
        trades.append(Trade(
            opened_at=datetime.combine(today.dt, datetime.min.time(), tzinfo=timezone.utc),
            closed_at=datetime.combine(closed_date, datetime.min.time(), tzinfo=timezone.utc),
            label=label, pnl=pnl, result="WIN" if pnl > 0 else "LOSS",
            entry_price=round(entry_p, 2), exit_price=round(exit_p, 2),
        ))
        last_exit_date = closed_date
        equity += pnl

    return trades


def _simulate_futures_trades(candles: list[Candle], strategy: str, position_size_lots: int, include_costs: bool, starting_capital: float) -> list[Trade]:
    """Futures need no premium model — P&L is the real spot move itself."""
    trades: list[Trade] = []
    last_exit_date: Optional[date] = None
    equity = starting_capital
    for i in range(2, len(candles) - 1):
        if equity <= 0:
            break
        today = candles[i]
        if last_exit_date and today.dt <= last_exit_date:
            continue
        prior, before = candles[i - 1], candles[i - 2]
        levels = daily_levels(prior.high, prior.low, prior.close, before.high, before.low, before.close)
        entry_spot = today.open
        hint = _signal(strategy, entry_spot, levels)
        if not hint or hint == "CONDOR":
            continue  # a condor has no directional futures equivalent
        direction = -1 if hint == "CALL_OTM_WRITE" else 1  # bearish setups short the future, bullish setups go long

        expiry = _next_thursday(today.dt)
        exit_candle = today
        for c in candles[i:]:
            if c.dt <= today.dt:
                continue
            exit_candle = c
            if c.dt >= expiry:
                break
        pnl = direction * (exit_candle.close - entry_spot) * LOT_SIZE * position_size_lots
        if include_costs:
            pnl -= abs(entry_spot + exit_candle.close) * LOT_SIZE * position_size_lots * COST_FRACTION
        pnl = round(pnl, 2)
        trades.append(Trade(
            opened_at=datetime.combine(today.dt, datetime.min.time(), tzinfo=timezone.utc),
            closed_at=datetime.combine(exit_candle.dt, datetime.min.time(), tzinfo=timezone.utc),
            label=("SELL FUTURES" if direction < 0 else "BUY FUTURES"), pnl=pnl, result="WIN" if pnl > 0 else "LOSS",
            entry_price=round(entry_spot, 2), exit_price=round(exit_candle.close, 2),
        ))
        last_exit_date = exit_candle.dt
        equity += pnl
    return trades


def _compute_metrics(starting_capital: float, trades: list[Trade]) -> BacktestResult:
    equity = [starting_capital]
    for tr in trades:
        equity.append(equity[-1] + tr.pnl)

    wins = [tr.pnl for tr in trades if tr.pnl > 0]
    losses = [tr.pnl for tr in trades if tr.pnl <= 0]

    net_profit = sum(tr.pnl for tr in trades)
    net_profit_pct = (net_profit / starting_capital) * 100 if starting_capital else 0.0
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else float("inf")

    # per-trade returns (as a fraction of starting capital) for Sharpe/Sortino/vol
    returns = [tr.pnl / starting_capital for tr in trades] if starting_capital else []
    mean_ret = sum(returns) / len(returns) if returns else 0.0
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns) if returns else 0.0
    std_dev = variance ** 0.5
    downside = [r for r in returns if r < 0]
    downside_var = sum(r ** 2 for r in downside) / len(downside) if downside else 0.0
    downside_dev = downside_var ** 0.5

    # Annualization factor derived from the trade log's own date span, rather than a fixed
    # 252 (equities-daily-bar) assumption, since these are event-driven options trades that
    # don't fire on a fixed daily schedule.
    if len(trades) >= 2:
        elapsed_days = max((trades[-1].closed_at - trades[0].opened_at).total_seconds() / 86400, 1.0)
        trades_per_year = len(trades) / elapsed_days * 365
    else:
        trades_per_year = len(trades) or 1
    sharpe = (mean_ret / std_dev) * (trades_per_year ** 0.5) if std_dev else 0.0
    sortino = (mean_ret / downside_dev) * (trades_per_year ** 0.5) if downside_dev else 0.0
    volatility_pct = std_dev * (trades_per_year ** 0.5) * 100

    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        dd = (e - peak) / peak * 100 if peak else 0.0
        max_dd = min(max_dd, dd)

    return BacktestResult(
        starting_capital=starting_capital,
        net_profit=round(net_profit, 2),
        net_profit_pct=round(net_profit_pct, 2),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(profit_factor, 2),
        sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2),
        max_drawdown_pct=round(max_dd, 2),
        volatility_pct=round(volatility_pct, 1),
        total_trades=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        equity_curve=[round(e, 2) for e in equity],
        trades=trades,
    )


def run_backtest(
    candles: list[Candle],
    strategy: str = "ai-bias-ce-writing-below-vah",
    starting_capital: float = 100_000.0,
    position_size_lots: int = 3,
    stop_loss_pct: float = 1.5,
    target_pct: float = 3.0,
    include_slippage_and_costs: bool = True,
    is_futures: bool = False,
) -> BacktestResult:
    """`candles` must be real historical daily OHLC for the requested
    underlying+date range (see backend/app/services/backtest_service.py),
    sorted ascending, with a couple of extra leading days so the first
    in-range day still has real prior-day OHLC for its CPR calculation."""
    if is_futures:
        trades = _simulate_futures_trades(candles, strategy, position_size_lots, include_slippage_and_costs, starting_capital)
    else:
        trades = _simulate_trades(candles, strategy, position_size_lots, stop_loss_pct, target_pct, include_slippage_and_costs, starting_capital)
    return _compute_metrics(starting_capital, trades)
