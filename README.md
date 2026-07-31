# NiftyEdgeAI

NiftyEdgeAI is an AI-assisted options trading terminal for the Indian derivatives market (NIFTY 50, BANKNIFTY, SENSEX, and single-stock F&O). It combines market/volume profile analytics, order-flow (footprint) charts, options chain and Greeks, an AI market-bias engine, strategy backtesting, and live order/position management into a single desktop-first web terminal.

This repository is the project workspace: product docs, the frontend terminal, and stubs for the backend, AI engine, broker integrations, infrastructure, deployment, and test suites that will sit behind it.

## Project status

The **frontend UI** (`frontend/`) is built out as a static HTML/CSS/JS prototype with dummy data — 15 pages, three switchable themes (Light / Dark / Terminal), and a full component library. It is the visual and functional reference the rest of the system is being built against, and the spec for the target React implementation.

Everything else (`backend/`, `ai-engine/`, `broker-plugins/`, `infrastructure/`, `deployment/`, `tests/`) is currently a **scaffold**: folder structure and a README describing its intended responsibility, ready for implementation.

## Stack (decided)

`React` (frontend) → `API Gateway` → `FastAPI` backend → `Business Service Layer` → `Analytics Engine` (ai-engine) → `Broker Adapter Layer` → `Dhan / Fyers / Angel One` → `PostgreSQL + Redis`. Full detail and diagrams in `docs/Architecture/Architecture.md`.

## Structure

```
NiftyEdgeAI/
│
├── docs/                 Product & engineering documentation
│   ├── SRS/              Software Requirements Specification
│   ├── Architecture/      System architecture & data flow
│   ├── API/               REST API specification
│   ├── ERD/                Data model / entity relationship diagram
│   ├── Wireframes/         UI reference (points at frontend/)
│   └── User Guide/         End-user walkthrough of the terminal
│
├── frontend/              Trading terminal UI (HTML/CSS/JS prototype today)
├── backend/                Core API service (auth, orders, positions, market data proxy)
├── ai-engine/              Market bias model, strategy signal generation, backtester engine
├── broker-plugins/         Broker adapters (Zerodha Kite Connect, Upstox, Angel One, ...)
├── infrastructure/         IaC, data feed configuration, environment definitions
├── deployment/             CI/CD pipelines, release configuration
└── tests/                  Unit / integration / e2e test suites
```

## Where to start

- Read `docs/SRS/SRS.md` for what the product needs to do.
- Read `docs/Architecture/Architecture.md` for how the pieces fit together.
- Open `frontend/index.html` in a browser to see the terminal itself.
- Each subfolder's `README.md` explains what belongs there and suggested next steps.

## Disclaimer

Educational/prototype project. Nothing here is investment advice, and no component is currently connected to a live broker or real funds.
