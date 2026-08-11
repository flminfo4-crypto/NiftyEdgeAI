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

class StrategyOut(CamelModel):
    key: str
    label: str
    description: str = ""


# -- user-managed strategy configs (the Strategies page) -------------------------

class StrategyTemplateParamOut(CamelModel):
    name: str
    type: Literal["int", "float", "bool"]
    default: float | int | bool
    label: str
    min: Optional[float] = None
    max: Optional[float] = None


class StrategyTemplateOut(CamelModel):
    template: str
    label: str
    description: str
    params: list[StrategyTemplateParamOut]


class StrategyConfigOut(CamelModel):
    key: str
    label: str
    description: str = ""
    is_builtin: bool
    active: bool
    template: Optional[str] = None
    params: Optional[dict] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StrategyCreateIn(CamelModel):
    label: str
    template: str
    params: dict = {}
    active: bool = True


class StrategyUpdateIn(CamelModel):
    label: Optional[str] = None
    params: Optional[dict] = None
    active: Optional[bool] = None


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
    # How long each position is carried, independent of the date range:
    # "strategy" keeps each strategy's own choice, the rest force one horizon.
    hold: Literal["strategy", "intraday", "weekly", "custom"] = "strategy"
    hold_days: int = Field(alias="holdDays", default=5, ge=1, le=60)


class BacktestTradeOut(CamelModel):
    opened_at: datetime
    closed_at: datetime
    label: str
    pnl: float
    result: str


class PeriodRowOut(CamelModel):
    period: str
    start_date: str
    end_date: str
    trades: int
    wins: int
    losses: int
    pnl: float
    return_pct: float
    opening_equity: float
    closing_equity: float
    hit_target: bool


class PeriodSummaryOut(CamelModel):
    total_periods: int
    periods_with_trades: int
    periods_flat: int
    periods_profitable: int
    periods_hit_target: int
    pct_hit_target: float
    pct_profitable: float
    avg_return_pct: float
    median_return_pct: float
    best_period_pct: float
    worst_period_pct: float
    max_consecutive_losing_periods: int
    final_equity: float
    total_return_pct: float


class PeriodReportOut(CamelModel):
    strategy: str
    strategy_label: str
    instrument: str
    underlying: str
    period: Literal["weekly", "monthly"]
    years: float
    from_date: str
    to_date: str
    starting_capital: float
    position_size_lots: int
    target_return_pct: float
    summary: PeriodSummaryOut
    rows: list[PeriodRowOut]


class WeeklyStatsOut(CamelModel):
    """Per-week performance of a backtest — the unit the app's 1-2%/week
    goal is judged on. Computed against the equity at each week's start."""
    total_weeks: int
    avg_return_pct: float
    median_return_pct: float
    pct_weeks_profitable: float
    pct_weeks_hit_1pct: float
    pct_weeks_hit_2pct: float
    best_week_pct: float
    worst_week_pct: float
    max_consecutive_losing_weeks: int


class IvRealityCheckOut(CamelModel):
    available: bool
    model_sigma_pct: Optional[float] = None
    real_market_iv_pct: Optional[float] = None
    delta_pct: Optional[float] = None


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
    weekly: Optional[WeeklyStatsOut] = None
    iv_reality_check: Optional[IvRealityCheckOut] = None


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


class TpoRowOut(CamelModel):
    price: float
    letters: str
    count: int
    is_poc: bool
    in_value_area: bool
    is_single_print: bool


class TpoPreviousSessionOut(CamelModel):
    relationship: str
    bias: str
    overlap_pct: float
    poc_migration: float
    prev_vah: float
    prev_val: float
    prev_poc: float
    prev_session_date: str


class VirginPocOut(CamelModel):
    date: str
    poc: float
    sessions_ago: int
    distance: float
    above: bool


class VirginPocsOut(CamelModel):
    underlying: str
    as_of_session: str
    current_price: float
    sessions_scanned: int
    virgin_pocs: list[VirginPocOut]


class TpoProfileOut(CamelModel):
    """Full TPO Market Profile for one session — letter grid plus the
    structural reads (value area, Initial Balance, extension, day type)."""
    session_date: str
    bracket_minutes: int
    brackets: int
    tick: float
    rows: list[TpoRowOut]
    poc: float
    vah: float
    val: float
    day_high: float
    day_low: float
    day_range: float
    ib_high: float
    ib_low: float
    ib_range: float
    range_extension_up: float
    range_extension_down: float
    single_prints: list[float]
    poor_high: bool
    poor_low: bool
    open_price: float
    close_price: float
    day_type: str
    reasoning: str
    bias: str
    open_type: str = "Unclassified"
    open_reasoning: str = ""
    selling_tail: list[float] = []
    buying_tail: list[float] = []
    virgin_pocs: list[VirginPocOut] = []
    previous_session: Optional[TpoPreviousSessionOut] = None


class TpoProfileCompositeSessionOut(CamelModel):
    session_date: str
    rows: list[TpoRowOut]
    vah: float
    val: float
    poc: float
    ib_high: float
    ib_low: float
    ib_range: float
    session_high: float
    session_low: float
    poor_high: Optional[float] = None
    poor_low: Optional[float] = None
    single_prints: list[float] = []
    volume: float
    vol_ma: float
    vol_ma_window: int
    structure_label: Optional[str] = None


class VolumeProfileCompositeSessionOut(CamelModel):
    session_date: str
    rows: list[VolumeProfileRowOut]
    vah: float
    val: float
    poc: float
    session_high: float
    session_low: float
    total_volume: float
    vol_ma: float
    vol_ma_window: int


class PressureStrikeOut(CamelModel):
    strike: float
    ce_oi_change: float
    pe_oi_change: float
    ce_activity: Optional[str] = None
    pe_activity: Optional[str] = None


class OptionPressureOut(CamelModel):
    underlying: str
    expiry: str
    spot_price: float
    net_score: float
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    label: str
    ce_pressure: float
    pe_pressure: float
    ce_dominant: Optional[str] = None
    pe_dominant: Optional[str] = None
    ce_oi_added: float
    pe_oi_added: float
    pcr_oi: float
    pcr_volume: float
    support_strike: Optional[float] = None
    resistance_strike: Optional[float] = None
    strikes_analyzed: int
    strikes: list[PressureStrikeOut]


class AtmAnalysisRowOut(CamelModel):
    time: str
    ts: str
    spot_open: float
    spot_close: float
    spot_high: float
    spot_low: float
    atm_strike: float
    ce_open: Optional[float] = None
    ce_high: Optional[float] = None
    ce_low: Optional[float] = None
    ce_close: Optional[float] = None
    pe_open: Optional[float] = None
    pe_high: Optional[float] = None
    pe_low: Optional[float] = None
    pe_close: Optional[float] = None
    straddle: Optional[float] = None
    # real per-bucket traded volume and open interest
    ce_volume: Optional[float] = None
    pe_volume: Optional[float] = None
    ce_oi: Optional[float] = None
    pe_oi: Optional[float] = None
    ce_oi_change: Optional[float] = None
    pe_oi_change: Optional[float] = None
    # IV solved from the row's real premium; Greeks derived from that IV
    ce_iv: Optional[float] = None
    ce_delta: Optional[float] = None
    ce_gamma: Optional[float] = None
    ce_theta: Optional[float] = None
    ce_vega: Optional[float] = None
    pe_iv: Optional[float] = None
    pe_delta: Optional[float] = None
    pe_gamma: Optional[float] = None
    pe_theta: Optional[float] = None
    pe_vega: Optional[float] = None
    reason: str = ""


class AtmAnalysisOut(CamelModel):
    underlying: str
    from_date: str
    to_date: str
    expiry_kind: Literal["weekly", "monthly"]
    expiry_date: Optional[str] = None
    interval: str
    strike_step: int
    source: Literal["mock", "broker"] = "mock"
    note: Optional[str] = None
    rows: list[AtmAnalysisRowOut]


class BiasConfirmationOut(CamelModel):
    """Whether the day's opening print confirmed or rejected the two-day
    CPR bias [Ochoa 2010, Ch. 6] — a rejected bias inverts the plan."""
    status: Literal["CONFIRMED", "REJECTED", "PENDING"]
    initial_direction: str
    effective_direction: str
    strong: bool = False
    prior_close_supports: Optional[bool] = None
    guidance: str = ""


class PivotTrendOut(CamelModel):
    """Pivot Trend Analysis [Ochoa 2010, Ch. 5]: uptrend holds while price
    closes above S1, downtrend while it closes below R1."""
    state: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    days_in_state: int
    flip_level: Optional[float] = None
    buy_zones: list[str] = []
    sell_zones: list[str] = []
    targets: list[str] = []
    guidance: str = ""


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
    session_open: Optional[float] = None
    bias_confirmation: Optional[BiasConfirmationOut] = None
    pivot_trend: Optional[PivotTrendOut] = None


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
