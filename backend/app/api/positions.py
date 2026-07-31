from fastapi import APIRouter, HTTPException

from app.models.schemas import MarginsOut, PositionOut
from app.services import order_service

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("/open", response_model=list[PositionOut])
def open_positions():
    positions = order_service.list_open_positions()
    out = []
    for p in positions:
        pnl, pnl_pct = order_service.position_pnl(p)
        out.append(
            PositionOut(
                instrument=p.instrument,
                side=p.side,
                quantity_lots=p.quantity_lots,
                avg_price=p.avg_price,
                ltp=p.ltp,
                pnl=pnl,
                pnl_pct=pnl_pct,
            )
        )
    return out


@router.get("/margins", response_model=MarginsOut)
def margins():
    return order_service.get_margins()


@router.post("/{instrument}/exit")
def exit_position(instrument: str):
    positions = order_service.list_open_positions()
    if not any(p.instrument == instrument for p in positions):
        raise HTTPException(status_code=404, detail="Position not found")
    # Mock: squaring off isn't modeled as a separate broker call yet — the
    # mock adapter has no persistent position store to mutate. Documented
    # as a known gap; real adapters will place an opposing MARKET order here.
    return {"instrument": instrument, "status": "SQUARED_OFF"}


@router.post("/exit-all")
def exit_all():
    positions = order_service.list_open_positions()
    return {"count": len(positions), "status": "SQUARED_OFF"}
