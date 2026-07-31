"""
Pydantic request/response models. Field names and JSON shapes follow
docs/API/API.md (camelCase over the wire via `alias`, snake_case in Python).
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(field: str) -> str:
    head, *tail = field.split("_")
    return head + "".join(w.capitalize() for w in tail)


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)


# -- market data --------------------------------------------------------------

class QuoteOut(CamelModel):
    symbol: str
    ltp: float
    change: float
    change_pct: float
    as_of: datetime


class OptionChainRowOut(CamelModel):
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
    ce_delta: float = 0.0
    ce_gamma: float = 0.0
    ce_theta: float = 0.0
    ce_vega: float = 0.0
    ce_rho: float = 0.0
    pe_delta: float = 0.0
    pe_gamma: float = 0.0
    pe_theta: float = 0.0
    pe_vega: float = 0.0
    pe_rho: float = 0.0


class OptionChainOut(CamelModel):
    underlying: str
    expiry: str
    as_of: datetime
    spot_price: float
    rows: list[OptionChainRowOut]


class CandleOut(CamelModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class OiSummaryOut(CamelModel):
    pcr: float
    max_pain: float


class OiBuildupRowOut(CamelModel):
    strike: float
    ce_oi_change: float
    ce_ltp_change_pct: float
    ce_signal: str
    pe_oi_change: float
    pe_ltp_change_pct: float
    pe_signal: str


class VolumeProfileRowOut(CamelModel):
    price: float
    volume: float


class VolumeProfileOut(CamelModel):
    rows: list[VolumeProfileRowOut]
    vah: float
    val: float
    poc: float
    total_volume: float


class MarketProfileOut(CamelModel):
    vah: float
    val: float
    poc: float


class CprDashboardOut(CamelModel):
    ltp: float
    change: float
    change_pct: float
    cpr_width_label: str
    cpr_width_pct: float
    cpr_relationship: str
    day_range: float
    tc: float
    pivot: float
    bc: float
    r1: float
    r2: float
    s1: float
    s2: float
    pdh: float
    pdl: float


class MarketBreadthOut(CamelModel):
    advancing: int
    declining: int
    unchanged: int
    new_highs: int
    new_lows: int
    universe_size: int
    universe_label: str


class IvRankOut(CamelModel):
    iv_rank: float
    iv_percentile: float
    current_iv: float
    history: list[float]


class CvdPointOut(CamelModel):
    ts: str
    cvd: float


class CvdOut(CamelModel):
    points: list[CvdPointOut]
    cumulative: float


# -- AI bias & signals ----------------------------------------------------------

class BiasFactorOut(CamelModel):
    key: str
    label: str
    value: str
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]


class BiasOut(CamelModel):
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    headline: str
    subtext: str
    confidence_pct: int
    score: float
    factors: list[BiasFactorOut]


class SignalOut(CamelModel):
    action: str
    instrument: str
    entry_zone: str
    target: str
    stop_loss: str
    confidence_pct: int
    generated_at: datetime
    reasoning: list[str]


class SignalHistoryRowOut(CamelModel):
    when: str
    signal: str
    confidence_pct: int
    entry: str
    target: str
    stop_loss: str
    result: str
    pnl: float


class ActiveSignalsOut(CamelModel):
    bias: BiasOut
    primary: SignalOut
    alternative: SignalOut


class StrategyStatOut(CamelModel):
    strategy: str
    hit_rate_pct: float
    sample_size: int


class ConfidenceCalibrationOut(CamelModel):
    confidence_range: str
    hit_rate_pct: float
    sample_size: int


class SignalStatsOut(CamelModel):
    today_hit_rate_pct: float
    hit_rate_pct: float
    avg_risk_reward: float
    resolved_count: int
    open_count: int
    by_strategy: list[StrategyStatOut]
    confidence_calibration: list[ConfidenceCalibrationOut]


# -- positions & orders --------------------------------------------------------

class PositionOut(CamelModel):
    instrument: str
    side: Literal["LONG", "SHORT"]
    quantity_lots: int
    avg_price: float
    ltp: float
    pnl: float
    pnl_pct: float


class MarginsOut(CamelModel):
    used: float
    available: float


class ClosedPositionOut(CamelModel):
    instrument: str
    side: Literal["BUY", "SELL"]
    quantity: int
    entry_price: float
    exit_price: float
    pnl: float
    closed_at: datetime


class PositionGreeksRowOut(CamelModel):
    instrument: str
    side: Literal["LONG", "SHORT"]
    quantity_lots: int
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    position_delta: float
    position_gamma: float
    position_theta: float
    position_vega: float
    position_rho: float


class GreeksSummaryOut(CamelModel):
    positions: list[PositionGreeksRowOut]
    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float
    net_rho: float


class OrderRequestIn(CamelModel):
    instrument: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"] = Field(alias="orderType")
    product: Literal["MIS", "NRML"]
    quantity_lots: int = Field(alias="quantityLots")
    price: Optional[float] = None
    trigger_price: Optional[float] = Field(default=None, alias="triggerPrice")


class OrderOut(CamelModel):
    order_id: str
    broker_order_id: Optional[str] = None
    status: Literal["PENDING", "EXECUTED", "REJECTED", "CANCELLED"]
    instrument: str
    side: str
    order_type: str
    product: str
    quantity_lots: int
    price: Optional[float] = None
    filled_price: Optional[float] = None
    margin_required: Optional[float] = None
    placed_at: datetime


class RiskRejectionOut(CamelModel):
    error: str = "RISK_LIMIT_EXCEEDED"
    message: str
    limit: str
    current_value: float
    attempted_value: float


class BrokerRejectionOut(CamelModel):
    error: str = "BROKER_REJECTED"
    message: str


# -- backtests -------------------------------------------------------------------

class BacktestRequestIn(CamelModel):
    strategy: str = "ai-bias-ce-writing-below-vah"
    instrument: str = "NIFTY50_OPTIONS"
    from_: str = Field(alias="from", default="2026-01-01")
    to: str = "2026-07-25"
    initial_capital: float = Field(alias="initialCapital", default=100_000.0)
    position_size_lots: int = Field(alias="positionSizeLots", default=3)
    stop_loss_pct: float = Field(alias="stopLossPct", default=1.5)
    target_pct: float = Field(alias="targetPct", default=3.0)
    include_slippage_and_costs: bool = Field(alias="includeSlippageAndCosts", default=True)


class BacktestTradeOut(CamelModel):
    opened_at: datetime
    closed_at: datetime
    label: str
    pnl: float
    result: str


class BacktestResultOut(CamelModel):
    job_id: str
    status: Literal["QUEUED", "RUNNING", "COMPLETE", "FAILED"]
    starting_capital: float
    net_profit: float
    net_profit_pct: float
    win_rate_pct: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    volatility_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    equity_curve: list[float]


# -- CPR / pivot levels (docs/PivotBoss-Roadmap.md, docs/API/API.md §2) -----------

class FloorPivotsOut(CamelModel):
    pivot: float
    r1: float
    r2: float
    r3: float
    r4: float
    s1: float
    s2: float
    s3: float
    s4: float


class CprOut(CamelModel):
    tc: float
    pivot: float
    bc: float
    width: float
    width_pct: float


class CamarillaOut(CamelModel):
    h4: float
    h3: float
    h2: float
    h1: float
    l1: float
    l2: float
    l3: float
    l4: float


class WidthForecastOut(CamelModel):
    regime: Literal["NARROW", "NORMAL", "WIDE"]
    forecast: str


class TwoDayRelationshipOut(CamelModel):
    relationship: str
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    label: str
    description: str


class CprLevelsOut(CamelModel):
    underlying: str
    session_date: str
    source: Literal["mock", "broker"] = "mock"
    floor: FloorPivotsOut
    cpr: CprOut
    camarilla: CamarillaOut
    width: WidthForecastOut
    two_day: Optional[TwoDayRelationshipOut] = None
    pdh: float
    pdl: float
    pdc: float


class CprClusterOut(CamelModel):
    lower: float
    upper: float


class CprTradePlanOut(CamelModel):
    bias: Literal["Bullish", "Bearish", "Range-Bound", "No-Trade"]
    side: Optional[Literal["LONG", "SHORT"]] = None
    entry_trigger: str
    stop_loss: Optional[float] = None
    target1: Optional[float] = None
    target2: Optional[float] = None


class TopNarrowStockOut(CamelModel):
    symbol: str
    width_pct: float
    regime: Literal["NARROW", "NORMAL", "WIDE"]
    close: float


class TopNarrowStocksOut(CamelModel):
    source: Literal["mock", "broker"] = "mock"
    stocks: list[TopNarrowStockOut]


class CprAnalysisOut(CamelModel):
    underlying: str
    session_date: str
    source: Literal["mock", "broker"] = "mock"
    floor: FloorPivotsOut
    cpr: CprOut
    camarilla: CamarillaOut
    width: WidthForecastOut
    two_day: Optional[TwoDayRelationshipOut] = None
    pdh: float
    pdl: float
    pdc: float
    current_ltp: Optional[float] = None
    percentile_regime: Optional[Literal["NARROW", "NORMAL", "WIDE"]] = None
    percentile_rank: Optional[float] = None
    p20_threshold: Optional[float] = None
    p70_threshold: Optional[float] = None
    consecutive_narrow_flag: bool = False
    support_cluster: CprClusterOut
    resistance_cluster: CprClusterOut
    trade_plan: CprTradePlanOut


# -- reports ---------------------------------------------------------------------

class ChargesOut(CamelModel):
    brokerage: float
    stt: float
    exchange_charges: float
    gst: float
    sebi_stamp_duty: float
    total: float


class DailyPnlOut(CamelModel):
    date: str
    pnl: float


class ReportSummaryOut(CamelModel):
    net_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    charges: ChargesOut
    winning_days: int
    losing_days: int
    daily_pnl: list[DailyPnlOut]


# -- system ------------------------------------------------------------------------

class SystemStatusOut(CamelModel):
    market_data_feed: str
    order_execution: str
    ai_signal_engine: str
    broker_api: str
    broker_adapter: str
    as_of: datetime


class BrokerInfoOut(CamelModel):
    broker_label: str
    client_id_masked: Optional[str] = None
    connected: bool
    last_sync_at: datetime


class RiskLimitsOut(CamelModel):
    max_daily_loss: float
    max_lots_per_order: int
    max_exposure_pct: float
