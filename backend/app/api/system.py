import os
from datetime import datetime, timezone

from fastapi import APIRouter

from app.config import settings
from app.models.schemas import BrokerInfoOut, RiskLimitsOut, SystemStatusOut
from app.services.broker import get_broker

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatusOut)
def system_status():
    try:
        get_broker()
        broker_ok = True
    except Exception:
        broker_ok = False

    suffix = f"({settings.broker_adapter})"
    return SystemStatusOut(
        market_data_feed=f"OPERATIONAL {suffix}" if broker_ok else "DOWN",
        order_execution=f"OPERATIONAL {suffix}" if broker_ok else "DOWN",
        ai_signal_engine="OPERATIONAL",
        broker_api=f"OPERATIONAL {suffix}" if broker_ok else "DOWN",
        broker_adapter=settings.broker_adapter,
        as_of=datetime.now(timezone.utc),
    )


@router.get("/broker-info", response_model=BrokerInfoOut)
def broker_info():
    try:
        get_broker()
        connected = True
    except Exception:
        connected = False

    client_id_masked = None
    if settings.broker_adapter == "dhan":
        client_id = os.getenv("DHAN_CLIENT_ID", "")
        client_id_masked = ("*" * max(0, len(client_id) - 4) + client_id[-4:]) if client_id else None

    return BrokerInfoOut(
        broker_label=settings.broker_adapter.replace("_", " ").title(),
        client_id_masked=client_id_masked,
        connected=connected,
        last_sync_at=datetime.now(timezone.utc),
    )


@router.get("/risk-limits", response_model=RiskLimitsOut)
def risk_limits():
    return RiskLimitsOut(
        max_daily_loss=settings.max_daily_loss,
        max_lots_per_order=settings.max_lots_per_order,
        max_exposure_pct=settings.max_exposure_pct,
    )
