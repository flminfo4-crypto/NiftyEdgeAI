# NiftyEdgeAI — User Guide

This guide walks through the terminal screen by screen. It describes the product as designed in `frontend/`; once connected to a live broker (`broker-plugins/`) the data shown becomes real instead of illustrative.

## Getting started

1. Log in and link your broker account under **Settings → Broker & API Connection**.
2. Set your risk limits under **Settings → Risk Limits** before placing any orders — max daily loss, max lot size per order, and max exposure are enforced automatically once configured.
3. Pick a theme under **Settings → Appearance** (or the sun/moon/terminal icons in the top-right of any page): **Light** for daytime use, **Dark** for low-light trading, **Terminal** for a retro green-on-black CRT look. Your choice is remembered across sessions.

## Pre-market: CPR Dashboard

Open before the market opens to get your levels for the day. It shows, for both NIFTY 50 and SENSEX:

- **TC / Pivot / BC** — the Central Pivot Range for the session, and whether it's Narrow, Medium, or Wide (narrower ranges tend to precede bigger moves).
- **R1/R2/S1/S2, PDH/PDL** — resistance and support levels to watch.
- A plain-language **setup note** ("Medium ascending CPR — bullish tilt...") summarizing what the levels imply.
- **Gift Nifty, India VIX, PCR, straddle premiums** for a quick read on where the market is likely to open and how expensive options are.
- **Top Narrow CPR stocks/sectors** — a watchlist of names most likely to make a sharp intraday move.

Use **Export / Share Image** to save the report for your morning notes.

## Dashboard

Your main screen once the market is open.

- The banner at the top is the **AI Market Bias** — a direction (Bullish/Bearish) and confidence %, with the five factors that drove it (price vs. value area, order flow, OI trend, delta, gamma).
- **Price vs. Value**, **OI Walls**, **Market Internals**, and **Volatility Snapshot** give you the supporting context at a glance.
- The main chart shows price with your key levels overlaid, plus a **volume profile** alongside it and **cumulative volume delta (CVD)** below — CVD rising means buyers are more aggressive than sellers, and vice versa.
- On the right, the **Strategy Signal** card is the AI's current trade idea: direction, confidence, entry zone, target, and stop loss, with a "Why?" list explaining the reasoning. An **Alternative Setup** is shown if the primary thesis fails.
- At the bottom: a snapshot of the option chain, ATM Greeks, your risk/position summary, and open positions.

## Market Profile / Volume Profile

Both analyze where the day's volume/time was concentrated, from two angles:

- **Market Profile** (TPO) shows *time* spent at each price as a letter-coded profile, and tells you the day type (Normal, Trend, Neutral) and whether today's value area overlaps with yesterday's.
- **Volume Profile** shows *volume* traded at each price as a histogram, with High/Low Volume Nodes marked — HVNs tend to act as support/resistance, LVNs are where price often moves through quickly.

## Footprint (Order Flow)

The most granular view: for each price level within each candle, you see buy volume vs. sell volume (bid × ask). Green-highlighted cells are buy imbalances, red are sell imbalances. The delta row at the bottom totals buying vs. selling pressure per candle, and the CVD chart shows the running total for the session. Use this to spot where large buyers or sellers are absorbing the other side at a price level.

## Options Chain, Open Interest, Greeks

- **Options Chain** is the full calls/puts table for your selected expiry — OI, OI change, volume, IV, and LTP per strike, with PCR and Max Pain summarized at the top.
- **Open Interest** classifies what each strike's OI change means (Long Buildup, Short Buildup, Short Covering, Long Unwinding) — this tells you whether new positions are being opened or existing ones unwound.
- **Greeks** shows Delta/Gamma/Theta/Vega/Rho per strike and rolls your open positions up into portfolio-level net Greeks, so you know your overall directional (delta), acceleration (gamma), time-decay (theta), and volatility (vega) exposure at a glance.

## Strategy Signals

A running feed of every AI-generated trade idea, active and historical, with a hit-rate breakdown by strategy type and a confidence-calibration chart (does a "70% confidence" signal actually win about 70% of the time?). Use this to decide how much weight to give new signals.

## Positions & Orders

- **Positions** shows everything you currently hold, live P&L, and lets you exit one position or square off everything at once.
- **Orders** is where you place new orders (Market/Limit/SL/SL-M) and manage pending ones. Margin required is shown before you submit. If an order would breach a risk limit you've set, it's blocked here with an explanation rather than silently failing at the broker.

## Backtester

Test a strategy against history before trusting it live: pick a strategy, date range, position size, and stop-loss/target, and get back an equity curve, win rate, profit factor, Sharpe ratio, max drawdown, and a full trade log.

## Reports

Daily P&L statements, contract notes, monthly account statements, tax reports, and strategy performance reports — viewable in-app and downloadable as PDF/XLSX. The charges breakdown shows exactly what brokerage, STT, exchange fees, GST, and stamp duty cost you over the period.

## Settings

Manage your profile, broker connection, risk limits, notification preferences, data refresh behavior, and appearance (theme, chart colors, default landing page) here.

## Help

Searchable FAQ, feature guides, keyboard shortcuts, live chat/support, and a live system status panel (market data feed, order execution, AI signal engine, broker API) so you can tell at a glance if something's degraded before you trade.

---

**Reminder:** AI Market Bias and Strategy Signals reflect a statistical model based on historical patterns. They are educational, not investment advice — always size positions according to your own risk limits.
