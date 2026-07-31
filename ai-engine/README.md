# ai-engine/

The **Analytics Engine** layer in `docs/Architecture/Architecture.md` — everything that turns raw market data into the AI Market Bias, strategy signals, and backtest results shown in the frontend. Called by the Business Service Layer inside `backend/`, either in-process (same language: Python) or as its own service for heavier workloads like backtests.

## Status

Scaffold only — no code yet.

## Responsibilities

- **Market bias model** — the logic behind the Dashboard's "Market Bias (AI)" banner: combines price-vs-value-area position (Market Profile), order flow delta (Footprint), OI buildup classification (Open Interest), and portfolio Greeks into a directional bias + confidence score.
- **Strategy signal generation** — produces the entries shown on `strategy-signals.html` (e.g. "SELL CALL (CE Writing)", "BUY PUT (PE Buying)") with entry zone, target, stop loss, and a confidence score, along with the human-readable "Why?" reasoning list.
- **Backtesting engine** — powers `backtester.html`: replays a strategy definition against historical option/underlying data and returns equity curve, win rate, Sharpe, max drawdown, and a trade log.
- **Feature pipeline** — shared feature computation used by both live signal generation and backtesting, so results are comparable (CPR levels, TPO value area, volume profile HVN/LVN, cumulative delta, OI buildup, IV rank/percentile).

## Suggested layout

```
ai-engine/
├── features/         CPR, market profile, volume profile, footprint delta, OI buildup, Greeks — feature extraction, shared by live + backtest
├── models/            bias scoring model, signal generation rules/model
├── backtest/           backtest runner, performance metrics (Sharpe, drawdown, profit factor)
├── serving/            inference API (called by backend/, or exposed as its own service)
└── notebooks/           research / model iteration notebooks (not shipped to prod)
```

## Suggested stack

Python: pandas/numpy for feature computation, a lightweight model (gradient-boosted trees or a rules+scoring hybrid — deep learning is unlikely to be necessary or well-calibrated for this at first) for the bias/signal confidence score, and `vectorbt` or a hand-rolled event-driven backtester for `backtest/`. Keep feature computation as pure functions shared between live inference and backtesting so a strategy performs identically in both.

## Open questions to resolve before implementation

- Data vendor for historical options data (needed for realistic backtesting — intraday option chain history is not trivially available from most Indian brokers).
- Whether the bias model starts as a hand-tuned rules engine (transparent, easy to explain in the "Why?" list) versus a trained classifier (better calibrated, harder to explain) — the frontend's reasoning-list UI implies the former, at least initially.
