# API Specification — NiftyEdgeAI backend

**Version:** 0.1 (draft)
**Base URL:** `/api/v1`
**Auth:** Bearer JWT in `Authorization` header, unless noted. All endpoints below are scoped to the authenticated user's linked broker account.

This spec covers the REST surface needed to power every screen in `frontend/`. Real-time data (ticks, option chain updates, order/position status changes, live signals) is delivered over the WebSocket gateway (§9), not polled REST — REST is used for initial page load and actions (placing an order, running a backtest, etc.).

## 1. Auth

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create a user account |
| POST | `/auth/login` | Email/password login → access + refresh token |
| POST | `/auth/refresh` | Exchange refresh token for a new access token |
| POST | `/auth/logout` | Invalidate current session |
| POST | `/auth/broker/connect` | Start broker OAuth/API-key linking flow (broker-specific) |
| GET | `/auth/broker/status` | Connection status, last sync time — powers Settings → Broker & API |

## 2. Market data

| Method | Path | Description |
|---|---|---|
| GET | `/market/quote?symbols=NIFTY50,SENSEX,INDIAVIX` | Latest LTP/change for the topbar ticker |
| GET | `/market/candles?symbol=NIFTY50&interval=5m&from=&to=` | OHLCV for the Dashboard chart |
| GET | `/market/option-chain?underlying=NIFTY50&expiry=2026-07-24` | Full chain: strike, CE/PE OI, OI chg, volume, IV, LTP |
| GET | `/market/expiries?underlying=NIFTY50` | Available expiries for the selector |
| GET | `/market/cpr?underlying=NIFTY50\|SENSEX&date=` | TC/Pivot/BC, R1/R2/S1/S2, PDH/PDL, width/relationship classification — powers `cpr-dashboard.html` |
| GET | `/market/market-profile?underlying=NIFTY50&bracket=30m&date=` | TPO letters per price level, VAH/POC/VAL, IB, day type |
| GET | `/market/volume-profile?underlying=NIFTY50&range=session\|5d\|20d` | Volume-by-price rows, HVN/LVN, POC/VAH/VAL |
| GET | `/market/footprint?underlying=NIFTY50&interval=5m&from=&to=` | Bid×ask grid per price level per candle, per-candle delta |
| GET | `/market/open-interest?underlying=NIFTY50&expiry=` | OI by strike, buildup classification, max pain |
| GET | `/market/greeks?underlying=NIFTY50&expiry=` | Per-strike Greeks (calls & puts) |
| GET | `/market/internals?index=NIFTY50` | Advance/decline, new highs/lows, cumulative delta |
| GET | `/market/narrow-cpr?scope=stocks\|sectors` | Ranked narrow-CPR watchlist for the CPR dashboard |

## 3. AI bias & strategy signals

| Method | Path | Description |
|---|---|---|
| GET | `/signals/bias?underlying=NIFTY50` | Current AI Market Bias: direction, confidence, contributing factors |
| GET | `/signals/active` | Active signals (primary + alternative setups) |
| GET | `/signals/history?from=&to=&strategy=` | Closed signals with outcome and realized P&L |
| GET | `/signals/performance` | Hit-rate stats, overall and by strategy type; confidence calibration curve |

## 4. Positions & orders

| Method | Path | Description |
|---|---|---|
| GET | `/positions/open` | Open positions with live P&L, per-position Greeks |
| GET | `/positions/closed?date=` | Same-day realized/closed positions |
| POST | `/positions/{id}/exit` | Square off a single position |
| POST | `/positions/exit-all` | Square off all open positions |
| GET | `/orders` | Order book, filterable by status (`pending`, `executed`, `cancelled`) |
| POST | `/orders` | Place an order — see §4.1 for body/response |
| PUT | `/orders/{id}` | Modify a pending order |
| DELETE | `/orders/{id}` | Cancel a pending order |
| GET | `/orders/margin-preview` | Margin required for a not-yet-submitted order (query params mirror POST body) |

### 4.1 `POST /orders`

Request:
```json
{
  "instrument": "NIFTY24JUL23500CE",
  "side": "BUY",
  "orderType": "LIMIT",
  "product": "MIS",
  "quantityLots": 3,
  "price": 58.40,
  "triggerPrice": null
}
```

Response `201`:
```json
{
  "orderId": "NE-8841203",
  "brokerOrderId": "231025000123456",
  "status": "PENDING",
  "marginRequired": 42180.00
}
```

Response `422` (risk limit rejection):
```json
{
  "error": "RISK_LIMIT_EXCEEDED",
  "message": "Order would exceed max exposure limit of 60%.",
  "limit": "maxExposurePct",
  "currentValue": 58.2,
  "attemptedValue": 64.7
}
```

## 5. Backtester

| Method | Path | Description |
|---|---|---|
| POST | `/backtests` | Submit a backtest run (async job) — returns `jobId` |
| GET | `/backtests/{jobId}` | Job status; once complete, includes results |
| GET | `/backtests/{jobId}/trades` | Full trade log (paginated) |
| GET | `/backtests` | List past backtest runs for the user |

`POST /backtests` request:
```json
{
  "strategy": "ai-bias-ce-writing-below-vah",
  "instrument": "NIFTY50_OPTIONS",
  "from": "2026-01-01",
  "to": "2026-07-25",
  "initialCapital": 100000,
  "positionSizeLots": 3,
  "stopLossPct": 1.5,
  "targetPct": 3.0,
  "includeSlippageAndCosts": true
}
```

## 6. Reports

| Method | Path | Description |
|---|---|---|
| GET | `/reports/pnl-summary?period=mtd\|last-month\|fy` | Net/realized/unrealized P&L, charges breakdown |
| GET | `/reports/daily-pnl?period=` | Daily P&L series for the bar chart |
| GET | `/reports/documents?type=daily-pnl\|contract-note\|monthly-statement\|tax-report\|strategy-performance` | List available generated reports |
| GET | `/reports/documents/{id}/download` | Download a specific report (PDF/XLSX) |

## 7. Settings

| Method | Path | Description |
|---|---|---|
| GET / PUT | `/settings/profile` | Display name, email, client ID, trading segment |
| GET / PUT | `/settings/risk-limits` | Max daily loss, max positions, max lot size, max exposure, auto square-off, block-over-limit |
| GET / PUT | `/settings/notifications` | Per-event notification toggles |
| GET / PUT | `/settings/appearance` | Theme (`light`/`dark`/`terminal`), candle color scheme, default landing page — primarily client-side, but persisted server-side for cross-device sync |
| GET / PUT | `/settings/data-refresh` | Real-time ticks on/off, option chain refresh interval, extended-hours data |

## 8. Help / system status

| Method | Path | Description |
|---|---|---|
| GET | `/system/status` | Health of market data feed, order execution, AI signal engine, broker API — powers Help → System Status |
| GET | `/help/faq` | FAQ content (searchable) |
| POST | `/help/tickets` | Raise a support ticket |

## 9. WebSocket gateway

**Endpoint:** `wss://.../ws`, authenticated via a short-lived token obtained from `GET /ws/token`.

Client subscribes to channels by sending `{"subscribe": ["quote:NIFTY50", "option-chain:NIFTY50:2026-07-24", "orders", "positions", "signals"]}`. Server pushes typed messages:

```json
{ "channel": "quote:NIFTY50", "type": "tick", "data": { "ltp": 23532.45, "change": 182.60, "changePct": 0.78 } }
{ "channel": "orders", "type": "order-update", "data": { "orderId": "NE-8841203", "status": "EXECUTED" } }
{ "channel": "signals", "type": "new-signal", "data": { "...": "..." } }
```

## 10. Conventions

- All money values are decimal INR (paise-precision floats), not strings.
- All timestamps are ISO-8601 UTC; the frontend converts to IST for display.
- Pagination: `?page=&pageSize=`, response includes `{ "items": [...], "total": N }`.
- Errors: `{ "error": "SOME_CODE", "message": "human readable" }` with an appropriate 4xx/5xx status.
