"""
Backtesting engine — powers backtester.html.

Runs over real historical underlying OHLC (Dhan provides ~5-6 years of daily
NIFTY50/BANKNIFTY candles — see broker_plugins/dhan/adapter.py). Entry/exit
rules are driven by real CPR levels computed from real prior-day OHLC (see
pivots.py — the same calculation backing /market/cpr). There is no historical
option-chain data source (Dhan only exposes a live snapshot), so option-
strategy premiums are *modeled* with Black-Scholes using realized volatility
from the real price series as the IV input (see options_pricing.py) rather
than fabricated outright — a disclosed approximation, not fake data. Futures
strategies need no such modeling: their P&L is the real spot move itself.
"""

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Literal, Optional

from .contract_spec import is_expiry_day, lot_size, next_expiry, strike_step
from .options_pricing import black_scholes, realized_volatility
from .pivots import (
    DailyLevels,
    classify_width_percentile,
    confirm_two_day_bias,
    cpr as calc_cpr,
    daily_levels,
    floor_pivots,
    pivot_trend_state,
)

# NIFTY-shaped defaults, derived from contract_spec so there is exactly one
# source of truth for lot size and strike step. Both used to be hardcoded here
# (LOT_SIZE = 75) and went stale when NSE revised lots from the Jan 2026
# series; anything symbol-aware should call contract_spec.lot_size(symbol) /
# strike_step(symbol) instead of reading these.
LOT_SIZE = lot_size("NIFTY50")
STRIKE_STEP = strike_step("NIFTY50")
# Rough all-in cost (brokerage + STT + exchange + GST + stamp duty) as a
# fraction of premium turnover — same order of magnitude as
# order_service._estimate_charges on the backend, kept local since ai-engine
# has no backend dependency.
COST_FRACTION = 0.0025
PERCENTILE_TRAILING_SESSIONS = 20

# Securities Transaction Tax, effective 1 April 2026 (Budget 2026-27):
# options 0.15% on premium (sell side only, so the option BUYER pays it on
# exit), futures 0.05% (also sell side). Previous rates were 0.10% and 0.02%.
# Kept as named constants because they change with almost every Budget and
# were previously buried as magic numbers that went two revisions stale.
_STT_OPTIONS_SELL = 0.0015
_STT_FUTURES_SELL = 0.0005


def _estimate_leg_charges(side: Literal["BUY", "SELL"], turnover: float) -> float:
    """Real per-order NSE F&O charge model — mirrors
    backend/app/services/order_service._estimate_charges exactly (flat
    brokerage capped at Rs20/order, STT only on the sell side, standard
    exchange/GST/stamp-duty rates), kept local since ai-engine has no
    backend dependency. Used for SPREAD trades instead of the blended
    COST_FRACTION approximation above, since a spread's 4 separate orders
    (sell-open, buy-open, buy-close, sell-close) each have a different,
    side-dependent real cost — STT only hits the two SELL legs, stamp duty
    is ~30x higher on the two BUY legs — that a single flat percentage
    can't represent."""
    brokerage = min(20.0, turnover * 0.0003)
    exchange_charges = turnover * 0.0003503
    # Options STT, sell side. Raised to 0.15% effective 1 Apr 2026 (Budget
    # 2026-27); this was still carrying 0.05%, the pre-Oct-2024 rate, which
    # understated the single largest cost on every exit by 3x and quietly
    # flattered every premium-selling backtest.
    stt = turnover * _STT_OPTIONS_SELL if side == "SELL" else 0.0
    stamp_duty = turnover * 0.00003 if side == "BUY" else turnover * 0.000001
    gst = (brokerage + exchange_charges) * 0.18
    return brokerage + exchange_charges + stt + stamp_duty + gst


@dataclass
class Candle:
    dt: date
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    opened_at: datetime
    closed_at: datetime
    label: str
    pnl: float
    result: str  # "WIN" | "LOSS"
    entry_price: float = 0.0
    exit_price: float = 0.0


@dataclass
class WeeklyStats:
    """Per-week performance, the unit this app is actually judged on (the
    stated goal is 1-2% per week). Each week's P&L is measured against the
    equity at the START of that week, so it reflects what a compounding
    account would really have earned that week — not a flattering fraction
    of the original capital."""
    total_weeks: int
    avg_return_pct: float
    median_return_pct: float
    pct_weeks_profitable: float
    pct_weeks_hit_1pct: float
    pct_weeks_hit_2pct: float
    best_week_pct: float
    worst_week_pct: float
    max_consecutive_losing_weeks: int


@dataclass
class BacktestResult:
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
    equity_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    weekly: Optional[WeeklyStats] = None


def _round_strike(spot: float, symbol: str | None = None) -> float:
    step = strike_step(symbol) if symbol else STRIKE_STEP
    return round(spot / step) * step


@dataclass
class StrategyContext:
    """Everything a strategy's signal function might need beyond today's
    levels: the full candle series and today's index into it, so strategies
    can look at trailing history (e.g. for percentile width classification)
    without ai-engine reaching out to a broker — the series is already
    real historical data the caller fetched."""
    candles: list["Candle"]
    index: int
    # Which index is being traded. Strategies need it to resolve their own
    # expiry and strike step, since those now differ per index: NIFTY expires
    # Tuesday, SENSEX Thursday, and BANKNIFTY has no weekly at all (see
    # contract_spec). Defaulted so older callers keep working.
    symbol: str = "NIFTY50"

    def expiry(self, kind: str = "weekly") -> date:
        """Next expiry for this symbol after the current bar."""
        return next_expiry(self.symbol, self.candles[self.index].dt, kind)

    def is_expiry_day(self, kind: str = "weekly") -> bool:
        return is_expiry_day(self.symbol, self.candles[self.index].dt, kind)

    def strike_step(self) -> int:
        return strike_step(self.symbol)


@dataclass
class LegShape:
    """What a strategy wants to trade once its signal fires. CONDOR and
    SPREAD are their own kinds since a multi-leg position has fundamentally
    different P&L math (see _condor_pnl / _spread_pnl) than a single
    directional leg (see _leg_pnl) — everything else is described generically
    so adding a new single-leg strategy never requires touching the
    simulation loop, only registering a new signal_fn.

    CONDOR sells a naked strangle (call + put, no protective legs) — its
    entire loss is exposed to however far the underlying moves; stop_loss_pct
    is a soft percentage check, not a structural cap (see the drawdowns on
    iron-condor-weekly / the weekly-income-* strategies). SPREAD sells one
    option and buys a further-OTM option of the same type as protection
    (a vertical credit spread) — max loss is capped at the strike width minus
    the credit received, a structural bound the naked CONDOR doesn't have.
    IRON is the two-sided version of SPREAD: a short strangle with a
    protective long wing bought beyond EACH side (four legs), so it is a
    genuine defined-risk iron condor — max loss capped at the wing width
    minus the net credit, on whichever side is breached. Note the historical
    naming wart: the hand-written "iron-condor-weekly" strategy uses kind
    CONDOR and is therefore NAKED despite its name; IRON is the kind that
    actually buys the protection. inner_offset 0 makes it an iron fly (short
    legs at ATM), anything larger an iron condor.

    hold controls the exit style on the daily-bar series:
    - SWING: walk day-by-day until stop/target/expiry (_leg_pnl).
    - INTRADAY: enter on the real open, exit on the real close of the same
      candle, with one day of time value decayed (stop/target don't apply —
      there is no multi-day path to walk on a daily bar).
    - EXPIRY_DAY: same-session, but the exit is the option's real intrinsic
      value at the close — i.e. actual expiry settlement, which is exact
      rather than modeled.
    """
    kind: Literal["SINGLE", "CONDOR", "SPREAD", "IRON", "LONG_STRADDLE"]
    option_type: Optional[Literal["CE", "PE"]] = None
    side: Optional[Literal["BUY", "SELL"]] = None
    # SINGLE: offset of the one leg. SPREAD: offset of the short (sold) leg —
    # the long protective leg sits `wing_offset` further out (see below).
    strike_offset: int = 0  # points OTM from ATM (0 = ATM); direction of "OTM" follows option_type
    direction: int = 0      # +1 profits from underlying rising, -1 profits from underlying falling
    hold: Literal["SWING", "INTRADAY", "EXPIRY_DAY"] = "SWING"
    # CONDOR: each wing's OTM distance from ATM (both legs are naked).
    # SPREAD: extra OTM distance from the short leg to the protective long leg.
    # IRON: extra OTM distance from each short leg to its protective long leg.
    wing_offset: int = 200
    # IRON only: how far OTM each SHORT leg sits from ATM. 0 = iron fly
    # (both shorts at ATM); >0 = iron condor with that much room either side.
    inner_offset: int = 0
    # LONG_STRADDLE only: abandon the position after this many sessions if
    # the expansion it was bought for hasn't arrived. A long-premium position
    # that isn't working is bleeding theta every day it's held, so the time
    # stop is a real risk control, not a convenience. 0 disables it.
    time_stop_days: int = 0
    # Multiplier on the run's target_pct [Ochoa 2010, Ch. 6, ~p.196]: a narrow
    # CPR follows a quiet session and lets price run past the second pivot
    # layer, so a fixed target leaves money behind; a wide CPR rarely clears
    # that layer, so the same target is unreachable. Strategies that read the
    # width set this; everything else leaves it at 1.0 and is unaffected.
    target_scale: float = 1.0
    # Multiplier on the run's stop_loss_pct, the mirror of target_scale. It
    # exists mainly for long premium: a buyer's edge lives in the payoff ratio
    # (a low win rate carried by large winners), and that ratio is shaped by
    # moving stop and target independently. 1.0 = unchanged, so every existing
    # strategy is unaffected.
    stop_scale: float = 1.0


SignalFn = Callable[[float, DailyLevels, StrategyContext], Optional[LegShape]]


@dataclass
class _StrategyDef:
    label: str
    signal_fn: SignalFn
    description: str = ""


STRATEGY_REGISTRY: dict[str, _StrategyDef] = {}


def register_strategy(key: str, label: str):
    def deco(fn: SignalFn) -> SignalFn:
        description = " ".join((fn.__doc__ or "").split())
        STRATEGY_REGISTRY[key] = _StrategyDef(label=label, signal_fn=fn, description=description)
        return fn
    return deco


def _sma(closes: list[float], n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _percentile_width_regime(ctx: StrategyContext) -> Optional[str]:
    """Today's CPR width regime (NARROW/NORMAL/WIDE) ranked against the
    trailing real sessions already in this backtest's own candle series —
    the same percentile method backing the live CPR Dashboard's Trade Plan
    (see pivot_service.py), just computed offline over historical candles
    instead of live broker calls. Returns None if there isn't enough
    trailing history yet (early in the backtest window)."""
    i = ctx.index
    if i < PERCENTILE_TRAILING_SESSIONS + 2:
        return None
    widths = []
    for j in range(i - PERCENTILE_TRAILING_SESSIONS, i):
        prior, before = ctx.candles[j - 1], ctx.candles[j - 2]
        widths.append(calc_cpr(prior.high, prior.low, prior.close).width_pct)
    today_prior = ctx.candles[i - 1]
    today_width = calc_cpr(today_prior.high, today_prior.low, today_prior.close).width_pct
    forecast = classify_width_percentile(today_width, widths)
    return forecast.regime if forecast else None


def _confirmed_bias(entry_spot: float, levels: DailyLevels, ctx: StrategyContext):
    """The two-day bias after the day's real opening print is applied.

    entry_spot IS the opening print here (the engine enters at the day's
    open), so this is the same confirmation the live CPR dashboard runs —
    identical function, identical inputs. Returns None when there is no
    two-day relationship to judge."""
    prior_close = ctx.candles[ctx.index - 1].close if ctx.index >= 1 else None
    return confirm_two_day_bias(levels.two_day, levels.cpr_today, entry_spot, prior_close)


_TREND_LOOKBACK = 30


def _pivot_trend(ctx: StrategyContext):
    """Pivot Trend Analysis over the trailing real sessions — each day judged
    against the S1/R1 that were in force for it (computed from the day
    before it), exactly as pivot_service does for the live dashboard."""
    i = ctx.index
    start = max(2, i - _TREND_LOOKBACK)
    if i - start < 2:
        return None
    sessions = []
    for j in range(start, i):
        prior = ctx.candles[j - 1]
        fp = floor_pivots(prior.high, prior.low, prior.close)
        sessions.append({"close": ctx.candles[j].close, "s1": fp.s1, "r1": fp.r1})
    return pivot_trend_state(sessions)


def _width_target_scale(ctx: StrategyContext) -> float:
    """Target multiplier from the CPR width regime [Ochoa 2010, ~p.196]."""
    regime = _percentile_width_regime(ctx)
    if regime == "NARROW":
        return 1.6   # let it run past the second layer
    if regime == "WIDE":
        return 0.6   # moderate expectations, take the closer pivot
    return 1.0


@register_strategy("ai-bias-ce-writing-below-vah", "SELL CALL (CE Writing)")
def _sig_ce_writing_below_vah(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Sells a call 100 points OTM when the day opens below the CPR top (TC)
    and the two-day CPR bias is not bullish — collects premium while the
    level above caps upside. Strike: ATM + 100. Held until stop/target/
    weekly expiry."""
    cpr = levels.cpr_today
    bearish_or_flat = levels.two_day is None or levels.two_day.direction != "BULLISH"
    if entry_spot < cpr.tc and bearish_or_flat:
        return LegShape(kind="SINGLE", option_type="CE", side="SELL", strike_offset=100, direction=-1)
    return None


@register_strategy("mean-reversion-at-val", "BUY CALL (Mean Reversion)")
def _sig_mean_reversion_at_val(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Buys an ATM call when the day opens at or below the CPR bottom (BC) —
    a bet on reversion back up into the range. Strike: ATM."""
    if entry_spot <= levels.cpr_today.bc * 1.002:
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1)
    return None


@register_strategy("breakout-above-vah", "BUY CALL (Breakout)")
def _sig_breakout_above_vah(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Buys an ATM call when the day opens above the CPR top (TC) — momentum
    continuation above value. Strike: ATM."""
    if entry_spot > levels.cpr_today.tc:
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1)
    return None


@register_strategy("iron-condor-weekly", "IRON CONDOR")
def _sig_iron_condor_weekly(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Sells a 200-point-wide strangle (call at ATM+200, put at ATM−200)
    when the CPR width regime is NORMAL or WIDE — a rangebound-day premium
    bet. Exits when a wing is threatened (stop %), premium has decayed
    (target %), or at weekly expiry."""
    if levels.width.regime in ("NORMAL", "WIDE"):
        return LegShape(kind="CONDOR")
    return None


@register_strategy("pe-writing-above-val", "SELL PUT (PE Writing)")
def _sig_pe_writing_above_val(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Bearish-writing's mirror: sell an OTM put (bet the underlying holds
    up / doesn't break down) when price sits above the CPR's floor and the
    two-day bias isn't bearish — the put-writing counterpart to the
    existing CE-writing strategy above."""
    cpr = levels.cpr_today
    bullish_or_flat = levels.two_day is None or levels.two_day.direction != "BEARISH"
    if entry_spot > cpr.bc and bullish_or_flat:
        return LegShape(kind="SINGLE", option_type="PE", side="SELL", strike_offset=100, direction=1)
    return None


@register_strategy("cpr-percentile-breakout-long", "BUY CALL (Percentile Breakout)")
def _sig_cpr_percentile_breakout_long(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Higher-conviction breakout: today's CPR isn't just above yesterday's
    (ascending two-day relationship) but is itself in the NARROW percentile
    band — PivotBoss theory reads a narrow CPR as an elevated-probability
    trending day, so a breakout out of a narrow range is a stronger signal
    than breakout-above-vah's plain "price above TC" check."""
    two_day = levels.two_day
    is_ascending = two_day is not None and two_day.relationship in ("HIGHER_VALUE", "OVERLAPPING_HIGHER_VALUE")
    if is_ascending and entry_spot > levels.cpr_today.tc and _percentile_width_regime(ctx) == "NARROW":
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1)
    return None


@register_strategy("cpr-percentile-breakout-short", "BUY PUT (Percentile Breakdown)")
def _sig_cpr_percentile_breakout_short(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Bearish mirror of cpr-percentile-breakout-long: a narrow-CPR
    breakdown below a descending two-day CPR."""
    two_day = levels.two_day
    is_descending = two_day is not None and two_day.relationship in ("LOWER_VALUE", "OVERLAPPING_LOWER_VALUE")
    if is_descending and entry_spot < levels.cpr_today.bc and _percentile_width_regime(ctx) == "NARROW":
        return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1)
    return None


@register_strategy("gamma-blast-expiry-day", "GAMMA BLAST (Expiry-Day ATM Buy)")
def _sig_gamma_blast(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Expiry-day gamma blast: on the weekly expiry day itself, an ATM
    option is nearly all gamma, so a decisive open outside the CPR after a
    coiled (NARROW-percentile) session can multiply the premium in hours.
    Buys when: today is the weekly expiry, the trailing percentile width
    regime is NARROW, and the open is outside the CPR — a CALL above TC, a
    PUT below BC. Strike: ATM (offset 0) — gamma is highest at the money on
    expiry day; further-OTM strikes need a much larger move to participate.
    Exits at the close at real intrinsic settlement value."""
    today = ctx.candles[ctx.index].dt
    if not is_expiry_day(ctx.symbol, today):  # per-index expiry: NIFTY Tue, SENSEX Thu, BANKNIFTY monthly
        return None
    if _percentile_width_regime(ctx) != "NARROW":
        return None
    cpr = levels.cpr_today
    if entry_spot > cpr.tc:
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1, hold="EXPIRY_DAY")
    if entry_spot < cpr.bc:
        return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1, hold="EXPIRY_DAY")
    return None


@register_strategy("intraday-otm-seller", "SELL OTM INTRADAY (Theta Scalp)")
def _sig_intraday_otm_seller(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Intraday premium selling: on a day whose CPR width forecast reads
    rangebound (WIDE regime → sideways session likely), sells a far-OTM
    option on the side the two-day bias says the market should NOT go —
    a PUT 200 points below ATM when the bias is bullish, a CALL 200 points
    above ATM when bearish — and buys it back at the close. Strike: ATM ±
    200, roughly outside a normal NIFTY day range, so the position is
    mostly theta with a buffer against drift."""
    if levels.width.regime != "WIDE":
        return None
    td = levels.two_day
    if td is not None and td.direction == "BULLISH":
        return LegShape(kind="SINGLE", option_type="PE", side="SELL", strike_offset=200, direction=1, hold="INTRADAY")
    if td is not None and td.direction == "BEARISH":
        return LegShape(kind="SINGLE", option_type="CE", side="SELL", strike_offset=200, direction=-1, hold="INTRADAY")
    return None


@register_strategy("ma-greeks-trend-buy", "BUY OPTION (MA Trend + Delta Strike)")
def _sig_ma_greeks_trend_buy(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Momentum buying with the strike picked by Greeks instead of a fixed
    offset: when the 20-SMA is above the 50-SMA and price is above the
    20-SMA (uptrend), buys the CALL whose Black-Scholes delta is closest
    to 0.60 — slightly ITM, enough delta to track the move without paying
    deep-ITM premium. Mirrors with a PUT (|delta| ≈ 0.60) in downtrends.
    Held until stop/target/weekly expiry."""
    i = ctx.index
    closes = [c.close for c in ctx.candles[: i + 1]]
    s20, s50 = _sma(closes, 20), _sma(closes, 50)
    if s20 is None or s50 is None:
        return None
    up = s20 > s50 and entry_spot > s20
    down = s20 < s50 and entry_spot < s20
    if not (up or down):
        return None
    opt: Literal["CE", "PE"] = "CE" if up else "PE"
    sigma = realized_volatility(closes)
    today = ctx.candles[i].dt
    expiry = next_expiry(ctx.symbol, today)
    t = max((expiry - today).days / 365.0, 1.0 / 365.0)
    atm = _round_strike(entry_spot, ctx.symbol)
    best_off, best_err = 0, float("inf")
    for off in range(-200, 201, ctx.strike_step()):
        d = abs(black_scholes(entry_spot, atm + off, t, sigma, opt).delta)
        err = abs(d - 0.60)
        if err < best_err:
            best_err, best_off = err, off
    # _strike_for applies +offset for CE and -offset for PE; best_off is in
    # absolute points from ATM, so flip the sign for puts to land on atm+off.
    shape_off = best_off if opt == "CE" else -best_off
    return LegShape(kind="SINGLE", option_type=opt, side="BUY", strike_offset=shape_off, direction=1 if up else -1)


@dataclass
class MaDeltaSpreadParams:
    """Everything the ma_delta_spread template needs, pulled out of
    _sig_ma_greeks_credit_spread so the Strategies page can configure its
    own instances of the same logic (see make_ma_delta_spread_signal)
    instead of the one fixed preset below. Defaults are that preset —
    0.25 delta / 100pt wing / 30%-of-credit target — arrived at by real
    iteration against 6 years of Dhan data (see that function's history for
    the two parameter choices that made things worse, not better)."""
    fast_ma: int = 20
    slow_ma: int = 50
    delta_target: float = 0.25
    wing_offset: int = 100
    target_scale: float = 10.0  # run's shared target_pct(default 3%) * this = ~effective % of credit
    skip_narrow: bool = True


def make_ma_delta_spread_signal(p: MaDeltaSpreadParams) -> SignalFn:
    """Factory: builds a SignalFn closure for the MA-crossover-trend +
    delta-selected defined-risk credit spread — sells a bull put spread in
    an uptrend, a bear call spread in a downtrend, short strike chosen by
    Black-Scholes delta, protected by a further-OTM long leg (see
    LegShape/_spread_pnl for why that protection matters vs. the naked
    iron-condor-weekly/weekly-income-* strategies above)."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        i = ctx.index
        closes = [c.close for c in ctx.candles[: i + 1]]
        s_fast, s_slow = _sma(closes, p.fast_ma), _sma(closes, p.slow_ma)
        if s_fast is None or s_slow is None:
            return None
        if p.skip_narrow and _percentile_width_regime(ctx) == "NARROW":
            return None
        up = s_fast > s_slow and entry_spot > s_fast
        down = s_fast < s_slow and entry_spot < s_fast
        if not (up or down):
            return None

        opt: Literal["CE", "PE"] = "PE" if up else "CE"  # sell the side the trend defends
        sigma = realized_volatility(closes)
        today = ctx.candles[i].dt
        expiry = next_expiry(ctx.symbol, today)
        t = max((expiry - today).days / 365.0, 1.0 / 365.0)
        atm = _round_strike(entry_spot, ctx.symbol)
        best_off, best_err = 50, float("inf")
        for off in range(50, 301, ctx.strike_step()):
            strike = atm + off if opt == "CE" else atm - off
            err = abs(abs(black_scholes(entry_spot, strike, t, sigma, opt).delta) - p.delta_target)
            if err < best_err:
                best_err, best_off = err, off

        return LegShape(
            kind="SPREAD", option_type=opt, side="SELL", strike_offset=best_off,
            direction=1 if up else -1, wing_offset=p.wing_offset, target_scale=p.target_scale,
        )
    return _signal


# -- premium-selling templates -----------------------------------------------
# Four configurable short-premium structures, two naked and two defined-risk.
# They share one entry gate (_sell_entry_ok) so the comparison between them is
# a comparison of STRUCTURE, not of two different entry rules — the honest way
# to answer "is the extra protection worth the credit I give up for it".


def _iv_proxy_rank(ctx: StrategyContext, lookback: int) -> Optional[float]:
    """Where today's realized volatility sits within its own trailing range,
    0-100 — a stand-in for IV rank.

    True IV rank needs an implied-vol history, which no Indian broker API
    exposes historically (see options_pricing's module docstring). Realized
    vol computed from the underlying's own real closes is the defensible
    substitute: it moves with the same regime shifts, it is built entirely
    from real data, and being a *rank* it cares only about the relative
    position, which survives the systematic gap between realized and implied.
    It is NOT an IV rank and the templates that use it say so.
    """
    i = ctx.index
    if i < lookback + 25:
        return None
    closes = [c.close for c in ctx.candles[: i + 1]]
    series = []
    for j in range(i - lookback, i + 1):
        window = closes[: j + 1]
        if len(window) > 21:
            series.append(realized_volatility(window))
    if len(series) < 10:
        return None
    lo, hi = min(series), max(series)
    if hi <= lo:
        return None
    return (series[-1] - lo) / (hi - lo) * 100


def _sell_entry_ok(ctx: StrategyContext, skip_narrow: bool, min_vol_rank: float,
                   vol_lookback: int) -> bool:
    """Shared gate for every short-premium template.

    Two filters, both grounded in why selling loses money rather than in
    curve-fitting: a NARROW CPR forecasts a trending session [Ochoa 2010,
    Ch. 6] and trend is what runs a short-premium book over, and selling
    cheap volatility collects too little credit to survive the occasional
    breach. Either can be switched off by the user (min_vol_rank 0 disables
    the vol gate) — they are defaults, not doctrine.
    """
    if skip_narrow and _percentile_width_regime(ctx) == "NARROW":
        return False
    if min_vol_rank > 0:
        rank = _iv_proxy_rank(ctx, vol_lookback)
        # None = not enough history yet; don't fire rather than assume cheap
        # vol is fine, since that is the failure mode this gate exists for.
        if rank is None or rank < min_vol_rank:
            return False
    return True


@dataclass
class ShortStraddleParams:
    """Sells the ATM call and ATM put, both naked. The maximum credit any of
    these four structures collects, and the only one with no protection at
    all on either side — its loss is bounded only by the stop."""
    skip_narrow: bool = True
    min_vol_rank: float = 40.0
    vol_lookback: int = 120
    target_scale: float = 10.0


def make_short_straddle_signal(p: ShortStraddleParams) -> SignalFn:
    """Factory: naked short straddle. Uses the CONDOR kind with zero wing
    offset, which places both legs at ATM — CONDOR's P&L model is already
    'sell a call and a put and track the combined premium', and a straddle is
    exactly that with the wings collapsed onto ATM."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        if not _sell_entry_ok(ctx, p.skip_narrow, p.min_vol_rank, p.vol_lookback):
            return None
        return LegShape(kind="CONDOR", wing_offset=0, target_scale=p.target_scale)
    return _signal


@dataclass
class ShortStrangleParams:
    """Sells an OTM call and an OTM put, both naked. Less credit than the
    straddle for a much wider profit zone; still structurally unprotected."""
    wing_offset: int = 200
    skip_narrow: bool = True
    min_vol_rank: float = 40.0
    vol_lookback: int = 120
    target_scale: float = 10.0


def make_short_strangle_signal(p: ShortStrangleParams) -> SignalFn:
    """Factory: naked short strangle at ATM ± wing_offset."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        if not _sell_entry_ok(ctx, p.skip_narrow, p.min_vol_rank, p.vol_lookback):
            return None
        return LegShape(kind="CONDOR", wing_offset=p.wing_offset, target_scale=p.target_scale)
    return _signal


@dataclass
class IronCondorParams:
    """Sells an OTM strangle and buys a wing beyond each short leg — the
    defined-risk version of ShortStrangleParams. Max loss is capped at
    wing_width minus net credit (see _iron_pnl), which is the whole point."""
    inner_offset: int = 200
    wing_offset: int = 100
    skip_narrow: bool = True
    min_vol_rank: float = 40.0
    vol_lookback: int = 120
    target_scale: float = 10.0


def make_iron_condor_signal(p: IronCondorParams) -> SignalFn:
    """Factory: genuine four-leg defined-risk iron condor. Note this is NOT
    the same as the hand-written "iron-condor-weekly" strategy, which despite
    its name sells a naked strangle (kind CONDOR) with no wings at all."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        if not _sell_entry_ok(ctx, p.skip_narrow, p.min_vol_rank, p.vol_lookback):
            return None
        return LegShape(
            kind="IRON", inner_offset=p.inner_offset, wing_offset=p.wing_offset,
            target_scale=p.target_scale,
        )
    return _signal


@dataclass
class IronFlyParams:
    """Sells the ATM straddle and buys a wing either side — the defined-risk
    version of ShortStraddleParams. Largest credit of the two defined-risk
    structures, narrowest profit zone."""
    wing_offset: int = 200
    skip_narrow: bool = True
    min_vol_rank: float = 40.0
    vol_lookback: int = 120
    target_scale: float = 10.0


def make_iron_fly_signal(p: IronFlyParams) -> SignalFn:
    """Factory: four-leg iron fly — an IRON with both shorts at ATM."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        if not _sell_entry_ok(ctx, p.skip_narrow, p.min_vol_rank, p.vol_lookback):
            return None
        return LegShape(
            kind="IRON", inner_offset=0, wing_offset=p.wing_offset, target_scale=p.target_scale,
        )
    return _signal


# -- option-BUYING templates --------------------------------------------------
# Long premium is a structurally harder game than selling and these templates
# are built to say so rather than to flatter it.
#
# Implied volatility exceeds subsequently realized volatility on roughly
# three-quarters of days in NIFTY, so the buyer is on average overpaying for
# optionality before any timing skill is applied. Theta compounds daily against
# the position and steepens into expiry. The honest consequence is a LOW win
# rate (~30-45% for directional buying) carried by a right-skewed payoff: a few
# large winners funding many small losers.
#
# So these templates target PAYOFF RATIO, not hit rate. Defaults use a tight
# stop against a much wider target, which deliberately produces a low win rate
# and a high average-win-to-average-loss ratio. Any configuration that raises
# the win rate by taking profits early will usually lower expectancy — that
# trade-off is the whole design problem here, and the backtest's `payoff` and
# `expectancy` columns are the ones to read, not `win_rate`.
#
# What this engine CANNOT express, stated plainly rather than faked: it runs on
# daily bars, so intraday-triggered ideas (opening-range breakouts on 5-minute
# closes, VWAP pullbacks, delta-hedged gamma scalping) have no faithful form
# here. They are omitted rather than approximated into something that would
# backtest well and mean nothing.


def _vol_rank(ctx: StrategyContext, lookback: int) -> Optional[float]:
    """Alias of the shared realized-volatility rank. Buyers want it LOW (cheap
    premium); sellers want it high — same measure, opposite sign of interest."""
    return _iv_proxy_rank(ctx, lookback)


@dataclass
class DirectionalBuyParams:
    """Trend-following long option. Delta is configurable because it is the
    central trade-off for a buyer: higher delta tracks the underlying more
    closely and bleeds less theta per rupee, but costs far more premium and so
    risks more per lot."""
    fast_ma: int = 20
    slow_ma: int = 50
    delta_target: float = 0.60      # slightly ITM — more delta, less theta burn
    stop_scale: float = 0.6         # x the run's stop_loss_pct
    target_scale: float = 2.4       # x the run's target_pct -> ~2:1+ payoff by design
    max_vol_rank: float = 70.0      # don't buy when premium is already rich
    vol_lookback: int = 120


def make_directional_buy_signal(p: DirectionalBuyParams) -> SignalFn:
    """Factory: buys a delta-selected call in an uptrend, put in a downtrend,
    and refuses to buy when volatility is already expensive by its own trailing
    standard — the buyer's version of the sellers' minimum-vol gate."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        i = ctx.index
        closes = [c.close for c in ctx.candles[: i + 1]]
        s_fast, s_slow = _sma(closes, p.fast_ma), _sma(closes, p.slow_ma)
        if s_fast is None or s_slow is None:
            return None
        if p.max_vol_rank < 100:
            rank = _vol_rank(ctx, p.vol_lookback)
            if rank is not None and rank > p.max_vol_rank:
                return None  # paying up for premium at the top of its own range
        up = s_fast > s_slow and entry_spot > s_fast
        down = s_fast < s_slow and entry_spot < s_fast
        if not (up or down):
            return None

        opt: Literal["CE", "PE"] = "CE" if up else "PE"
        sigma = realized_volatility(closes)
        expiry = next_expiry(ctx.symbol, ctx.candles[i].dt)
        t = max((expiry - ctx.candles[i].dt).days / 365.0, 1.0 / 365.0)
        atm = _round_strike(entry_spot, ctx.symbol)
        step = ctx.strike_step()
        best_off, best_err = 0, float("inf")
        for off in range(-4 * step, 4 * step + 1, step):
            strike = atm + off
            d = abs(black_scholes(entry_spot, strike, t, sigma, opt).delta)
            if abs(d - p.delta_target) < best_err:
                best_err, best_off = abs(d - p.delta_target), off
        # _strike_for treats offset as OTM distance, so mirror the sign for puts
        shape_off = best_off if opt == "CE" else -best_off
        return LegShape(
            kind="SINGLE", option_type=opt, side="BUY", strike_offset=shape_off,
            direction=1 if up else -1, target_scale=p.target_scale, stop_scale=p.stop_scale,
        )
    return _signal


@dataclass
class LongStraddleParams:
    """Buys the ATM straddle out of a volatility squeeze. Wins on expansion in
    either direction; loses to theta if the squeeze simply continues, which is
    the common outcome and why the time stop is on by default."""
    max_vol_rank: float = 30.0      # only when premium is cheap by its own history
    vol_lookback: int = 120
    squeeze_lookback: int = 7       # NR7-style range compression
    require_squeeze: bool = True
    time_stop_days: int = 2
    stop_scale: float = 1.0
    target_scale: float = 2.0


def make_long_straddle_signal(p: LongStraddleParams) -> SignalFn:
    """Factory: long ATM straddle on a low-volatility range compression."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        i = ctx.index
        if p.max_vol_rank < 100:
            rank = _vol_rank(ctx, p.vol_lookback)
            if rank is None or rank > p.max_vol_rank:
                return None
        if p.require_squeeze:
            n = p.squeeze_lookback
            if i < n:
                return None
            ranges = [c.high - c.low for c in ctx.candles[i - n: i]]
            today_range = ctx.candles[i - 1].high - ctx.candles[i - 1].low
            if not ranges or today_range > min(ranges):
                return None  # not the narrowest of the window — no coil to break
        return LegShape(
            kind="LONG_STRADDLE", target_scale=p.target_scale, stop_scale=p.stop_scale,
            time_stop_days=p.time_stop_days,
        )
    return _signal


@dataclass
class ExpiryDayBuyParams:
    """0DTE ATM buy on the index's own expiry day. On expiry an ATM option is
    almost pure gamma, so a decisive directional session can multiply it — and
    a quiet one can take most of it to zero by the close. Highest variance of
    anything here."""
    require_narrow: bool = True     # coiled prior session; a squeeze precedes the blast
    otm_offset_steps: int = 0       # 0 = ATM; 1 = one strike OTM (cheaper, lower delta)
    target_scale: float = 3.0


def make_expiry_day_buy_signal(p: ExpiryDayBuyParams) -> SignalFn:
    """Factory: expiry-session ATM directional buy, direction taken from where
    the session opens relative to the CPR. Uses each index's REAL expiry day —
    Tuesday for NIFTY, Thursday for SENSEX, monthly for BANKNIFTY."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        if not ctx.is_expiry_day():
            return None
        if p.require_narrow and _percentile_width_regime(ctx) != "NARROW":
            return None
        cpr = levels.cpr_today
        step = ctx.strike_step()
        off = p.otm_offset_steps * step
        if entry_spot > cpr.tc:
            return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=off,
                            direction=1, hold="EXPIRY_DAY", target_scale=p.target_scale)
        if entry_spot < cpr.bc:
            return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=off,
                            direction=-1, hold="EXPIRY_DAY", target_scale=p.target_scale)
        return None
    return _signal


@dataclass
class PivotReversalBuyParams:
    """Counter-trend: buys a reversal after the prior session CLOSED beyond a
    pivot extreme, betting price reverts toward the central pivot.

    Note what this deliberately is NOT. The textbook version of this idea
    triggers when price *touches* an extreme intraday and rejects it — that
    needs intraday bars, and this engine enters at the daily open, which sits
    essentially at the prior close. Tested against the open, the touch
    condition fires on literally zero bars. Anchoring on the prior session's
    CLOSE beyond the band is a completed, daily-observable fact and is
    therefore something this engine can actually measure. It is a weaker,
    slower signal than the intraday version and should not be read as a
    backtest of it.
    """
    stretch_pct: float = 0.0        # extra % beyond S1/R1 the close must reach
    skip_narrow: bool = True        # NARROW forecasts a trend day; stand aside
    target_scale: float = 2.0
    vol_lookback: int = 120
    max_vol_rank: float = 80.0


def make_pivot_reversal_buy_signal(p: PivotReversalBuyParams) -> SignalFn:
    """Factory: buys a call after a close below S1 / a put after a close above
    R1, entering at the next session's open."""
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        if p.skip_narrow and _percentile_width_regime(ctx) == "NARROW":
            return None
        if p.max_vol_rank < 100:
            rank = _vol_rank(ctx, p.vol_lookback)
            if rank is not None and rank > p.max_vol_rank:
                return None
        # The pivots in `levels` are computed FROM the prior session, so
        # testing that session's own close against them is circular — S1
        # derived from a day's own H/L/C can never sit above that day's low,
        # so the condition is unsatisfiable and the strategy silently never
        # fires. Rebuild the levels that were actually in force WHILE the
        # prior session traded (from the two days before it) and test against
        # those instead.
        i = ctx.index
        if i < 3:
            return None
        prior = ctx.candles[i - 1]
        b1, b2 = ctx.candles[i - 2], ctx.candles[i - 3]
        prior_levels = daily_levels(b1.high, b1.low, b1.close, b2.high, b2.low, b2.close)
        prior_close = prior.close
        fp = prior_levels.floor
        stretch = p.stretch_pct / 100.0
        if prior_close <= fp.s1 * (1 - stretch):
            return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0,
                            direction=1, target_scale=p.target_scale)
        if prior_close >= fp.r1 * (1 + stretch):
            return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0,
                            direction=-1, target_scale=p.target_scale)
        return None
    return _signal


@dataclass
class SmaCrossoverParams:
    """Classic two-SMA crossover: fast above slow is long, fast below is short.

    `cross_only` is the parameter that decides what is actually being tested,
    and the two settings are different strategies wearing the same name:

    - True  (default): enter only on the bar where the fast SMA CROSSES the
      slow one. One entry per swing, which is what "15 breaks 20" means.
    - False: enter on any bar where fast is above (or below) slow. Since the
      engine holds one position at a time and re-enters as soon as the last
      exits, this re-buys the same trend repeatedly after every stop-out —
      far more trades, and a very different risk profile.

    Defaults are the user-requested 15/20. Nothing about that pair is
    special; it is a short, fast crossover that will whipsaw in a range.
    """
    fast_ma: int = 15
    slow_ma: int = 20
    cross_only: bool = True
    # Strike selection for the options leg. ~0.55 delta is near-ATM: enough
    # delta to track the move without paying deep-ITM premium.
    delta_target: float = 0.55
    stop_scale: float = 1.0
    target_scale: float = 2.0


def make_sma_crossover_signal(p: SmaCrossoverParams) -> SignalFn:
    """Factory: 15/20-style SMA crossover, long above and short below.

    Works unchanged for futures runs: the engine reads LegShape.direction for
    those and ignores the option fields, so the same signal expresses "long
    the index" or "buy a call" depending only on the instrument chosen.
    """
    def _signal(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
        i = ctx.index
        closes = [c.close for c in ctx.candles[: i + 1]]
        fast, slow = _sma(closes, p.fast_ma), _sma(closes, p.slow_ma)
        if fast is None or slow is None:
            return None

        up = fast > slow
        if p.cross_only:
            # Compare against the PREVIOUS bar's pair to detect the cross
            # itself. Needs one extra bar of history than the state check.
            prev_closes = closes[:-1]
            prev_fast, prev_slow = _sma(prev_closes, p.fast_ma), _sma(prev_closes, p.slow_ma)
            if prev_fast is None or prev_slow is None:
                return None
            crossed_up = prev_fast <= prev_slow and fast > slow
            crossed_down = prev_fast >= prev_slow and fast < slow
            if not (crossed_up or crossed_down):
                return None
            up = crossed_up

        opt: Literal["CE", "PE"] = "CE" if up else "PE"
        sigma = realized_volatility(closes)
        today = ctx.candles[i].dt
        expiry = next_expiry(ctx.symbol, today)
        t = max((expiry - today).days / 365.0, 1.0 / 365.0)
        atm = _round_strike(entry_spot, ctx.symbol)
        step = ctx.strike_step()
        best_off, best_err = 0, float("inf")
        for off in range(-4 * step, 4 * step + 1, step):
            d = abs(black_scholes(entry_spot, atm + off, t, sigma, opt).delta)
            if abs(d - p.delta_target) < best_err:
                best_err, best_off = abs(d - p.delta_target), off
        # _strike_for reads offset as OTM distance, so mirror the sign for puts
        shape_off = best_off if opt == "CE" else -best_off

        return LegShape(
            kind="SINGLE", option_type=opt, side="BUY", strike_offset=shape_off,
            direction=1 if up else -1,
            target_scale=p.target_scale, stop_scale=p.stop_scale,
        )
    return _signal


# Templates the Strategies page can instantiate via register_custom_strategy.
# Keyed by the template id the frontend/backend pass through; each maps to
# the params dataclass and signal factory that build a runnable strategy
# from a plain dict of user-supplied values.
STRATEGY_TEMPLATES: dict[str, tuple[type, Callable[[object], SignalFn]]] = {
    "ma_delta_spread": (MaDeltaSpreadParams, make_ma_delta_spread_signal),
    "short_straddle": (ShortStraddleParams, make_short_straddle_signal),
    "short_strangle": (ShortStrangleParams, make_short_strangle_signal),
    "iron_condor": (IronCondorParams, make_iron_condor_signal),
    "iron_fly": (IronFlyParams, make_iron_fly_signal),
    # option buying
    "directional_buy": (DirectionalBuyParams, make_directional_buy_signal),
    "long_straddle": (LongStraddleParams, make_long_straddle_signal),
    "expiry_day_buy": (ExpiryDayBuyParams, make_expiry_day_buy_signal),
    "pivot_reversal_buy": (PivotReversalBuyParams, make_pivot_reversal_buy_signal),
    "sma_crossover": (SmaCrossoverParams, make_sma_crossover_signal),
}

_CUSTOM_STRATEGY_KEYS: set[str] = set()


def register_custom_strategy(key: str, label: str, template: str, params: dict) -> None:
    """Registers a data-driven strategy instance — built via the Strategies
    page's form, not hand-written code — into the same STRATEGY_REGISTRY the
    hand-written strategies in this file use. The backtest engine, the
    Strategy Lab sweep, and /backtests don't need to know the difference;
    they just see another registered key. Re-registering an existing key
    (e.g. after an edit) overwrites it in place."""
    if template not in STRATEGY_TEMPLATES:
        raise ValueError(f"unknown strategy template '{template}' (known: {sorted(STRATEGY_TEMPLATES)})")
    params_cls, factory = STRATEGY_TEMPLATES[template]
    try:
        p = params_cls(**params)
    except TypeError as exc:
        raise ValueError(f"invalid params for template '{template}': {exc}") from exc
    fn = factory(p)
    fn.__doc__ = f"Custom strategy — template '{template}', params {params}."
    STRATEGY_REGISTRY[key] = _StrategyDef(label=label, signal_fn=fn, description=fn.__doc__)
    _CUSTOM_STRATEGY_KEYS.add(key)


def unregister_custom_strategy(key: str) -> None:
    """Removes a custom strategy from the runnable registry (used on delete
    and on deactivate — an inactive custom strategy simply isn't registered,
    same as if it had never been created)."""
    STRATEGY_REGISTRY.pop(key, None)
    _CUSTOM_STRATEGY_KEYS.discard(key)


def is_custom_strategy(key: str) -> bool:
    return key in _CUSTOM_STRATEGY_KEYS


_sig_ma_greeks_credit_spread = make_ma_delta_spread_signal(MaDeltaSpreadParams())
_sig_ma_greeks_credit_spread.__doc__ = """Defined-risk income strategy: when the 20-SMA/50-SMA trend agrees with
a rangebound CPR-width read (NORMAL or WIDE — NARROW is skipped, the same
coiled-breakout filter the other premium sellers above use), sells a
vertical credit spread in the direction of the trend — a bull put spread
in an uptrend, a bear call spread in a downtrend. The short strike is
chosen by Black-Scholes delta (~0.25, a standard defensive credit-spread
convention), and a further-OTM long strike is bought as protection. That
protective leg is what iron-condor-weekly and the weekly-income-*
strategies don't have: max loss here is structurally capped at the
strike width minus the credit received (see LegShape/_spread_pnl), not
merely soft-stopped against however far the underlying actually moves.

This is the fixed preset (see MaDeltaSpreadParams' defaults) behind the
parameterized ma_delta_spread template the Strategies page offers — two
tuning notes from real iteration against 6 years of Dhan data, kept here
so the next change starts from evidence, not a guess:
- A further-OTM ~0.20 delta was tried first for a higher win rate. It
  backfired: less-OTM strikes collect less credit, so for the *same*
  wing width the structural max loss (width - credit) is actually
  *larger*, not smaller — out-of-sample max drawdown got worse
  (-109% vs -96%), not better. Reverted to 0.25.
- target_scale=10 turns the run's shared target_pct (meant for a
  directional leg's underlying-move target, default 3%) into a ~30%
  net-credit decay target instead. Left at the shared 3% default, this
  exited almost every trade within a day or two for a token profit —
  a 70% win rate but a 0.65 profit factor (mostly tiny wins, one loss
  that ran much further). 30% is a middle ground between that and the
  standard "take profit at 50% of max credit" convention, which (at
  0.25 delta) pushed drawdown worse still by holding losers longer
  before the stop could fire.
Net effect after both changes is still being validated — see the lab
sweep's out-of-sample numbers for ma-greeks-credit-spread before
trusting this as tuned rather than in-progress. The trend + width
filter is deliberately selective — meant to fire a handful of times a
month, not every week."""
register_strategy("ma-greeks-credit-spread", "SELL CREDIT SPREAD (MA Trend + Delta, Defined Risk)")(_sig_ma_greeks_credit_spread)


@register_strategy("expiry-theta-crush-seller", "SELL OTM (Expiry-Day Theta Crush)")
def _sig_expiry_theta_crush(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """The seller's side of expiry day: whatever OTM time value is left at
    the open dies to intrinsic (usually zero) by the close. Sells an option
    150 points OTM on the side the CPR defends — a CALL when price is below
    TC with a not-bullish bias, a PUT when above BC with a not-bearish
    bias. Skips NARROW-percentile sessions: that is gamma-blast territory,
    where a coiled market can blow through an OTM short in one afternoon.
    Exits at the close at real intrinsic settlement value."""
    today = ctx.candles[ctx.index].dt
    if not is_expiry_day(ctx.symbol, today):
        return None
    if _percentile_width_regime(ctx) == "NARROW":
        return None
    td = levels.two_day
    cpr = levels.cpr_today
    if entry_spot < cpr.tc and (td is None or td.direction != "BULLISH"):
        return LegShape(kind="SINGLE", option_type="CE", side="SELL", strike_offset=150, direction=-1, hold="EXPIRY_DAY")
    if entry_spot > cpr.bc and (td is None or td.direction != "BEARISH"):
        return LegShape(kind="SINGLE", option_type="PE", side="SELL", strike_offset=150, direction=1, hold="EXPIRY_DAY")
    return None


@register_strategy("camarilla-h4l4-breakout", "BUY OPTION (Camarilla H4/L4 Breakout)")
def _sig_camarilla_breakout(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Camarilla range-blowout play: H4/L4 are the outermost Camarilla
    levels — an open beyond them signals a trend day. Buys an ATM CALL on
    an open above H4, an ATM PUT on an open below L4. Strike: ATM. Held
    until stop/target/weekly expiry."""
    cam = levels.camarilla
    if entry_spot > cam.h4:
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1)
    if entry_spot < cam.l4:
        return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1)
    return None


# -- Book-accurate PivotBoss strategies [Ochoa 2010] --------------------------
#
# These apply the two rules the earlier CPR strategies omitted: a two-day
# bias is only provisional until the OPENING PRINT confirms or rejects it
# (Ch. 6), and the pivot trend is defined by which side of S1/R1 the market
# keeps closing on (Ch. 5). They also scale their targets to the CPR width
# (~p.196) instead of using one fixed target for every regime.

@register_strategy("pivotboss-confirmed-bias", "PIVOTBOSS · Confirmed Two-Day Bias")
def _sig_pivotboss_confirmed(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Trades the two-day CPR bias only once the day's opening print has
    CONFIRMED it — buys an ATM call when a bullish relationship opens at or
    above the CPR floor, buys an ATM put when a bearish one opens at or
    below the CPR top. Skips rejected and pending days entirely, which is
    the filter the plain two-day strategies lack. Targets scale with the CPR
    width."""
    conf = _confirmed_bias(entry_spot, levels, ctx)
    if not conf or conf.status != "CONFIRMED":
        return None
    scale = _width_target_scale(ctx)
    if conf.effective_direction == "BULLISH":
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1, target_scale=scale)
    if conf.effective_direction == "BEARISH":
        return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1, target_scale=scale)
    return None


@register_strategy("pivotboss-rejection-fade", "PIVOTBOSS · Opening Rejection Fade")
def _sig_pivotboss_rejection(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Trades the opposite case: days where the opening print REJECTS the
    two-day bias. The book singles this out — a bearish relationship that
    opens above the range signals sentiment shifted overnight, and some of
    the biggest rallies follow. Buys in the direction of the rejection (the
    inverse of the original bias), which the relationship alone would have
    called exactly backwards."""
    conf = _confirmed_bias(entry_spot, levels, ctx)
    if not conf or conf.status != "REJECTED":
        return None
    scale = _width_target_scale(ctx)
    if conf.effective_direction == "BULLISH":
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1, target_scale=scale)
    return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1, target_scale=scale)


@register_strategy("pivotboss-trend-pullback", "PIVOTBOSS · Trend Pullback (S1/R1)")
def _sig_pivotboss_trend_pullback(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Pivot Trend Analysis, the book's core discipline: buy at support in an
    uptrend, sell at resistance in a downtrend. An uptrend holds while price
    closes above S1 — so on an uptrend day that opens back down at S1 or
    inside the CPR (a pullback into value), buy an ATM call. Mirrored for a
    downtrend rallying to R1 or the CPR. Trades only with the established
    trend, never against it."""
    trend = _pivot_trend(ctx)
    if not trend or trend.state == "NEUTRAL":
        return None
    floor, cpr_today = levels.floor, levels.cpr_today
    scale = _width_target_scale(ctx)
    if trend.state == "BULLISH":
        # pullback into value: open at/below the CPR top but holding above S1
        if floor.s1 <= entry_spot <= cpr_today.tc:
            return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1, target_scale=scale)
        return None
    # bearish: rally into resistance but still capped below R1
    if cpr_today.bc <= entry_spot <= floor.r1:
        return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1, target_scale=scale)
    return None


# -- The four classic CPR trade types (Pivot Boss) ---------------------------
#
# Each type is a distinct read of the same CPR structure: continuation off
# the pivot zone (bounce), continuation through it (breakout), a range bet
# inside it (fade), and mean-reversion toward an untested one (magnet).

def _is_ascending(levels: DailyLevels) -> bool:
    td = levels.two_day
    return td is not None and td.relationship in ("HIGHER_VALUE", "OVERLAPPING_HIGHER_VALUE")


def _is_descending(levels: DailyLevels) -> bool:
    td = levels.two_day
    return td is not None and td.relationship in ("LOWER_VALUE", "OVERLAPPING_LOWER_VALUE")


@register_strategy("cpr-type1-bounce", "CPR Type 1 · Bounce (Trend Continuation)")
def _sig_cpr_type1_bounce(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Type 1 — pivot-zone bounce: in an ascending two-day CPR (bullish
    structure), a day that opens back INSIDE the CPR is a pullback into
    value — buy the bounce with an ATM CALL, expecting the trend to resume
    off the pivot zone. Mirror in a descending CPR: an open back inside is
    a rally into resistance — buy an ATM PUT. Strike: ATM. Held until
    stop/target/weekly expiry."""
    cpr = levels.cpr_today
    inside = cpr.bc <= entry_spot <= cpr.tc
    if not inside:
        return None
    if _is_ascending(levels):
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1)
    if _is_descending(levels):
        return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1)
    return None


@register_strategy("cpr-type2-breakout", "CPR Type 2 · Two-Day Breakout")
def _sig_cpr_type2_breakout(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Type 2 — structure-confirmed breakout: the two-day CPR relationship
    and price agree. An ascending CPR with the open already above TC buys
    an ATM CALL (value shifted higher AND price accepted above it); a
    descending CPR with the open below BC buys an ATM PUT. Unlike the
    percentile-breakout strategies, no narrow-width filter — this is the
    broad Type 2 play. Strike: ATM. Held until stop/target/weekly expiry."""
    cpr = levels.cpr_today
    if _is_ascending(levels) and entry_spot > cpr.tc:
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1)
    if _is_descending(levels) and entry_spot < cpr.bc:
        return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1)
    return None


@register_strategy("cpr-type3-wide-fade", "CPR Type 3 · Wide-Range Fade")
def _sig_cpr_type3_wide_fade(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Type 3 — wide-CPR fade: a WIDE CPR forecasts a sideways session, and
    an open still inside it means price is boxed by value. Sells the range
    with a 200-point-wide short strangle (call at ATM+200, put at ATM−200)
    and lets the non-move decay the premium. Stricter than the weekly iron
    condor: fires only on WIDE (not NORMAL) regimes and only when the open
    is inside the CPR. Exits on wing threat (stop %), premium decay
    (target %), or weekly expiry."""
    cpr = levels.cpr_today
    if levels.width.regime == "WIDE" and cpr.bc <= entry_spot <= cpr.tc:
        return LegShape(kind="CONDOR")
    return None


# -- Weekly-income family ----------------------------------------------------
#
# Built for a "1-2% per week" objective specifically, which is a different
# problem from maximising total return. Directional option BUYING produces
# lumpy equity — a few explosive weeks carrying many small losing ones — so
# its median week is negative even when its total return is large. Premium
# SELLING inverts that: many small positive weeks, occasional sharp losses.
# Only the second shape can plausibly deliver a positive result most weeks,
# so these strategies sell premium, fire on a fixed weekly cadence for
# maximum week coverage, and use wider wings to cap the tail risk that
# ruins premium sellers.

@register_strategy("weekly-income-strangle", "WEEKLY INCOME · Short Strangle")
def _sig_weekly_income_strangle(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Sells a wide strangle on the first trading day of each week and holds
    into expiry, collecting a full week of decay. Wings sit 300 points OTM
    (roughly 1.2% on NIFTY) — far enough that an ordinary week expires
    worthless, close enough to be worth selling. Fires nearly every week by
    design: weekly income needs weekly presence, and a strategy that only
    trades occasionally cannot produce a result most weeks."""
    today = ctx.candles[ctx.index].dt
    if today.weekday() != 0:  # Monday only — one position per week
        return None
    return LegShape(kind="CONDOR", wing_offset=300)


@register_strategy("weekly-income-strangle-wide", "WEEKLY INCOME · Wide Strangle (defensive)")
def _sig_weekly_income_strangle_wide(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Same weekly cadence as weekly-income-strangle but with 500-point
    wings (~2% OTM). Collects less premium per week in exchange for a much
    larger buffer before either wing is threatened — the trade-off that
    decides whether a premium seller survives a trending month."""
    today = ctx.candles[ctx.index].dt
    if today.weekday() != 0:
        return None
    return LegShape(kind="CONDOR", wing_offset=500)


@register_strategy("weekly-income-calm-filter", "WEEKLY INCOME · Strangle (calm weeks only)")
def _sig_weekly_income_calm(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Weekly strangle that stands aside when the setup argues for a
    trending week: skips NARROW-percentile CPR sessions (which forecast
    expansion) and skips weeks opening outside the CPR (already trending).
    Trades fewer weeks than the plain version, aiming for a higher share of
    clean ones — the premium seller's core trade-off between coverage and
    quality."""
    today = ctx.candles[ctx.index].dt
    if today.weekday() != 0:
        return None
    if _percentile_width_regime(ctx) == "NARROW":
        return None
    cpr = levels.cpr_today
    if not (cpr.bc <= entry_spot <= cpr.tc):
        return None
    return LegShape(kind="CONDOR", wing_offset=400)


@register_strategy("cpr-type4-virgin-magnet", "CPR Type 4 · Virgin CPR Magnet")
def _sig_cpr_type4_virgin_magnet(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Type 4 — virgin CPR magnet: a CPR that price never traded into all
    session stays 'virgin' and tends to act as a magnet on later days.
    Checks yesterday's CPR (computed from the day before yesterday's OHLC):
    if yesterday's entire range stayed ABOVE it, the untested zone below
    pulls price down — buy an ATM PUT; if the range stayed BELOW it, the
    magnet above pulls price up — buy an ATM CALL. Strike: ATM. Held until
    stop/target/weekly expiry."""
    i = ctx.index
    if i < 3:
        return None
    day_before_yday = ctx.candles[i - 2]
    yday = ctx.candles[i - 1]
    virgin = calc_cpr(day_before_yday.high, day_before_yday.low, day_before_yday.close)
    if yday.low > virgin.tc:
        return LegShape(kind="SINGLE", option_type="PE", side="BUY", strike_offset=0, direction=-1)
    if yday.high < virgin.bc:
        return LegShape(kind="SINGLE", option_type="CE", side="BUY", strike_offset=0, direction=1)
    return None


_STRATEGY_LABELS = {key: d.label for key, d in STRATEGY_REGISTRY.items()}


def _strike_for(entry_spot: float, option_type: Literal["CE", "PE"], offset: int,
                symbol: str | None = None) -> float:
    atm = _round_strike(entry_spot, symbol)
    if offset == 0:
        return atm
    return atm + offset if option_type == "CE" else atm - offset


def _leg_pnl(
    entry_spot: float, strike: float, option_type: Literal["CE", "PE"], side: Literal["BUY", "SELL"],
    entry_date: date, expiry: date, path: list[Candle], sigma: float,
    stop_loss_pct: float, target_pct: float, direction: int,
) -> tuple[float, float, date]:
    """Simulates one option leg day-by-day (re-pricing via Black-Scholes on
    each subsequent real close) until stop/target trips or expiry is reached.

    stop_loss_pct/target_pct are checked against the *underlying's* real move
    from entry (the standard convention — "1.5% SL" means a 1.5% move in
    NIFTY, not in the option premium, which is far more volatile day-to-day
    and would blow through a same-sized threshold almost immediately on the
    next daily close). `direction` is +1 if the position profits from the
    underlying rising (long calls) or -1 if it profits from the underlying
    NOT rising (short calls) — it decides which way a move counts as
    favorable vs. adverse. Realized P&L still comes from the real premium
    (via Black-Scholes) at whichever day the exit condition fires.
    Returns (entry_premium, exit_premium, closed_date)."""
    entry_t = max((expiry - entry_date).days / 365.0, 1.0 / 365.0)
    entry_price = black_scholes(entry_spot, strike, entry_t, sigma, option_type).price

    exit_price = entry_price
    closed = expiry
    for c in path:
        if c.dt <= entry_date:
            continue
        t = max((expiry - c.dt).days / 365.0, 1.0 / 365.0)
        px = black_scholes(c.close, strike, t, sigma, option_type).price
        spot_move_pct = direction * (c.close - entry_spot) / entry_spot * 100
        exit_price = px
        closed = c.dt
        if spot_move_pct >= target_pct or spot_move_pct <= -stop_loss_pct or c.dt >= expiry:
            break
    return entry_price, exit_price, closed


def _condor_pnl(
    entry_spot: float, ce_strike: float, pe_strike: float, entry_date: date, expiry: date,
    path: list[Candle], sigma: float, stop_loss_pct: float, target_pct: float,
) -> tuple[float, float, date]:
    """Iron condor is a range bet, not directional, so its stop isn't "the
    underlying moved against me" in a single direction — it's "the underlying
    threatened a wing" (an absolute move past stop_loss_pct from entry either
    way). Target is combined-premium decay (the normal way a credit spread's
    profit is realized) rather than a further underlying move."""
    entry_t = max((expiry - entry_date).days / 365.0, 1.0 / 365.0)
    ce_entry = black_scholes(entry_spot, ce_strike, entry_t, sigma, "CE").price
    pe_entry = black_scholes(entry_spot, pe_strike, entry_t, sigma, "PE").price
    entry_premium = ce_entry + pe_entry

    exit_premium = entry_premium
    closed = expiry
    for c in path:
        if c.dt <= entry_date:
            continue
        t = max((expiry - c.dt).days / 365.0, 1.0 / 365.0)
        ce_px = black_scholes(c.close, ce_strike, t, sigma, "CE").price
        pe_px = black_scholes(c.close, pe_strike, t, sigma, "PE").price
        exit_premium = ce_px + pe_px
        closed = c.dt
        breach_pct = abs(c.close - entry_spot) / entry_spot * 100
        decay_pct = (entry_premium - exit_premium) / entry_premium * 100
        if breach_pct >= stop_loss_pct or decay_pct >= target_pct or c.dt >= expiry:
            break
    return entry_premium, exit_premium, closed


@dataclass
class _SpreadFill:
    """The four individual leg prices behind a SPREAD trade's net credit —
    kept separate from the aggregate entry_net/exit_net so the caller can
    cost each of the four real orders (sell-open, buy-open, buy-close,
    sell-close) with _estimate_leg_charges instead of one blended rate."""
    entry_net: float
    exit_net: float
    closed: date
    short_entry: float
    long_entry: float
    short_exit: float
    long_exit: float


def _spread_pnl(
    entry_spot: float, short_strike: float, long_strike: float, option_type: Literal["CE", "PE"],
    entry_date: date, expiry: date, path: list[Candle], sigma: float,
    stop_loss_pct: float, target_pct: float, direction: int,
) -> _SpreadFill:
    """A defined-risk vertical credit spread: sells `short_strike`, buys
    `long_strike` further OTM as protection, both the same option_type and
    expiry. Unlike _condor_pnl's naked strangle, the bought leg structurally
    caps max loss at the strike width minus the net credit received, however
    far the underlying moves — there is no equivalent of the condor's
    triple-digit drawdowns here by construction, only what stop/target/expiry
    realize along the way. Net premium tracked is short leg minus long leg
    (the credit received); stop/target read the *underlying's* real move like
    _leg_pnl, in the one direction this spread is actually exposed to (a
    credit spread's risk is one-sided, unlike a condor's two-sided one)."""
    entry_t = max((expiry - entry_date).days / 365.0, 1.0 / 365.0)
    short_entry = black_scholes(entry_spot, short_strike, entry_t, sigma, option_type).price
    long_entry = black_scholes(entry_spot, long_strike, entry_t, sigma, option_type).price
    entry_net = short_entry - long_entry

    short_exit, long_exit = short_entry, long_entry
    exit_net = entry_net
    closed = expiry
    for c in path:
        if c.dt <= entry_date:
            continue
        t = max((expiry - c.dt).days / 365.0, 1.0 / 365.0)
        short_exit = black_scholes(c.close, short_strike, t, sigma, option_type).price
        long_exit = black_scholes(c.close, long_strike, t, sigma, option_type).price
        exit_net = short_exit - long_exit
        closed = c.dt
        spot_move_pct = direction * (c.close - entry_spot) / entry_spot * 100
        # A spread whose two legs price identically collects no credit at all
        # (black_scholes floors both at the same minimum when they're far
        # enough OTM), so entry_net is 0 and the decay ratio is undefined.
        # Guard it the way _iron_pnl already does: with no credit there is
        # nothing to decay, so the position can only exit on stop or expiry.
        # Left unguarded this raised ZeroDivisionError out of run_backtest and
        # took both credit-spread strategies out of the Strategy Lab entirely.
        decay_pct = (entry_net - exit_net) / entry_net * 100 if entry_net else 0.0
        if spot_move_pct <= -stop_loss_pct or decay_pct >= target_pct or c.dt >= expiry:
            break
    return _SpreadFill(entry_net, exit_net, closed, short_entry, long_entry, short_exit, long_exit)


def _long_straddle_pnl(
    entry_spot: float, entry_date: date, expiry: date, path: list[Candle], sigma: float,
    stop_loss_pct: float, target_pct: float, time_stop_days: int, symbol: str | None = None,
) -> tuple[float, float, date]:
    """Buys the ATM call and ATM put — long premium, long gamma, long vega,
    SHORT theta. The mirror of _condor_pnl's structure but not of its logic.

    A seller's exits and a buyer's exits are genuinely different, not sign
    flips of each other. _condor_pnl stops when the underlying threatens a
    wing and targets when premium DECAYS; the buyer here stops when the
    combined premium decays (that IS the loss) and targets when it EXPANDS.
    Both are read off the combined premium rather than the underlying's
    direction, because a straddle is a bet on movement, not on which way.

    The time stop matters more than it looks. A long straddle that hasn't
    moved is losing money every session it stays open, so "nothing happened"
    is a losing outcome that has to be cut, not a neutral one to wait out.
    stop_loss_pct/target_pct are read here as percentages of the PREMIUM
    PAID, which is the only quantity a buyer's risk is actually denominated
    in — unlike the single-leg _leg_pnl, where they measure the underlying.
    """
    atm = _round_strike(entry_spot, symbol)
    entry_t = max((expiry - entry_date).days / 365.0, 1.0 / 365.0)
    entry_premium = (black_scholes(entry_spot, atm, entry_t, sigma, "CE").price
                     + black_scholes(entry_spot, atm, entry_t, sigma, "PE").price)

    exit_premium = entry_premium
    closed = expiry
    sessions = 0
    for c in path:
        if c.dt <= entry_date:
            continue
        sessions += 1
        t = max((expiry - c.dt).days / 365.0, 1.0 / 365.0)
        exit_premium = (black_scholes(c.close, atm, t, sigma, "CE").price
                        + black_scholes(c.close, atm, t, sigma, "PE").price)
        closed = c.dt
        move_pct = (exit_premium - entry_premium) / entry_premium * 100 if entry_premium else 0.0
        if move_pct <= -stop_loss_pct or move_pct >= target_pct or c.dt >= expiry:
            break
        if time_stop_days and sessions >= time_stop_days:
            break
    return entry_premium, exit_premium, closed


@dataclass
class _IronFill:
    """The eight individual leg prices behind an IRON trade's net credit —
    four legs opened and the same four closed. Kept separate from the
    aggregate nets so the caller can cost each of the eight real orders with
    _estimate_leg_charges rather than one blended rate, the same reason
    _SpreadFill exists. Charges matter disproportionately here: an iron
    condor places twice the orders of a vertical for a similar credit, and
    pretending otherwise is how backtests flatter four-leg structures."""
    entry_net: float
    exit_net: float
    closed: date
    max_loss: float
    entry_legs: dict
    exit_legs: dict


def _iron_pnl(
    entry_spot: float, inner_offset: int, wing_offset: int, entry_date: date, expiry: date,
    path: list[Candle], sigma: float, stop_loss_pct: float, target_pct: float,
    symbol: str | None = None,
) -> _IronFill:
    """A genuine defined-risk iron condor: sells a strangle and buys a
    further-OTM wing beyond EACH short leg, all four the same expiry.

    Unlike _condor_pnl's naked strangle, the two bought legs structurally cap
    max loss at the wing width minus the net credit received, however far the
    underlying travels — the naked version's unbounded tail is exactly what
    the protection is being paid for. Unlike _spread_pnl's one-sided vertical,
    risk here is two-sided, so the stop is an ABSOLUTE move past stop_loss_pct
    in either direction (matching _condor_pnl's range-bet logic) rather than a
    directional one, and the target is decay of the combined credit.

    inner_offset 0 puts both shorts at ATM — an iron fly, which collects a far
    larger credit for a far narrower profit zone. Returns the fill with every
    leg price kept so the caller can charge all eight orders.
    """
    atm = _round_strike(entry_spot, symbol)
    ce_short = atm + inner_offset
    pe_short = atm - inner_offset
    ce_long = ce_short + wing_offset
    pe_long = pe_short - wing_offset

    def price(spot: float, strike: float, t: float, opt: Literal["CE", "PE"]) -> float:
        return black_scholes(spot, strike, t, sigma, opt).price

    def legs_at(spot: float, t: float) -> dict:
        return {
            "ce_short": price(spot, ce_short, t, "CE"),
            "pe_short": price(spot, pe_short, t, "PE"),
            "ce_long": price(spot, ce_long, t, "CE"),
            "pe_long": price(spot, pe_long, t, "PE"),
        }

    def net_of(legs: dict) -> float:
        return (legs["ce_short"] + legs["pe_short"]) - (legs["ce_long"] + legs["pe_long"])

    entry_t = max((expiry - entry_date).days / 365.0, 1.0 / 365.0)
    entry_legs = legs_at(entry_spot, entry_t)
    entry_net = net_of(entry_legs)
    # Structural cap: only one side can be breached at expiry, so the worst
    # case is one wing's width less whatever credit was taken in.
    max_loss = max(wing_offset - entry_net, 0.0)

    exit_legs = dict(entry_legs)
    exit_net = entry_net
    closed = expiry
    for c in path:
        if c.dt <= entry_date:
            continue
        t = max((expiry - c.dt).days / 365.0, 1.0 / 365.0)
        exit_legs = legs_at(c.close, t)
        exit_net = net_of(exit_legs)
        closed = c.dt
        breach_pct = abs(c.close - entry_spot) / entry_spot * 100
        decay_pct = (entry_net - exit_net) / entry_net * 100 if entry_net else 0.0
        if breach_pct >= stop_loss_pct or decay_pct >= target_pct or c.dt >= expiry:
            break

    # Even when the modeled path overshoots it, the bought wings mean the
    # position cannot lose more than the cap — enforce it rather than letting
    # Black-Scholes noise report a loss the structure makes impossible.
    if entry_net - exit_net < -max_loss:
        exit_net = entry_net + max_loss
    return _IronFill(entry_net, exit_net, closed, max_loss, entry_legs, exit_legs)


def _same_day_pnl(
    entry_spot: float, strike: float, option_type: Literal["CE", "PE"],
    hold: str, today: Candle, sigma: float, symbol: str | None = None,
) -> tuple[float, float, date]:
    """Same-session exits approximated on daily bars: enter on the candle's
    real open, exit on its real close. EXPIRY_DAY prices the entry with one
    day of life left and exits at intrinsic value — actual expiry
    settlement, which is exact, not modeled. INTRADAY exits via
    Black-Scholes with one day of time value decayed. Stop/target
    percentages don't apply — there is no multi-day path to walk.
    Returns (entry_premium, exit_premium, closed_date)."""
    if hold == "EXPIRY_DAY":
        entry_p = black_scholes(entry_spot, strike, 1.0 / 365.0, sigma, option_type).price
        exit_p = max(0.0, today.close - strike) if option_type == "CE" else max(0.0, strike - today.close)
    else:  # INTRADAY
        expiry = next_expiry(symbol, today.dt)
        entry_t = max((expiry - today.dt).days / 365.0, 1.0 / 365.0)
        entry_p = black_scholes(entry_spot, strike, entry_t, sigma, option_type).price
        exit_t = max(entry_t - 1.0 / 365.0, 1.0 / 365.0)
        exit_p = black_scholes(today.close, strike, exit_t, sigma, option_type).price
    return entry_p, exit_p, today.dt


HoldMode = Literal["strategy", "intraday", "weekly", "custom"]


def _apply_hold_mode(shape: LegShape, hold_mode: str) -> LegShape:
    """Override how long a position is carried, independently of what the
    strategy itself asked for. 'strategy' leaves each strategy's own choice
    alone; the others force every trade onto the same footing so the SAME
    signal can be compared intraday vs weekly vs a fixed holding period.

    A condor or spread is left alone: both are multi-day premium-decay
    structures whose P&L model has no same-day form (see _condor_pnl /
    _spread_pnl)."""
    if hold_mode == "strategy" or shape.kind in ("CONDOR", "SPREAD", "IRON", "LONG_STRADDLE"):
        return shape
    if hold_mode == "intraday":
        return replace(shape, hold="INTRADAY")
    return replace(shape, hold="SWING")  # weekly / custom both walk the path


def _effective_expiry(entry_dt: date, hold_mode: str, custom_hold_days: int,
                      symbol: str | None = None) -> date:
    """The date a position is force-closed on. Weekly uses this index's REAL
    weekly expiry — Tuesday for NIFTY, Thursday for SENSEX, and the monthly
    for BANKNIFTY, which has no weekly (see contract_spec). Custom holds a
    fixed number of calendar days."""
    if hold_mode == "custom":
        return entry_dt + timedelta(days=max(1, custom_hold_days))
    return next_expiry(symbol, entry_dt)


def _simulate_trades(
    candles: list[Candle], strategy: str, position_size_lots: int,
    stop_loss_pct: float, target_pct: float, include_costs: bool, starting_capital: float,
    hold_mode: str = "strategy", custom_hold_days: int = 5, symbol: str = "NIFTY50",
) -> list[Trade]:
    strategy_def = STRATEGY_REGISTRY[strategy]
    lot = lot_size(symbol)
    trades: list[Trade] = []
    closes = [c.close for c in candles]
    last_exit_date: Optional[date] = None
    equity = starting_capital

    for i in range(2, len(candles) - 1):
        if equity <= 0:
            break  # account wiped out — a real trader stops here, not carries on
        today = candles[i]
        if last_exit_date and today.dt <= last_exit_date:
            continue  # one position at a time
        prior, before = candles[i - 1], candles[i - 2]
        levels = daily_levels(prior.high, prior.low, prior.close, before.high, before.low, before.close)
        entry_spot = today.open
        shape = strategy_def.signal_fn(entry_spot, levels, StrategyContext(candles=candles, index=i, symbol=symbol))
        if not shape:
            continue
        shape = _apply_hold_mode(shape, hold_mode)

        expiry = _effective_expiry(today.dt, hold_mode, custom_hold_days, symbol)
        path = [c for c in candles[i:] if c.dt <= expiry + timedelta(days=1)]
        sigma = realized_volatility(closes[: i + 1])
        # Width-aware target: strategies that read the CPR width scale how far
        # they reach (see LegShape.target_scale). Default 1.0 = unchanged.
        eff_target_pct = target_pct * shape.target_scale
        eff_stop_pct = stop_loss_pct * shape.stop_scale

        costs = 0.0
        if shape.kind == "CONDOR":
            ce_strike = _round_strike(entry_spot, symbol) + shape.wing_offset
            pe_strike = _round_strike(entry_spot, symbol) - shape.wing_offset
            entry_p, exit_p, closed_date = _condor_pnl(
                entry_spot, ce_strike, pe_strike, today.dt, expiry, path, sigma, eff_stop_pct, eff_target_pct
            )
            pnl = (entry_p - exit_p) * lot * position_size_lots
            if include_costs:
                costs = (entry_p + exit_p) * lot * position_size_lots * COST_FRACTION
        elif shape.kind == "LONG_STRADDLE":
            entry_p, exit_p, closed_date = _long_straddle_pnl(
                entry_spot, today.dt, expiry, path, sigma, eff_stop_pct, eff_target_pct,
                shape.time_stop_days, symbol,
            )
            # Long premium: P&L is exit minus entry, the opposite sign to
            # every credit structure above.
            pnl = (exit_p - entry_p) * lot * position_size_lots
            if include_costs:
                # Four real orders (buy CE, buy PE, sell both to close), and
                # charges hurt a buyer disproportionately: they come off a
                # premium that is usually a small number, so the same rupee
                # cost is a far larger fraction of the trade than it is for a
                # seller collecting a wide credit.
                qty = lot * position_size_lots
                half_entry, half_exit = entry_p / 2.0, exit_p / 2.0
                costs = (
                    _estimate_leg_charges("BUY", half_entry * qty) * 2
                    + _estimate_leg_charges("SELL", half_exit * qty) * 2
                )
        elif shape.kind == "IRON":
            fill = _iron_pnl(
                entry_spot, shape.inner_offset, shape.wing_offset, today.dt, expiry, path, sigma,
                eff_stop_pct, eff_target_pct, symbol,
            )
            entry_p, exit_p, closed_date = fill.entry_net, fill.exit_net, fill.closed
            pnl = (entry_p - exit_p) * lot * position_size_lots  # net credit convention
            if include_costs:
                # Eight real orders, not four: both shorts and both wings are
                # opened and closed. Costed per leg (see _estimate_leg_charges)
                # because a four-leg structure's charges are the difference
                # between a profitable and an unprofitable premium-selling
                # program, and a blended rate hides that.
                qty = lot * position_size_lots
                costs = (
                    _estimate_leg_charges("SELL", fill.entry_legs["ce_short"] * qty)
                    + _estimate_leg_charges("SELL", fill.entry_legs["pe_short"] * qty)
                    + _estimate_leg_charges("BUY", fill.entry_legs["ce_long"] * qty)
                    + _estimate_leg_charges("BUY", fill.entry_legs["pe_long"] * qty)
                    + _estimate_leg_charges("BUY", fill.exit_legs["ce_short"] * qty)
                    + _estimate_leg_charges("BUY", fill.exit_legs["pe_short"] * qty)
                    + _estimate_leg_charges("SELL", fill.exit_legs["ce_long"] * qty)
                    + _estimate_leg_charges("SELL", fill.exit_legs["pe_long"] * qty)
                )
        elif shape.kind == "SPREAD":
            short_strike = _strike_for(entry_spot, shape.option_type, shape.strike_offset, symbol)
            long_strike = _strike_for(entry_spot, shape.option_type, shape.strike_offset + shape.wing_offset, symbol)
            fill = _spread_pnl(
                entry_spot, short_strike, long_strike, shape.option_type, today.dt, expiry, path, sigma,
                eff_stop_pct, eff_target_pct, direction=shape.direction,
            )
            entry_p, exit_p, closed_date = fill.entry_net, fill.exit_net, fill.closed
            pnl = (entry_p - exit_p) * lot * position_size_lots  # SELL-the-spread: net credit convention
            if include_costs:
                # Itemized real per-order charges (see _estimate_leg_charges) for
                # each of the 4 real orders a spread trade actually places —
                # exact, not the blended COST_FRACTION the other kinds use.
                qty = lot * position_size_lots
                costs = (
                    _estimate_leg_charges("SELL", fill.short_entry * qty)   # open: sell the short leg
                    + _estimate_leg_charges("BUY", fill.long_entry * qty)   # open: buy the protective leg
                    + _estimate_leg_charges("BUY", fill.short_exit * qty)   # close: buy back the short leg
                    + _estimate_leg_charges("SELL", fill.long_exit * qty)   # close: sell the protective leg
                )
        else:
            strike = _strike_for(entry_spot, shape.option_type, shape.strike_offset, symbol)
            if shape.hold == "SWING":
                entry_p, exit_p, closed_date = _leg_pnl(
                    entry_spot, strike, shape.option_type, shape.side, today.dt, expiry, path, sigma,
                    eff_stop_pct, eff_target_pct, direction=shape.direction,
                )
            else:
                entry_p, exit_p, closed_date = _same_day_pnl(
                    entry_spot, strike, shape.option_type, shape.hold, today, sigma, symbol,
                )
            pnl = (entry_p - exit_p) if shape.side == "SELL" else (exit_p - entry_p)
            pnl *= lot * position_size_lots
            if include_costs:
                costs = (entry_p + exit_p) * lot * position_size_lots * COST_FRACTION

        pnl -= costs

        pnl = round(pnl, 2)
        trades.append(Trade(
            opened_at=datetime.combine(today.dt, datetime.min.time(), tzinfo=timezone.utc),
            closed_at=datetime.combine(closed_date, datetime.min.time(), tzinfo=timezone.utc),
            label=strategy_def.label, pnl=pnl, result="WIN" if pnl > 0 else "LOSS",
            entry_price=round(entry_p, 2), exit_price=round(exit_p, 2),
        ))
        last_exit_date = closed_date
        equity += pnl

    return trades


def _simulate_futures_trades(
    candles: list[Candle], strategy: str, position_size_lots: int, include_costs: bool,
    starting_capital: float, hold_mode: str = "strategy", custom_hold_days: int = 5,
    symbol: str = "NIFTY50",
) -> list[Trade]:
    """Futures need no premium model — P&L is the real spot move itself."""
    strategy_def = STRATEGY_REGISTRY[strategy]
    lot = lot_size(symbol)
    trades: list[Trade] = []
    last_exit_date: Optional[date] = None
    equity = starting_capital
    for i in range(2, len(candles) - 1):
        if equity <= 0:
            break
        today = candles[i]
        if last_exit_date and today.dt <= last_exit_date:
            continue
        prior, before = candles[i - 1], candles[i - 2]
        levels = daily_levels(prior.high, prior.low, prior.close, before.high, before.low, before.close)
        entry_spot = today.open
        shape = strategy_def.signal_fn(entry_spot, levels, StrategyContext(candles=candles, index=i, symbol=symbol))
        if not shape or shape.kind in ("CONDOR", "IRON", "LONG_STRADDLE"):
            continue  # non-directional structures have no directional futures equivalent
        shape = _apply_hold_mode(shape, hold_mode)
        direction = shape.direction  # bearish setups short the future, bullish setups go long

        expiry = _effective_expiry(today.dt, hold_mode, custom_hold_days, symbol)
        exit_candle = today  # same-day holds exit on this candle's own close
        if shape.hold == "SWING":
            for c in candles[i:]:
                if c.dt <= today.dt:
                    continue
                exit_candle = c
                if c.dt >= expiry:
                    break
        pnl = direction * (exit_candle.close - entry_spot) * lot * position_size_lots
        if include_costs:
            pnl -= abs(entry_spot + exit_candle.close) * lot * position_size_lots * COST_FRACTION
        pnl = round(pnl, 2)
        trades.append(Trade(
            opened_at=datetime.combine(today.dt, datetime.min.time(), tzinfo=timezone.utc),
            closed_at=datetime.combine(exit_candle.dt, datetime.min.time(), tzinfo=timezone.utc),
            label=("SELL FUTURES" if direction < 0 else "BUY FUTURES"), pnl=pnl, result="WIN" if pnl > 0 else "LOSS",
            entry_price=round(entry_spot, 2), exit_price=round(exit_candle.close, 2),
        ))
        last_exit_date = exit_candle.dt
        equity += pnl
    return trades


def _compute_weekly_stats(starting_capital: float, trades: list[Trade]) -> Optional[WeeklyStats]:
    """Groups realized P&L into ISO weeks and measures each week against the
    equity going into it. Weeks with no trades are counted as 0% — flat
    weeks are real weeks and excluding them would inflate the hit rate for
    strategies that only fire occasionally."""
    if not trades or starting_capital <= 0:
        return None
    by_week: dict[tuple[int, int], float] = {}
    for tr in trades:
        key = tr.closed_at.isocalendar()[:2]  # (ISO year, ISO week)
        by_week[key] = by_week.get(key, 0.0) + tr.pnl

    first, last = min(by_week), max(by_week)
    # Walk every calendar week in the span so idle weeks aren't silently skipped
    week_keys: list[tuple[int, int]] = []
    cursor = date.fromisocalendar(first[0], first[1], 1)
    end = date.fromisocalendar(last[0], last[1], 1)
    while cursor <= end:
        week_keys.append(cursor.isocalendar()[:2])
        cursor += timedelta(weeks=1)

    equity = starting_capital
    returns: list[float] = []
    for key in week_keys:
        pnl = by_week.get(key, 0.0)
        if equity <= 0:
            break  # account wiped out; later weeks are not meaningful
        returns.append(pnl / equity * 100)
        equity += pnl
    if not returns:
        return None

    ordered = sorted(returns)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2

    worst_streak = streak = 0
    for r in returns:
        streak = streak + 1 if r < 0 else 0
        worst_streak = max(worst_streak, streak)

    n = len(returns)
    return WeeklyStats(
        total_weeks=n,
        avg_return_pct=round(sum(returns) / n, 2),
        median_return_pct=round(median, 2),
        pct_weeks_profitable=round(sum(1 for r in returns if r > 0) / n * 100, 1),
        pct_weeks_hit_1pct=round(sum(1 for r in returns if r >= 1.0) / n * 100, 1),
        pct_weeks_hit_2pct=round(sum(1 for r in returns if r >= 2.0) / n * 100, 1),
        best_week_pct=round(max(returns), 2),
        worst_week_pct=round(min(returns), 2),
        max_consecutive_losing_weeks=worst_streak,
    )


def _compute_metrics(starting_capital: float, trades: list[Trade]) -> BacktestResult:
    equity = [starting_capital]
    for tr in trades:
        equity.append(equity[-1] + tr.pnl)

    wins = [tr.pnl for tr in trades if tr.pnl > 0]
    losses = [tr.pnl for tr in trades if tr.pnl <= 0]

    net_profit = sum(tr.pnl for tr in trades)
    net_profit_pct = (net_profit / starting_capital) * 100 if starting_capital else 0.0
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else float("inf")

    # per-trade returns (as a fraction of starting capital) for Sharpe/Sortino/vol
    returns = [tr.pnl / starting_capital for tr in trades] if starting_capital else []
    mean_ret = sum(returns) / len(returns) if returns else 0.0
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns) if returns else 0.0
    std_dev = variance ** 0.5
    downside = [r for r in returns if r < 0]
    downside_var = sum(r ** 2 for r in downside) / len(downside) if downside else 0.0
    downside_dev = downside_var ** 0.5

    # Annualization factor derived from the trade log's own date span, rather than a fixed
    # 252 (equities-daily-bar) assumption, since these are event-driven options trades that
    # don't fire on a fixed daily schedule.
    if len(trades) >= 2:
        elapsed_days = max((trades[-1].closed_at - trades[0].opened_at).total_seconds() / 86400, 1.0)
        trades_per_year = len(trades) / elapsed_days * 365
    else:
        trades_per_year = len(trades) or 1
    sharpe = (mean_ret / std_dev) * (trades_per_year ** 0.5) if std_dev else 0.0
    sortino = (mean_ret / downside_dev) * (trades_per_year ** 0.5) if downside_dev else 0.0
    volatility_pct = std_dev * (trades_per_year ** 0.5) * 100

    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        dd = (e - peak) / peak * 100 if peak else 0.0
        max_dd = min(max_dd, dd)

    return BacktestResult(
        starting_capital=starting_capital,
        net_profit=round(net_profit, 2),
        net_profit_pct=round(net_profit_pct, 2),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(profit_factor, 2),
        sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2),
        max_drawdown_pct=round(max_dd, 2),
        volatility_pct=round(volatility_pct, 1),
        total_trades=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        equity_curve=[round(e, 2) for e in equity],
        trades=trades,
        weekly=_compute_weekly_stats(starting_capital, trades),
    )


def run_backtest(
    candles: list[Candle],
    strategy: str = "ai-bias-ce-writing-below-vah",
    starting_capital: float = 100_000.0,
    position_size_lots: int = 3,
    stop_loss_pct: float = 1.5,
    target_pct: float = 3.0,
    include_slippage_and_costs: bool = True,
    is_futures: bool = False,
    hold_mode: str = "strategy",
    custom_hold_days: int = 5,
    symbol: str = "NIFTY50",
) -> BacktestResult:
    """`candles` must be real historical daily OHLC for the requested
    underlying+date range (see backend/app/services/backtest_service.py),
    sorted ascending, with a couple of extra leading days so the first
    in-range day still has real prior-day OHLC for its CPR calculation.

    `hold_mode` overrides how long positions are carried so the same signal
    can be compared across horizons: "strategy" (each strategy's own choice),
    "intraday" (same-session exit), "weekly" (hold to weekly expiry) or
    "custom" (hold `custom_hold_days` days).

    `symbol` selects the contract spec — lot size, strike step and expiry
    calendar — and must match the index `candles` actually came from. It is
    not cosmetic: expiry day differs per index (NIFTY Tuesday, SENSEX
    Thursday, BANKNIFTY monthly-only), and expiry date drives time-to-expiry,
    which drives every modeled premium and Greek in the run."""
    if is_futures:
        trades = _simulate_futures_trades(
            candles, strategy, position_size_lots, include_slippage_and_costs, starting_capital,
            hold_mode, custom_hold_days, symbol,
        )
    else:
        trades = _simulate_trades(
            candles, strategy, position_size_lots, stop_loss_pct, target_pct,
            include_slippage_and_costs, starting_capital, hold_mode, custom_hold_days, symbol,
        )
    return _compute_metrics(starting_capital, trades)
