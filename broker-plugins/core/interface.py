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
    # Greeks (rho isn't in Dhan's /optionchain response — stays 0.0 for that adapter)
    # and each leg's previous-session close LTP, both defaulted so existing callers
    # and the mock adapter (which predates these fields) don't need to change.
    ce_delta: float = 0.0
    ce_gamma: float = 0.0
    ce_theta: float = 0.0
    ce_vega: float = 0.0
    ce_rho: float = 0.0
    ce_prev_ltp: float = 0.0
    pe_delta: float = 0.0
    pe_gamma: float = 0.0
    pe_theta: float = 0.0
    pe_vega: float = 0.0
    pe_rho: float = 0.0
    pe_prev_ltp: float = 0.0


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


@dataclass
class TradeHistoryEntry:
    """A single executed fill, for closed-position matching and P&L reporting
    (see app/services/analytics.py match_closed_trades/sum_charges). No broker
    adapter implements a trade-history call yet — order_service.get_trade_history()
    builds this from the app's own in-memory order records instead."""

    instrument: str
    side: Literal["BUY", "SELL"]
    quantity: int
    traded_price: float
    traded_at: datetime
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_charges: float = 0.0
    gst: float = 0.0
    sebi_stamp_duty: float = 0.0


@dataclass
class MarketBreadth:
    """Advance/decline breadth over whatever constituent universe the adapter
    can actually price (see broker_plugins/dhan/adapter.py — Dhan has no
    "index constituents" API, so this is a maintained snapshot of NIFTY 50
    symbols, not a live feed from an index provider)."""

    advancing: int
    declining: int
    unchanged: int
    new_highs: int
    new_lows: int
    universe_size: int
    universe_label: str


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
    def get_expiry_list(self, underlying: str) -> list[str]:
        """ISO-date (YYYY-MM-DD) expiries actually listed for this underlying —
        callers must not invent expiry dates, since option-chain endpoints
        reject anything not on the real instrument's expiry calendar."""

    @abstractmethod
    def get_historical_candles(self, symbol: str, interval: str, frm: datetime, to: datetime) -> list[Candle]:
        ...

    @abstractmethod
    def get_market_breadth(self) -> MarketBreadth:
        ...

    @abstractmethod
    def get_universe_symbols(self) -> list[str]:
        """Symbols this adapter can resolve for per-stock scans (e.g. NIFTY 50
        constituents) — callers loop get_historical_candles() over these
        rather than assuming any arbitrary stock symbol resolves."""

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
