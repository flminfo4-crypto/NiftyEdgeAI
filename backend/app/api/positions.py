from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models.schemas import ClosedPositionOut, GreeksSummaryOut, MarginsOut, PositionOut
from app.services import analytics, market_data, order_service

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


@router.get("/closed", response_model=list[ClosedPositionOut])
def closed_positions():
    return order_service.get_closed_positions()


@router.get("/greeks", response_model=GreeksSummaryOut)
def position_greeks(underlying: str = "NIFTY50", expiry: str = settings.default_expiry):
    positions = order_service.list_open_positions()
    chain = market_data.get_option_chain(underlying, expiry)
    return analytics.portfolio_greeks_detail(positions, chain)
