"""
Risk engine — the one place a bad order gets stopped, per backend/README.md
("this is the one place a bad order gets stopped, so it needs the heaviest
test coverage in the codebase"). Runs entirely server-side, before an order
ever reaches a broker adapter. A client cannot bypass this by, say, hiding
the "confirm" button — there is no client-side-only path to place an order.

Rules enforced (mock/dev defaults in app/config.py, real version would read
per-user limits from Settings → Risk Limits, see docs/API/API.md §7):
  - max exposure %  : projected (used + this order's margin) / total capital
  - max lots/order   : hard cap per single order, regardless of exposure
"""

from dataclasses import dataclass

from app.config import settings
from broker_plugins.core.interface import Margins, OrderRequest


class RiskLimitExceeded(Exception):
    def __init__(self, message: str, limit: str, current_value: float, attempted_value: float):
        super().__init__(message)
        self.message = message
        self.limit = limit
        self.current_value = current_value
        self.attempted_value = attempted_value


@dataclass
class MarginEstimate:
    required: float


def estimate_margin(order: OrderRequest, reference_price: float) -> MarginEstimate:
    """Rough mock margin model — not a real SPAN/exposure calculation.
    BUY (premium-paying) orders require the premium outlay; SELL (writing)
    orders require a leveraged margin block, since the loss is theoretically
    much larger than the premium received."""
    price = order.price or reference_price
    if order.side == "BUY":
        required = price * order.quantity_lots
    else:
        required = price * order.quantity_lots * 8.0  # mock leverage factor for writing
    return MarginEstimate(required=round(required, 2))


def check_order(order: OrderRequest, margins: Margins, margin_estimate: MarginEstimate) -> None:
    if order.quantity_lots > settings.max_lots_per_order:
        raise RiskLimitExceeded(
            message=f"Order quantity {order.quantity_lots} exceeds max lots per order of {settings.max_lots_per_order}.",
            limit="maxLotsPerOrder",
            current_value=0,
            attempted_value=order.quantity_lots,
        )

    total_capital = margins.used + margins.available
    if total_capital <= 0:
        return

    current_pct = margins.used / total_capital * 100
    projected_used = margins.used + margin_estimate.required
    attempted_pct = projected_used / (total_capital + margin_estimate.required) * 100

    if attempted_pct > settings.max_exposure_pct:
        raise RiskLimitExceeded(
            message=f"Order would exceed max exposure limit of {settings.max_exposure_pct:.0f}%.",
            limit="maxExposurePct",
            current_value=round(current_pct, 1),
            attempted_value=round(attempted_pct, 1),
        )
