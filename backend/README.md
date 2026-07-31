# backend/

The FastAPI backend — sits behind the API Gateway and in front of the Analytics Engine and Broker Adapter Layer. See `docs/Architecture/Architecture.md` for how this fits into the overall stack.

## Status

**Implemented (mock data, no auth yet):** market data, AI bias/signals, positions, orders (with risk-engine enforcement), backtests, and system status — enough to drive `frontend/index.html` end to end against `broker-plugins/mock`. See `docs/API/API.md` for the full target contract; auth (§1) and the WebSocket gateway (§9) are not built yet, and the datastore is in-memory (no Postgres/Redis wiring yet — see `docs/ERD/ERD.md` for the target data model).

### Run it

```bash
cd broker-plugins && pip install -e .
cd ../ai-engine && pip install -e .
cd ../backend && pip install -r requirements.txt
cp .env.example .env   # optional — defaults already work
uvicorn app.main:app --reload --port 8000
# docs at http://localhost:8000/api/v1/docs
```

## Two layers, one service

- **API layer** (`src/api/`) — FastAPI route handlers. Deliberately thin: request validation (Pydantic models), auth check, calling into the Business Service Layer, shaping the response. No business logic here.
- **Business Service Layer** (`src/services/`) — where the actual logic lives:
  - **Market data gateway** — normalizes and caches quotes, option chain, OI, and historical candles from the active `broker-plugins/` adapter (Dhan, Fyers, or Angel One); exposes them via the API layer over REST + WebSocket.
  - **Risk engine** — validates every order against the account's configured risk limits (max daily loss, max lot size, max exposure, etc.) *before* it's forwarded to a broker adapter. This is the one place a bad order gets stopped, so it needs the heaviest test coverage in the codebase.
  - **Orders & positions** — forwards validated orders to the active broker adapter, tracks position/P&L state.
  - **Strategy signals** — persists signals produced by the Analytics Engine (`ai-engine/`), exposes signal history/performance.
  - **Backtests** — job orchestration for `ai-engine/` backtest runs; stores results and trade logs.
  - **Reports** — generates/serves P&L statements, contract notes, tax reports.

## Layout (as built)

```
backend/
├── app/
│   ├── main.py           FastAPI app, CORS, router registration
│   ├── config.py           Settings (env-driven, mock-friendly defaults)
│   ├── api/                 Routers: market, signals, positions, orders, backtests, system
│   ├── services/            Business Service Layer: broker.py (adapter selection), market_data.py,
│   │                          risk_engine.py, order_service.py, signal_service.py, backtest_service.py
│   └── models/schemas.py    Pydantic request/response models (camelCase over the wire)
├── requirements.txt
└── .env.example
```

Not yet added: `auth/`, `ws/` (WebSocket gateway), `migrations/` (Alembic) — all pending real Postgres/Redis wiring and are called out as gaps above.

## Stack

**FastAPI** (async), **SQLAlchemy** + **Alembic** against **PostgreSQL**, **Redis** for caching/pub-sub (live quotes, option chain snapshots, WebSocket fan-out, rate limiting). Sits behind an **API Gateway** (auth/TLS/rate-limiting terminates there — see `infrastructure/README.md`), but the backend still verifies JWTs itself as defense in depth. Calls into `ai-engine/` in-process where practical (same language, avoids a network hop for latency-sensitive signal scoring) or as a separate service for heavier/backtest workloads.
