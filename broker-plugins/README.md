# broker-plugins/

The Broker Adapter Layer — translates NiftyEdgeAI's internal order/market-data interface into each broker's specific API. This is what lets the Business Service Layer (`backend/`) stay broker-agnostic.

## Status

**Implemented:** `core/` (the shared `BrokerAdapter` interface), `mock/` (dev/demo adapter, no network), and `dhan/` (DhanHQ API v2: quotes, daily/intraday historical candles, option chain, orders, positions, fund limits). Set `BROKER_ADAPTER=dhan` plus `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` in `backend/.env` to activate it — see `backend/.env.example`. Known gap: order placement needs a security-ID lookup from Dhan's instrument master for option contracts (quotes/candles/chain work with the built-in index map). Still pending: **Fyers**, **Angel One**.

## Responsibilities

Each plugin implements a common interface, roughly:

- `connect(credentials)` / `refreshToken()` — auth handshake, session/token refresh.
- `getQuote(symbol)`, `getOptionChain(underlying, expiry)`, `getHistoricalCandles(symbol, interval, range)` — market data.
- `placeOrder(order)`, `modifyOrder(id, changes)`, `cancelOrder(id)` — order execution.
- `getPositions()`, `getOrderBook()`, `getMargins()` — account state.
- Normalizes every broker's response shape into NiftyEdgeAI's internal types (see `docs/ERD/ERD.md`), so the Business Service Layer never branches on which broker is active.

## Suggested layout

```
broker-plugins/
├── core/                shared interface/types every plugin implements
├── dhan/                   Dhan adapter (REST + WebSocket, API-key auth)
├── fyers/                   Fyers adapter (REST + WebSocket, OAuth)
├── angel-one/                Angel One SmartAPI adapter (TOTP login, daily token expiry)
└── mock/                       a fake broker for local dev / demos (returns the same dummy data the frontend currently hardcodes)
```

## Per-broker notes

| Broker | Auth model | Notes |
|---|---|---|
| Dhan | API key + access token | Straightforward key-based auth; token has a defined validity window to track |
| Fyers | OAuth 2.0 | Needs a browser-based consent redirect on first connect, then refresh-token handling |
| Angel One (SmartAPI) | TOTP + client credentials | Tokens expire daily — the adapter needs to surface a "reconnect required" state cleanly rather than silently failing; Settings already has a "Regenerate Token" action for this flow |

## Notes

- Start with `mock/` — it lets `backend/` and `frontend/` be developed and demoed end-to-end before a real broker integration exists, and it's a natural source for the dummy data already baked into the frontend prototype.
- Real broker integrations need care around rate limits, token expiry/refresh (all three brokers above use expiring tokens requiring a re-login flow), and paper-trading/sandbox modes for safe development.
- v1 supports one connected broker account per user at a time; the adapter registry is built so adding a broker or supporting concurrent multi-broker accounts later is additive, not a rearchitecture.
