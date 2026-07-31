"""
NiftyEdgeAI backend entrypoint. Run with:

    uvicorn app.main:app --reload --port 8000

See backend/README.md and .env.example for configuration. With no BROKER_ADAPTER
set, this runs entirely against broker-plugins/mock — no credentials, no live
market data, and no real orders are ever placed. Interactive docs at /docs.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import backtests, market, orders, positions, signals, system
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "NiftyEdgeAI backend — implements the subset of docs/API/API.md needed to "
        "drive the frontend/ terminal end to end using broker-plugins/mock. "
        "Auth (§1) and WebSocket gateway (§9) are not implemented in this pass; "
        "all endpoints below are open (no auth) until that lands."
    ),
    openapi_url=f"{settings.api_prefix}/openapi.json",
    docs_url=f"{settings.api_prefix}/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allow_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router, prefix=settings.api_prefix)
app.include_router(signals.router, prefix=settings.api_prefix)
app.include_router(positions.router, prefix=settings.api_prefix)
app.include_router(orders.router, prefix=settings.api_prefix)
app.include_router(backtests.router, prefix=settings.api_prefix)
app.include_router(system.router, prefix=settings.api_prefix)


@app.get("/")
def root():
    return {
        "name": settings.app_name,
        "status": "ok",
        "brokerAdapter": settings.broker_adapter,
        "docs": settings.api_prefix + "/docs",
    }
