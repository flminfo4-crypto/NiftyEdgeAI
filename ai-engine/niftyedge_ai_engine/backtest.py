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

LOT_SIZE = 75  # NIFTY standard lot (see niftyedge_ai_engine equivalents elsewhere)
STRIKE_STEP = 50
# Rough all-in cost (brokerage + STT + exchange + GST + stamp duty) as a
# fraction of premium turnover — same order of magnitude as
# order_service._estimate_charges on the backend, kept local since ai-engine
# has no backend dependency.
COST_FRACTION = 0.0015
PERCENTILE_TRAILING_SESSIONS = 20


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
    stt = turnover * 0.0005 if side == "SELL" else 0.0
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


def _round_strike(spot: float) -> float:
    return round(spot / STRIKE_STEP) * STRIKE_STEP


def _next_thursday(d: date) -> date:
    offset = (3 - d.weekday()) % 7  # Mon=0 .. Thu=3 .. Sun=6
    return d + timedelta(days=offset or 7)


@dataclass
class StrategyContext:
    """Everything a strategy's signal function might need beyond today's
    levels: the full candle series and today's index into it, so strategies
    can look at trailing history (e.g. for percentile width classification)
    without ai-engine reaching out to a broker — the series is already
    real historical data the caller fetched."""
    candles: list["Candle"]
    index: int


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

    hold controls the exit style on the daily-bar series:
    - SWING: walk day-by-day until stop/target/expiry (_leg_pnl).
    - INTRADAY: enter on the real open, exit on the real close of the same
      candle, with one day of time value decayed (stop/target don't apply —
      there is no multi-day path to walk on a daily bar).
    - EXPIRY_DAY: same-session, but the exit is the option's real intrinsic
      value at the close — i.e. actual expiry settlement, which is exact
      rather than modeled.
    """
    kind: Literal["SINGLE", "CONDOR", "SPREAD"]
    option_type: Optional[Literal["CE", "PE"]] = None
    side: Optional[Literal["BUY", "SELL"]] = None
    # SINGLE: offset of the one leg. SPREAD: offset of the short (sold) leg —
    # the long protective leg sits `wing_offset` further out (see below).
    strike_offset: int = 0  # points OTM from ATM (0 = ATM); direction of "OTM" follows option_type
    direction: int = 0      # +1 profits from underlying rising, -1 profits from underlying falling
    hold: Literal["SWING", "INTRADAY", "EXPIRY_DAY"] = "SWING"
    # CONDOR: each wing's OTM distance from ATM (both legs are naked).
    # SPREAD: extra OTM distance from the short leg to the protective long leg.
    wing_offset: int = 200
    # Multiplier on the run's target_pct [Ochoa 2010, Ch. 6, ~p.196]: a narrow
    # CPR follows a quiet session and lets price run past the second pivot
    # layer, so a fixed target leaves money behind; a wide CPR rarely clears
    # that layer, so the same target is unreachable. Strategies that read the
    # width set this; everything else leaves it at 1.0 and is unaffected.
    target_scale: float = 1.0


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
    if today.weekday() != 3:  # the engine models weekly index expiry as Thursday throughout
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
    expiry = _next_thursday(today)
    t = max((expiry - today).days / 365.0, 1.0 / 365.0)
    atm = _round_strike(entry_spot)
    best_off, best_err = 0, float("inf")
    for off in range(-200, 201, STRIKE_STEP):
        d = abs(black_scholes(entry_spot, atm + off, t, sigma, opt).delta)
        err = abs(d - 0.60)
        if err < best_err:
            best_err, best_off = err, off
    # _strike_for applies +offset for CE and -offset for PE; best_off is in
    # absolute points from ATM, so flip the sign for puts to land on atm+off.
    shape_off = best_off if opt == "CE" else -best_off
    return LegShape(kind="SINGLE", option_type=opt, side="BUY", strike_offset=shape_off, direction=1 if up else -1)


@register_strategy("ma-greeks-credit-spread", "SELL CREDIT SPREAD (MA Trend + Delta, Defined Risk)")
def _sig_ma_greeks_credit_spread(entry_spot: float, levels: DailyLevels, ctx: StrategyContext) -> Optional[LegShape]:
    """Defined-risk income strategy: when the 20-SMA/50-SMA trend agrees with
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

    Two tuning notes from real iteration against 6 years of Dhan data (see
    the strategy lab sweep), kept here so the next change starts from
    evidence, not a guess:
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
    i = ctx.index
    closes = [c.close for c in ctx.candles[: i + 1]]
    s20, s50 = _sma(closes, 20), _sma(closes, 50)
    if s20 is None or s50 is None:
        return None
    if _percentile_width_regime(ctx) == "NARROW":
        return None
    up = s20 > s50 and entry_spot > s20
    down = s20 < s50 and entry_spot < s20
    if not (up or down):
        return None

    opt: Literal["CE", "PE"] = "PE" if up else "CE"  # sell the side the trend defends
    sigma = realized_volatility(closes)
    today = ctx.candles[i].dt
    expiry = _next_thursday(today)
    t = max((expiry - today).days / 365.0, 1.0 / 365.0)
    atm = _round_strike(entry_spot)
    best_off, best_err = 50, float("inf")
    for off in range(50, 301, STRIKE_STEP):
        strike = atm + off if opt == "CE" else atm - off
        err = abs(abs(black_scholes(entry_spot, strike, t, sigma, opt).delta) - 0.25)
        if err < best_err:
            best_err, best_off = err, off

    return LegShape(
        kind="SPREAD", option_type=opt, side="SELL", strike_offset=best_off,
        direction=1 if up else -1, wing_offset=100, target_scale=10.0,
    )


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
    if today.weekday() != 3:
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


def _strike_for(entry_spot: float, option_type: Literal["CE", "PE"], offset: int) -> float:
    atm = _round_strike(entry_spot)
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
        decay_pct = (entry_net - exit_net) / entry_net * 100
        if spot_move_pct <= -stop_loss_pct or decay_pct >= target_pct or c.dt >= expiry:
            break
    return _SpreadFill(entry_net, exit_net, closed, short_entry, long_entry, short_exit, long_exit)


def _same_day_pnl(
    entry_spot: float, strike: float, option_type: Literal["CE", "PE"],
    hold: str, today: Candle, sigma: float,
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
        expiry = _next_thursday(today.dt)
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
    if hold_mode == "strategy" or shape.kind in ("CONDOR", "SPREAD"):
        return shape
    if hold_mode == "intraday":
        return replace(shape, hold="INTRADAY")
    return replace(shape, hold="SWING")  # weekly / custom both walk the path


def _effective_expiry(entry_dt: date, hold_mode: str, custom_hold_days: int) -> date:
    """The date a position is force-closed on. Weekly uses the real weekly
    expiry (Thursday); custom holds a fixed number of calendar days."""
    if hold_mode == "custom":
        return entry_dt + timedelta(days=max(1, custom_hold_days))
    return _next_thursday(entry_dt)


def _simulate_trades(
    candles: list[Candle], strategy: str, position_size_lots: int,
    stop_loss_pct: float, target_pct: float, include_costs: bool, starting_capital: float,
    hold_mode: str = "strategy", custom_hold_days: int = 5,
) -> list[Trade]:
    strategy_def = STRATEGY_REGISTRY[strategy]
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
        shape = strategy_def.signal_fn(entry_spot, levels, StrategyContext(candles=candles, index=i))
        if not shape:
            continue
        shape = _apply_hold_mode(shape, hold_mode)

        expiry = _effective_expiry(today.dt, hold_mode, custom_hold_days)
        path = [c for c in candles[i:] if c.dt <= expiry + timedelta(days=1)]
        sigma = realized_volatility(closes[: i + 1])
        # Width-aware target: strategies that read the CPR width scale how far
        # they reach (see LegShape.target_scale). Default 1.0 = unchanged.
        eff_target_pct = target_pct * shape.target_scale

        costs = 0.0
        if shape.kind == "CONDOR":
            ce_strike = _round_strike(entry_spot) + shape.wing_offset
            pe_strike = _round_strike(entry_spot) - shape.wing_offset
            entry_p, exit_p, closed_date = _condor_pnl(
                entry_spot, ce_strike, pe_strike, today.dt, expiry, path, sigma, stop_loss_pct, eff_target_pct
            )
            pnl = (entry_p - exit_p) * LOT_SIZE * position_size_lots
            if include_costs:
                costs = (entry_p + exit_p) * LOT_SIZE * position_size_lots * COST_FRACTION
        elif shape.kind == "SPREAD":
            short_strike = _strike_for(entry_spot, shape.option_type, shape.strike_offset)
            long_strike = _strike_for(entry_spot, shape.option_type, shape.strike_offset + shape.wing_offset)
            fill = _spread_pnl(
                entry_spot, short_strike, long_strike, shape.option_type, today.dt, expiry, path, sigma,
                stop_loss_pct, eff_target_pct, direction=shape.direction,
            )
            entry_p, exit_p, closed_date = fill.entry_net, fill.exit_net, fill.closed
            pnl = (entry_p - exit_p) * LOT_SIZE * position_size_lots  # SELL-the-spread: net credit convention
            if include_costs:
                # Itemized real per-order charges (see _estimate_leg_charges) for
                # each of the 4 real orders a spread trade actually places —
                # exact, not the blended COST_FRACTION the other kinds use.
                qty = LOT_SIZE * position_size_lots
                costs = (
                    _estimate_leg_charges("SELL", fill.short_entry * qty)   # open: sell the short leg
                    + _estimate_leg_charges("BUY", fill.long_entry * qty)   # open: buy the protective leg
                    + _estimate_leg_charges("BUY", fill.short_exit * qty)   # close: buy back the short leg
                    + _estimate_leg_charges("SELL", fill.long_exit * qty)   # close: sell the protective leg
                )
        else:
            strike = _strike_for(entry_spot, shape.option_type, shape.strike_offset)
            if shape.hold == "SWING":
                entry_p, exit_p, closed_date = _leg_pnl(
                    entry_spot, strike, shape.option_type, shape.side, today.dt, expiry, path, sigma,
                    stop_loss_pct, eff_target_pct, direction=shape.direction,
                )
            else:
                entry_p, exit_p, closed_date = _same_day_pnl(
                    entry_spot, strike, shape.option_type, shape.hold, today, sigma,
                )
            pnl = (entry_p - exit_p) if shape.side == "SELL" else (exit_p - entry_p)
            pnl *= LOT_SIZE * position_size_lots
            if include_costs:
                costs = (entry_p + exit_p) * LOT_SIZE * position_size_lots * COST_FRACTION

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
) -> list[Trade]:
    """Futures need no premium model — P&L is the real spot move itself."""
    strategy_def = STRATEGY_REGISTRY[strategy]
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
        shape = strategy_def.signal_fn(entry_spot, levels, StrategyContext(candles=candles, index=i))
        if not shape or shape.kind == "CONDOR":
            continue  # a condor has no directional futures equivalent
        shape = _apply_hold_mode(shape, hold_mode)
        direction = shape.direction  # bearish setups short the future, bullish setups go long

        expiry = _effective_expiry(today.dt, hold_mode, custom_hold_days)
        exit_candle = today  # same-day holds exit on this candle's own close
        if shape.hold == "SWING":
            for c in candles[i:]:
                if c.dt <= today.dt:
                    continue
                exit_candle = c
                if c.dt >= expiry:
                    break
        pnl = direction * (exit_candle.close - entry_spot) * LOT_SIZE * position_size_lots
        if include_costs:
            pnl -= abs(entry_spot + exit_candle.close) * LOT_SIZE * position_size_lots * COST_FRACTION
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
) -> BacktestResult:
    """`candles` must be real historical daily OHLC for the requested
    underlying+date range (see backend/app/services/backtest_service.py),
    sorted ascending, with a couple of extra leading days so the first
    in-range day still has real prior-day OHLC for its CPR calculation.

    `hold_mode` overrides how long positions are carried so the same signal
    can be compared across horizons: "strategy" (each strategy's own choice),
    "intraday" (same-session exit), "weekly" (hold to weekly expiry) or
    "custom" (hold `custom_hold_days` days)."""
    if is_futures:
        trades = _simulate_futures_trades(
            candles, strategy, position_size_lots, include_slippage_and_costs, starting_capital,
            hold_mode, custom_hold_days,
        )
    else:
        trades = _simulate_trades(
            candles, strategy, position_size_lots, stop_loss_pct, target_pct,
            include_slippage_and_costs, starting_capital, hold_mode, custom_hold_days,
        )
    return _compute_metrics(starting_capital, trades)
