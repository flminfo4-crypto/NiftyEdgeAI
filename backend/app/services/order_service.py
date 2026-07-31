"""
Orders & positions service. Sequence for placing an order, matching the
architecture's "risk engine before broker forward" rule:

  API layer (Pydantic-validated request)
    -> risk_engine.check_order()   [raises RiskLimitExceeded -> API returns 422]
    -> broker.place_order()         [broker-side rejection -> status REJECTED, still 201]
    -> in-memory order store         [so GET /orders reflects it immediately]

In-memory store is a stand-in for the Postgres `ORDER` table in docs/ERD/ERD.md
until the backend is wired to a real database (see backend/README.md).
"""

import itertools
from datetime import datetime, timezone
from typing import Optional

from broker_plugins.core.interface import BrokerPosition, OrderRequest, OrderResult

from app.services import risk_engine
from app.services.broker import get_broker

_order_id_seq = itertools.count(8_841_200)
_orders: dict[str, dict] = {}


def _next_order_id() -> str:
    return f"NE-{next(_order_id_seq)}"


def place_order(order: OrderRequest) -> dict:
    broker = get_broker()
    margins = broker.get_margins()

    reference_price = order.price or _reference_price_for(order.instrument, broker)
    margin_estimate = risk_engine.estimate_margin(order, reference_price)

    # raises RiskLimitExceeded, handled by the API layer -> 422
    risk_engine.check_order(order, margins, margin_estimate)

    result: OrderResult = broker.place_order(order)

    order_id = _next_order_id()
    record = {
        "order_id": order_id,
        "broker_order_id": result.broker_order_id,
        "status": result.status,
        "instrument": order.instrument,
        "side": order.side,
        "order_type": order.order_type,
        "product": order.product,
        "quantity_lots": order.quantity_lots,
        "price": order.price,
        "filled_price": result.filled_price,
        "margin_required": margin_estimate.required,
        "placed_at": datetime.now(timezone.utc),
        "rejection_reason": result.rejection_reason,
    }
    _orders[order_id] = record
    return record


def _reference_price_for(instrument: str, broker) -> float:
    # Cheap heuristic: pull the LTP for the strike/side implied by the instrument
    # name out of the option chain, so market orders get a sane margin estimate.
    chain = broker.get_option_chain("NIFTY50", "2026-07-31")
    for row in chain.rows:
        strike_str = f"{row.strike:g}"
        if strike_str + "CE" in instrument.upper():
            return row.ce_ltp
        if strike_str + "PE" in instrument.upper():
            return row.pe_ltp
    return chain.spot_price


def list_orders(status: Optional[str] = None) -> list[dict]:
    orders = list(_orders.values())
    if status:
        orders = [o for o in orders if o["status"].lower() == status.lower()]
    return sorted(orders, key=lambda o: o["placed_at"], reverse=True)


def get_order(order_id: str) -> Optional[dict]:
    return _orders.get(order_id)


def cancel_order(order_id: str) -> Optional[dict]:
    record = _orders.get(order_id)
    if not record or record["status"] != "PENDING":
        return None
    get_broker().cancel_order(record["broker_order_id"])
    record["status"] = "CANCELLED"
    return record


def modify_order(order_id: str, changes: dict) -> Optional[dict]:
    record = _orders.get(order_id)
    if not record or record["status"] != "PENDING":
        return None
    get_broker().modify_order(record["broker_order_id"], changes)
    record.update({k: v for k, v in changes.items() if k in record})
    return record


# -- positions --------------------------------------------------------------------

def list_open_positions() -> list[BrokerPosition]:
    return get_broker().get_positions()


def get_margins():
    return get_broker().get_margins()


def position_pnl(position: BrokerPosition) -> tuple[float, float]:
    lot_size = 1  # broker_plugins.mock already reports quantity in shares, not raw lots
    direction = 1 if position.side == "LONG" else -1
    pnl = (position.ltp - position.avg_price) * position.quantity_lots * direction
    pnl_pct = (pnl / (position.avg_price * position.quantity_lots)) * 100 if position.avg_price else 0.0
    return round(pnl, 2), round(pnl_pct, 2)
