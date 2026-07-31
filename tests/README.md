# tests/

Cross-cutting test suites. Package-local unit tests should live alongside their code (e.g. `backend/src/**/*.test.ts`); this folder is for tests that span multiple components or need a full running stack.

## Status

`integration/test_backend_api.py` — 13 tests against the live backend (via FastAPI's `TestClient`) and `broker-plugins/mock`: quotes, option chain, AI bias (verified to match the frontend's baked-in mock exactly), signals, positions/P&L, order placement (success + both risk-engine rejection paths — lot cap and exposure %), backtest determinism, and system status. Run with:

```bash
cd NiftyEdgeAI
pip install -e ./broker-plugins && pip install -e ./ai-engine && pip install -r backend/requirements.txt
python3 -m pytest tests/integration -v
```

`e2e/` and `ai-engine/` (beyond the determinism check folded into `integration/` for now) are still scaffold-only.

## Responsibilities

- **Integration tests** — backend API against a real (test) DB and the `mock/` broker plugin: order placement, position updates, risk-limit enforcement, signal persistence.
- **End-to-end tests** — drive `frontend/` against a running backend + mock broker (e.g. Playwright): log in, view dashboard, place a mock order, check it appears in Positions.
- **AI engine validation** — backtest determinism tests (same inputs → same outputs), and regression tests comparing bias/signal output against a fixed historical dataset so model changes don't silently flip behavior.
- **Contract tests** — verify each `broker-plugins/` adapter satisfies the shared interface and normalizes responses correctly (can run against broker sandbox/paper-trading endpoints where available).

## Suggested layout

```
tests/
├── integration/       backend + DB + mock broker
├── e2e/                 Playwright/Cypress specs against the full stack
├── ai-engine/           backtest determinism + signal regression fixtures
└── fixtures/            shared test data (sample option chains, historical candles, etc.)
```

## Notes

Given the risk-limit enforcement (`Settings → Risk Limits`) is meant to block orders that exceed configured exposure, that logic deserves test coverage before anything else — a bug there is the difference between a UI annoyance and a real account blowing past its loss limit.
