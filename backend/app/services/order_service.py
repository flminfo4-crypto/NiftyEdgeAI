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
from datetime import datetime, timedelta, timezone
from typing import Optional

from broker_plugins.core.interface import BrokerPosition, OrderRequest, OrderResult, TradeHistoryEntry

from app.services import analytics, risk_engine
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


# -- trade history / closed positions / reports --------------------------------------
#
# No broker adapter implements a real trade-history/contract-note call yet, so
# "trades" here means this app's own EXECUTED orders (_orders above) — real once
# real orders are placed through this app, empty/zero until then. That's a
# disclosed limitation (same as _orders itself), not fabricated data.


def _estimate_charges(side: str, turnover: float) -> dict:
    """Approximate NSE F&O (index options) charge model — flat brokerage capped
    at Rs20/order, STT only on the sell side, standard exchange/GST/stamp-duty
    rates. Not a real DhanHQ contract note (no such API is wired up); same
    "documented approximation" spirit as risk_engine.estimate_margin."""
    brokerage = min(20.0, turnover * 0.0003)
    exchange_charges = round(turnover * 0.0003503, 2)
    stt = round(turnover * 0.0005, 2) if side == "SELL" else 0.0
    sebi_stamp_duty = round(turnover * 0.00003, 2) if side == "BUY" else round(turnover * 0.000001, 2)
    gst = round((brokerage + exchange_charges) * 0.18, 2)
    return {
        "brokerage": round(brokerage, 2),
        "stt": stt,
        "exchange_charges": exchange_charges,
        "gst": gst,
        "sebi_stamp_duty": sebi_stamp_duty,
    }


def get_trade_history() -> list[TradeHistoryEntry]:
    entries = []
    for o in _orders.values():
        if o["status"] != "EXECUTED":
            continue
        price = o["filled_price"] or o["price"] or 0.0
        turnover = price * o["quantity_lots"]
        entries.append(TradeHistoryEntry(
            instrument=o["instrument"], side=o["side"], quantity=o["quantity_lots"],
            traded_price=price, traded_at=o["placed_at"], **_estimate_charges(o["side"], turnover),
        ))
    return entries


def get_closed_positions() -> list[dict]:
    return analytics.match_closed_trades(get_trade_history())


def get_report_summary(from_date: str, to_date: str) -> dict:
    frm = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
    to = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc) + timedelta(days=1)
    trades = [t for t in get_trade_history() if frm <= t.traded_at < to]
    closed = analytics.match_closed_trades(trades)
    realized_pnl = round(sum(c["pnl"] for c in closed), 2)

    unrealized_pnl = round(sum(position_pnl(p)[0] for p in list_open_positions()), 2)

    daily_pnl = analytics.daily_realized_pnl(closed)
    winning_days = sum(1 for d in daily_pnl if d["pnl"] > 0)
    losing_days = sum(1 for d in daily_pnl if d["pnl"] < 0)

    return {
        "net_pnl": round(realized_pnl + unrealized_pnl, 2),
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "charges": analytics.sum_charges(trades),
        "winning_days": winning_days,
        "losing_days": losing_days,
        "daily_pnl": daily_pnl,
    }
