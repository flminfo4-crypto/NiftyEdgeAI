# Software Requirements Specification — NiftyEdgeAI

**Version:** 0.1 (draft, derived from the frontend prototype)
**Status:** Draft for review

## 1. Purpose

NiftyEdgeAI is a web-based options trading terminal for Indian index and stock derivatives (NIFTY 50, BANKNIFTY, SENSEX, and F&O stocks). It gives a discretionary options trader a single screen for market-profile/order-flow analysis, an AI-generated market bias and trade signals, options chain/Greeks/OI analytics, order and position management, strategy backtesting, and reporting — connected to a real brokerage account.

This document specifies the functional and non-functional requirements implied by the existing frontend prototype (`frontend/`), which serves as the UX reference.

## 2. Scope

In scope for v1:

- Read-only and interactive market analytics (Dashboard, CPR, Market Profile, Volume Profile, Footprint, Options Chain, Open Interest, Greeks).
- AI-generated market bias and strategy signals, with historical performance tracking.
- Order placement/management and position tracking against one connected broker account.
- Strategy backtesting against historical data.
- P&L/tax reporting.
- Risk limits and notification preferences.

Out of scope for v1: multi-account/multi-broker simultaneous trading, social/copy-trading features, mobile native apps, options strategies beyond single/multi-leg (no complex structured products).

## 3. User roles

- **Trader (primary)** — the only role modeled in the current prototype. Views analytics, receives signals, places/manages orders, configures risk limits.
- **Admin (future)** — user/account management, system health, broker connection management across users. Not present in the current UI; call out explicitly as a v2 concern.

## 4. Functional requirements by module

### 4.1 Dashboard (`index.html`)
- FR-1.1 Display live NIFTY 50 price, change, and % change.
- FR-1.2 Display India VIX, PCR (OI), ATM IV, IV Rank, and ATM straddle premium.
- FR-1.3 Compute and display an AI Market Bias (direction + confidence %) derived from price-vs-value-area, order flow, OI trend, and portfolio Greeks (delta/gamma sign).
- FR-1.4 Display Price vs Value card: VAH / POC / VAL and whether spot is above/below VAH.
- FR-1.5 Display OI resistance/support walls (top 2 strikes each side) with OI magnitude bars.
- FR-1.6 Display market internals: advance/decline, A/D ratio, new highs/lows, cumulative delta.
- FR-1.7 Render an intraday candlestick chart with VAH/POC/VAL/Pivot/R1/S1 overlay lines, VWAP, and a vertical volume profile alongside it.
- FR-1.8 Render cumulative volume delta (CVD) chart for the session.
- FR-1.9 Display the primary AI strategy signal (direction, confidence, entry zone, target, stop loss, reasoning list) and one alternative setup.
- FR-1.10 Display an option chain snapshot (5 strikes around ATM), Greeks summary for ATM, risk & position summary, and open positions — all as live-updating summary cards.

### 4.2 CPR Dashboard (`cpr-dashboard.html`)
- FR-2.1 Compute and display Central Pivot Range (TC/Pivot/BC), R1/R2/S1/S2, and prior-day high/low for NIFTY 50 and SENSEX, pre-market.
- FR-2.2 Classify CPR width (Narrow/Medium/Wide) and relationship (Ascending/Descending/Unchanged/Inside/Outside) vs. the prior session.
- FR-2.3 Generate a plain-language setup/market-view narrative from the computed CPR classification.
- FR-2.4 Display Gift Nifty, India VIX, PCR, and ATM straddle premiums for both indices.
- FR-2.5 Surface a ranked list of stocks/sectors with the narrowest CPR width (used as an intraday breakout watchlist).
- FR-2.6 Support exporting/sharing the report as an image.

### 4.3 Market Profile (`market-profile.html`)
- FR-3.1 Render a TPO (Time Price Opportunity) profile for the session at a configurable bracket size (15/30/60 min).
- FR-3.2 Compute and highlight Value Area (70%), POC, Initial Balance, day type (Normal/Trend/Neutral/Normal Variation), and open type.
- FR-3.3 Compare current session's value area/POC against the prior session (overlap %, POC migration).
- FR-3.4 Identify single prints and poor highs/lows.

### 4.4 Volume Profile (`volume-profile.html`)
- FR-4.1 Render a volume-by-price histogram for session, and composite (5-day / 20-day) ranges.
- FR-4.2 Identify and label High Volume Nodes (HVN) and Low Volume Nodes (LVN), POC, VAH/VAL.
- FR-4.3 Support a fixed-range profile mode (user-selected price/time range).

### 4.5 Footprint / Order Flow (`footprint.html`)
- FR-5.1 Render a bid×ask footprint grid per price level per candle for a configurable interval.
- FR-5.2 Highlight buy/sell imbalances (configurable threshold, default 250%).
- FR-5.3 Compute and display per-candle delta and session cumulative delta (CVD chart).
- FR-5.4 Identify absorption zones (repeated imbalance stacking at a price level).

### 4.6 Options Chain (`options-chain.html`)
- FR-6.1 Display full calls/puts chain for a selected expiry: OI, OI change, volume, IV, LTP per strike.
- FR-6.2 Highlight ATM strike and configurable strike range (±8/±15/all).
- FR-6.3 Compute PCR (OI), max pain, total call/put OI, and ATM straddle premium.
- FR-6.4 Support CSV export.

### 4.7 Open Interest (`open-interest.html`)
- FR-7.1 Display OI-by-strike bar chart (calls vs. puts).
- FR-7.2 Classify each strike/side's OI buildup (Long Buildup / Short Buildup / Short Covering / Long Unwinding) from OI-change + price-change sign.
- FR-7.3 Compute max pain per available expiry.
- FR-7.4 Render intraday PCR trend.

### 4.8 Greeks (`greeks.html`)
- FR-8.1 Display per-strike Delta/Gamma/Theta/Vega/Rho for calls and puts.
- FR-8.2 Compute and display portfolio-level net Greeks from the user's open positions.
- FR-8.3 Render a gamma exposure (GEX) curve and flag the strike with peak gamma concentration.

### 4.9 Strategy Signals (`strategy-signals.html`)
- FR-9.1 List active AI signals with direction, confidence, entry/target/stop, and reasoning.
- FR-9.2 Maintain signal history with outcome (Target Hit / SL Hit / Expired) and realized P&L.
- FR-9.3 Compute and display hit-rate statistics overall and broken down by strategy type (CE writing, PE writing, directional buying, iron condor, straddle/strangle).
- FR-9.4 Display confidence calibration (realized hit rate vs. stated confidence).

### 4.10 Positions (`positions.html`)
- FR-10.1 List open positions (instrument, side, qty, avg price, LTP, per-position delta/theta, P&L, P&L%) grouped by Options/Futures/Equity.
- FR-10.2 List closed (same-day realized) positions.
- FR-10.3 Support one-click exit per position and a "square off all" action.
- FR-10.4 Display day P&L, margin used, available margin, and exposure %.

### 4.11 Orders (`orders.html`)
- FR-11.1 List open, executed, and cancelled orders for the session.
- FR-11.2 Support order entry (Market/Limit/SL/SL-M, MIS/NRML product type, quantity in lots, price) with margin-required preview before submission.
- FR-11.3 Support order cancellation while pending.
- FR-11.4 Enforce configured risk limits (max lot size per order, max exposure) at submission time — reject or warn before forwarding to the broker.

### 4.12 Backtester (`backtester.html`)
- FR-12.1 Accept a strategy definition (rule set or reference to an ai-engine strategy), date range, instrument, initial capital, position size, and SL/target parameters.
- FR-12.2 Run the backtest against historical data and return an equity curve, trade log, and summary metrics (net profit, win rate, profit factor, Sharpe/Sortino, max drawdown, streaks, average holding time).
- FR-12.3 Support toggling slippage/cost modeling on or off.
- FR-12.4 Support exporting the trade log.

### 4.13 Reports (`reports.html`)
- FR-13.1 Generate daily P&L statement, contract note, monthly account statement, realized P&L (tax) report, and strategy performance report, each downloadable (PDF and/or XLSX).
- FR-13.2 Display MTD net/realized/unrealized P&L and a charges breakdown (brokerage, STT, exchange charges, GST, SEBI/stamp duty).
- FR-13.3 Display a daily P&L bar chart and win/loss day split for the selected period.

### 4.14 Settings (`settings.html`)
- FR-14.1 Manage profile (name, email, client ID, trading segment).
- FR-14.2 Manage broker connection: connection status, API key/secret (masked), token regeneration.
- FR-14.3 Configure data/refresh preferences (real-time ticks on/off, option chain auto-refresh interval, extended-hours data on/off).
- FR-14.4 Configure risk limits: max daily loss, max open positions, max lot size per order, max exposure %, auto square-off on max loss, block orders exceeding exposure.
- FR-14.5 Configure notification preferences (new signal, order filled/rejected, target/SL hit, margin call, daily P&L email).
- FR-14.6 Select UI theme (Light / Dark / Terminal), chart candle color scheme, and default landing page.

### 4.15 Help (`help.html`)
- FR-15.1 Provide searchable FAQ content.
- FR-15.2 Provide guide/tutorial links for each major feature area.
- FR-15.3 Provide live chat / support ticket entry points.
- FR-15.4 Display keyboard shortcuts reference and live system status (data feed, order execution, AI signal engine, broker API).

## 5. Non-functional requirements

- **NFR-1 Latency:** option chain and quote data should refresh at a configurable interval (default 5s) with a target end-to-end latency under 1s from broker to UI once live.
- **NFR-2 Availability:** the trading path (quotes → order placement → broker) is the highest-availability requirement in the system; analytics pages (Market/Volume Profile, Backtester, Reports) can tolerate brief degradation.
- **NFR-3 Auditability:** every order placed, modified, or cancelled — and every risk-limit block — must be logged immutably with timestamp and the state that triggered it.
- **NFR-4 Security:** broker API keys/secrets encrypted at rest; no credentials ever rendered in full in the UI (masked, as in the current Settings mock); session tokens short-lived with refresh.
- **NFR-5 Correctness of financial figures:** P&L, margin, and Greeks calculations must be covered by regression tests against known-good reference values before shipping (see `tests/`).
- **NFR-6 Accessibility/theming:** the UI must support the three shipped themes (Light/Dark/Terminal) without loss of contrast or functionality (WCAG AA contrast target for Light and Dark at minimum).
- **NFR-7 Responsiveness:** primary target is desktop (this is a professional trading tool); the existing prototype's responsive breakpoints (collapsing multi-column grids below ~1200px) should be preserved, but a full mobile-optimized layout is not required for v1.
- **NFR-8 Disclaimers:** signal and CPR-report content must carry an "educational content only, not investment advice" disclaimer wherever shown outside the app shell (already present on `cpr-dashboard.html`).

## 6. Assumptions & open questions

- Which broker(s) are prioritized for v1 integration (`broker-plugins/`) — assumed Zerodha Kite Connect first, based on the Settings mock.
- Source of historical option-chain data for backtesting — not all Indian brokers expose deep historical option data; a third-party data vendor may be required (see `ai-engine/README.md`).
- Whether signals are advisory-only (user must manually place the order) or support one-click "trade this signal" execution — current UI implies advisory-only (signals are shown separately from Orders).
