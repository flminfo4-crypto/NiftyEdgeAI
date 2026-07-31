from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from broker_plugins.core.interface import OrderRequest

from app.models.schemas import OrderOut, OrderRequestIn
from app.services import order_service, risk_engine

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderOut])
def list_orders(status: str | None = None):
    return order_service.list_orders(status)


@router.post("", response_model=OrderOut, status_code=201)
def place_order(body: OrderRequestIn):
    order = OrderRequest(
        instrument=body.instrument,
        side=body.side,
        order_type=body.order_type,
        product=body.product,
        quantity_lots=body.quantity_lots,
        price=body.price,
        trigger_price=body.trigger_price,
    )
    try:
        record = order_service.place_order(order)
    except risk_engine.RiskLimitExceeded as exc:
        return JSONResponse(
            status_code=422,
            content={
                "error": "RISK_LIMIT_EXCEEDED",
                "message": exc.message,
                "limit": exc.limit,
                "currentValue": exc.current_value,
                "attemptedValue": exc.attempted_value,
            },
        )
    return record


@router.put("/{order_id}", response_model=OrderOut)
def modify_order(order_id: str, changes: dict):
    record = order_service.modify_order(order_id, changes)
    if not record:
        raise HTTPException(status_code=404, detail="Order not found or not modifiable")
    return record


@router.delete("/{order_id}", response_model=OrderOut)
def cancel_order(order_id: str):
    record = order_service.cancel_order(order_id)
    if not record:
        raise HTTPException(status_code=404, detail="Order not found or not cancellable")
    return record


@router.get("/margin-preview")
def margin_preview(instrument: str, side: str, quantity_lots: int, price: float | None = None):
    from app.services.broker import get_broker

    broker = get_broker()
    order = OrderRequest(
        instrument=instrument, side=side, order_type="LIMIT" if price else "MARKET",
        product="MIS", quantity_lots=quantity_lots, price=price,
    )
    reference_price = price or order_service._reference_price_for(instrument, broker)
    estimate = risk_engine.estimate_margin(order, reference_price)
    return {"marginRequired": estimate.required}
