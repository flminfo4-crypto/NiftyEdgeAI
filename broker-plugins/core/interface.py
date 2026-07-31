"""
Common interface every broker-plugins/* adapter implements.

The Business Service Layer (backend/app/services) only ever talks to
this interface, never to a broker-specific SDK directly, so swapping
Dhan/Fyers/Angel One/mock in and out is a config change, not a rewrite.

See docs/Architecture/Architecture.md ("Broker Adapter Layer") and
docs/API/API.md for the shapes these methods are expected to return.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


class BrokerConnectionError(Exception):
    """Raised when auth/connect to the broker fails."""


class BrokerOrderError(Exception):
    """Raised when the broker rejects an order (distinct from a NiftyEdgeAI risk-limit rejection,
    which never reaches the broker at all)."""


@dataclass
class Quote:
    symbol: str
    ltp: float
    change: float
    change_pct: float
    as_of: datetime


@dataclass
class OptionChainRow:
    strike: float
    ce_oi: float
    ce_oi_change: float
    ce_volume: float
    ce_iv: float
    ce_ltp: float
    pe_oi: float
    pe_oi_change: float
    pe_volume: float
    pe_iv: float
    pe_ltp: float


@dataclass
class OptionChainSnapshot:
    underlying: str
    expiry: str
    as_of: datetime
    spot_price: float
    rows: list[OptionChainRow] = field(default_factory=list)


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OrderRequest:
    instrument: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"]
    product: Literal["MIS", "NRML"]
    quantity_lots: int
    price: Optional[float] = None
    trigger_price: Optional[float] = None


@dataclass
class OrderResult:
    broker_order_id: str
    status: Literal["PENDING", "EXECUTED", "REJECTED"]
    filled_price: Optional[float] = None
    rejection_reason: Optional[str] = None


@dataclass
class BrokerPosition:
    instrument: str
    side: Literal["LONG", "SHORT"]
    quantity_lots: int
    avg_price: float
    ltp: float


@dataclass
class Margins:
    used: float
    available: float


class BrokerAdapter(ABC):
    """Every broker-plugins/<broker>/adapter.py must implement this."""

    name: str = "unnamed"

    @abstractmethod
    def connect(self, credentials: dict) -> None:
        """Perform the auth handshake. Raises BrokerConnectionError on failure."""

    @abstractmethod
    def refresh_token(self) -> None:
        """Refresh/renew the session if the broker requires it (e.g. Angel One's daily TOTP expiry)."""

    @abstractmethod
    def get_quote(self, symbols: list[str]) -> list[Quote]:
        ...

    @abstractmethod
    def get_option_chain(self, underlying: str, expiry: str) -> OptionChainSnapshot:
        ...

    @abstractmethod
    def get_historical_candles(self, symbol: str, interval: str, frm: datetime, to: datetime) -> list[Candle]:
        ...

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    def modify_order(self, broker_order_id: str, changes: dict) -> OrderResult:
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> None:
        ...

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]:
        ...

    @abstractmethod
    def get_margins(self) -> Margins:
        ...
