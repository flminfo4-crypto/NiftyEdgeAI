"""
Backtesting engine — powers backtester.html.

No historical options data source is wired up yet (see ai-engine/README.md
"Open questions" — intraday option-chain history isn't trivially available
from most Indian brokers), so `run_backtest()` currently runs its real metric
computations (win rate, profit factor, Sharpe/Sortino, max drawdown) over a
synthetic-but-plausible trade log calibrated to land close to the numbers
already shown in the frontend prototype (~80 trades, ~68% win rate, ~80%
net return). Swap `_mock_trade_log()` for a real replay engine once a
historical data vendor is chosen — `_compute_metrics()` doesn't change.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class Trade:
    opened_at: datetime
    closed_at: datetime
    label: str
    pnl: float
    result: str  # "WIN" | "LOSS"


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


_LABELS = [
    "SELL CALL (CE Writing)", "BUY PUT (PE Buying)", "SELL PUT (PE Writing)",
    "BUY CALL (CE Buying)", "IRON CONDOR",
]


def _mock_trade_log(starting_capital: float, n_trades: int, seed: int) -> list[Trade]:
    rng = random.Random(seed)
    trades: list[Trade] = []
    t = datetime.now(timezone.utc) - timedelta(days=90)
    for i in range(n_trades):
        win = rng.random() < 0.675  # ~68% win rate
        if win:
            pnl = rng.uniform(800, 4200)
            result = "WIN"
        else:
            pnl = -rng.uniform(600, 3200)
            result = "LOSS"
        opened = t
        closed = t + timedelta(hours=rng.uniform(1, 30))
        trades.append(Trade(opened_at=opened, closed_at=closed, label=rng.choice(_LABELS), pnl=round(pnl, 2), result=result))
        t = closed + timedelta(hours=rng.uniform(2, 20))
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
    starting_capital: float = 100_000.0,
    n_trades: int = 80,
    seed: int = 19,
) -> BacktestResult:
    trades = _mock_trade_log(starting_capital, n_trades, seed)
    return _compute_metrics(starting_capital, trades)
