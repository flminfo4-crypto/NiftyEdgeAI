# infrastructure/

Infrastructure-as-code and environment configuration for running NiftyEdgeAI outside a laptop.

## Status

Scaffold only — no code yet.

## Responsibilities

- **API Gateway** provisioning — the single public entry point in front of the FastAPI backend (per `docs/Architecture/Architecture.md`): TLS termination, JWT auth check, rate limiting, routing to the backend's REST and WebSocket routes. Managed (AWS API Gateway/ALB) or self-hosted (Kong/NGINX) — pick one and document the decision here once made.
- Environment definitions (dev / staging / prod) and the secrets/config each needs (broker API keys for Dhan/Fyers/Angel One, DB URLs, etc. — never committed in plaintext).
- Database provisioning (PostgreSQL for core data, Redis for caching/pub-sub) and backup policy.
- Networking/security: everything behind the API Gateway (`backend/`, `ai-engine/`) is private; only the gateway is public. TLS termination happens at the gateway.
- Observability: logging, metrics, and alerting hooks (especially around order placement failures, broker API errors, and risk-limit breaches — this is a trading app, so silent failures here are unacceptable).

## Suggested layout

```
infrastructure/
├── terraform/        (or Pulumi/CDK) cloud resources: API Gateway, compute, Postgres, Redis, networking
├── docker/             Dockerfiles for backend (FastAPI)/ai-engine/frontend (React build), docker-compose for local dev
├── env/                 .env.example per environment (never real secrets)
└── monitoring/          dashboards/alert rules (e.g. Grafana + Prometheus, or a hosted APM)
```

## Notes

`docker-compose.yml` for local dev (backend + ai-engine + Postgres + Redis + the static frontend) is the highest-value first artifact here — it's what makes the rest of the team able to run the full stack locally before any cloud infra exists.
