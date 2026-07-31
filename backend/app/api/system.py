from datetime import datetime, timezone

from fastapi import APIRouter

from app.config import settings
from app.models.schemas import SystemStatusOut
from app.services.broker import get_broker

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatusOut)
def system_status():
    # With BROKER_ADAPTER=mock there is no live feed (see README "Project status") —
    # everything reports OPERATIONAL against the mock adapter, so this endpoint's
    # *shape* is proven out even though there's no real infra behind it yet.
    try:
        get_broker()
        broker_ok = True
    except Exception:
        broker_ok = False

    return SystemStatusOut(
        market_data_feed="OPERATIONAL (mock)" if broker_ok else "DOWN",
        order_execution="OPERATIONAL (mock)" if broker_ok else "DOWN",
        ai_signal_engine="OPERATIONAL",
        broker_api="OPERATIONAL (mock)" if broker_ok else "DOWN",
        broker_adapter=settings.broker_adapter,
        as_of=datetime.now(timezone.utc),
    )
