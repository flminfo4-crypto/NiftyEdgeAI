"""
Backend configuration. Reads from environment (see .env.example) with
mock-friendly defaults so `uvicorn app.main:app` works out of the box
with zero setup — no Postgres, no Redis, no broker credentials required.

Swap BROKER_ADAPTER to "dhan" / "fyers" / "angel_one" once a real
broker-plugins/<broker>/adapter.py exists and credentials are configured;
everything else in the backend is written against the broker_plugins.core
interface, so this is the only line that changes.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = "NiftyEdgeAI Backend"
    api_prefix: str = "/api/v1"
    broker_adapter: str = os.getenv("BROKER_ADAPTER", "mock")
    cors_allow_origins: tuple = tuple(
        o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
    )
    max_exposure_pct: float = float(os.getenv("RISK_MAX_EXPOSURE_PCT", "60.0"))
    max_lots_per_order: int = int(os.getenv("RISK_MAX_LOTS_PER_ORDER", "300"))
    max_daily_loss: float = float(os.getenv("RISK_MAX_DAILY_LOSS", "25000.0"))
    default_underlying: str = "NIFTY50"
    default_expiry: str = os.getenv("DEFAULT_EXPIRY", "2026-07-31")


settings = Settings()
