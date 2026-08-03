"""
Virgin CPR intraday trading — detection and bar-by-bar backtest.

A pivot level is "virgin" when price never traded into it during the session
that created it [Ochoa 2010, Ch. 4, ~pp.115-121]. The book applies the idea
to Money Zone / POC levels and reports that a large majority get "ruled"
(tested) within about a week of creation; this module applies the same
definition to the Central Pivot Range, and — importantly — MEASURES that
fill rate on real data instead of assuming it.

Trading rules taken from the book, not invented here:
- Fade a virgin level in the direction of the existing short-term trend
  (which the book says can be as short as two days).
- Prefer a MORNING test, roughly the first 30-60 minutes, because it lines
  up with the session's initial balance forming.
- If price only reaches the level LATE in the session, use it as a target
  rather than fading the reaction.

P&L here is expressed in index points and as a percentage of the entry
price, deliberately: this isolates whether the virgin-level signal itself
has an edge, without folding in the Black-Scholes premium modelling that
the option backtester has to assume (Dhan keeps no expired-contract
prices). Converting a points edge into an option P&L can come later; a
signal that does not work in points will not work in options either.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal, Optional

from .pivots import cpr as calc_cpr

# The book's "first thirty to sixty minutes" window for a fadeable test.
MORNING_WINDOW_MIN = 60
# A level is abandoned if it has not been filled after this many sessions —
# the book notes a level's relevance decays the longer price stays away.
MAX_ZONE_AGE_DAYS = 20


@dataclass
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class DailyBar:
    dt: date
    open: float
    high: float
    low: float
    close: float


@dataclass
class VirginZone:
    """A CPR that its own session never traded into."""
    created_for: date          # the session the CPR was calculated FOR
    bc: float
    tc: float
    filled_on: Optional[date] = None
    fill_age_days: Optional[int] = None   # trading sessions from creation to fill

    def contains(self, low: float, high: float) -> bool:
        return low <= self.tc and high >= self.bc


@dataclass
class IntradayTrade:
    entered_at: datetime
    exited_at: datetime
    side: Literal["LONG", "SHORT"]
    entry: float
    exit: float
    points: float
    return_pct: float
    reason: str
    zone_created_for: date


@dataclass
class VirginBacktestResult:
    total_sessions: int
    zones_created: int
    zones_filled: int
    fill_rate_pct: float
    fill_within_5_sessions_pct: float
    median_fill_age_days: Optional[float]
    trades: list = field(default_factory=list)
    total_points: float = 0.0
    win_rate_pct: float = 0.0
    avg_win_points: float = 0.0
    avg_loss_points: float = 0.0
    profit_factor: Optional[float] = None
    max_drawdown_points: float = 0.0


def find_virgin_zones(days: list) -> list:
    """Walk real sessions and mark every CPR its own session never touched.

    `days` is oldest-first DailyBar. Day i's CPR comes from day i-1's OHLC
    (the standard construction), so a zone is virgin when day i's own
    high/low never enter it. Each zone is then tracked forward until a later
    session trades into it.
    """
    zones: list = []
    for i in range(1, len(days)):
        prior, today = days[i - 1], days[i]
        c = calc_cpr(prior.high, prior.low, prior.close)
        if not (today.low <= c.tc and today.high >= c.bc):
            zones.append(VirginZone(created_for=today.dt, bc=c.bc, tc=c.tc))

    # forward-fill: which later session first trades into each zone
    for z in zones:
        age = 0
        for d in days:
            if d.dt <= z.created_for:
                continue
            age += 1
            if z.contains(d.low, d.high):
                z.filled_on = d.dt
                z.fill_age_days = age
                break
    return zones


def _short_term_trend(days: list, upto: date) -> Optional[str]:
    """Two-session close-to-close direction, the book's shortest usable
    trend. Returns None when there is not enough history."""
    prior = [d for d in days if d.dt < upto]
    if len(prior) < 2:
        return None
    if prior[-1].close > prior[-2].close:
        return "UP"
    if prior[-1].close < prior[-2].close:
        return "DOWN"
    return None


def _session_open_time(bars: list) -> datetime:
    return bars[0].ts


def run_virgin_intraday_backtest(
    days: list,
    bars_by_day: dict,
    stop_buffer_pct: float = 0.25,
    reward_multiple: float = 2.0,
    morning_window_min: int = MORNING_WINDOW_MIN,
    max_zone_age: int = MAX_ZONE_AGE_DAYS,
) -> VirginBacktestResult:
    """Simulate the virgin-CPR fade on real intraday bars.

    days        oldest-first DailyBar (for zone creation and trend)
    bars_by_day {date: [Bar, ...]} real intraday bars per session

    One position per session. Entry: price tests an unfilled virgin zone
    inside the morning window AND the two-day trend points the way the fade
    would go. Exit: stop beyond the zone, target at `reward_multiple` x risk,
    otherwise the session close.
    """
    zones = find_virgin_zones(days)
    open_zones: list = []
    trades: list = []
    zone_idx = 0
    zones_sorted = sorted(zones, key=lambda z: z.created_for)

    session_dates = [d.dt for d in days if d.dt in bars_by_day]

    for sess in session_dates:
        # Admit a zone only once it is actually tradeable — the session AFTER
        # the one it was created for. Advancing the cursor on `<= sess` while
        # the filter below requires `< sess` would consume each zone on its
        # creation session and drop it the same instant, and the cursor never
        # rewinds, so no zone would ever be tradeable.
        while zone_idx < len(zones_sorted) and zones_sorted[zone_idx].created_for < sess:
            open_zones.append(zones_sorted[zone_idx])
            zone_idx += 1
        open_zones = [
            z for z in open_zones
            if (z.filled_on is None or z.filled_on >= sess)
            and (sess - z.created_for).days <= max_zone_age * 2
        ]
        if not open_zones:
            continue

        bars = bars_by_day.get(sess) or []
        if len(bars) < 4:
            continue
        trend = _short_term_trend(days, sess)
        if trend is None:
            continue

        open_ts = _session_open_time(bars)
        cutoff = open_ts + timedelta(minutes=morning_window_min)
        position = None

        for bar in bars:
            if position is None:
                if bar.ts > cutoff:
                    break  # past the morning window; the book says target, not fade
                # find a zone this bar actually tested
                hit = next((z for z in open_zones if z.contains(bar.low, bar.high)), None)
                if hit is None:
                    continue
                mid = (hit.bc + hit.tc) / 2
                # Fade only with the short-term trend: a rally into a virgin
                # zone during a downtrend is a short, a drop into one during
                # an uptrend is a long.
                if trend == "DOWN" and bar.high >= hit.bc:
                    side = "SHORT"
                    entry = min(bar.close, hit.tc)
                    stop = hit.tc * (1 + stop_buffer_pct / 100)
                    risk = abs(stop - entry)
                    target = entry - risk * reward_multiple
                elif trend == "UP" and bar.low <= hit.tc:
                    side = "LONG"
                    entry = max(bar.close, hit.bc)
                    stop = hit.bc * (1 - stop_buffer_pct / 100)
                    risk = abs(entry - stop)
                    target = entry + risk * reward_multiple
                else:
                    continue
                if risk <= 0:
                    continue
                position = {
                    "side": side, "entry": entry, "stop": stop, "target": target,
                    "ts": bar.ts, "zone": hit,
                }
                continue

            # manage the open position on subsequent bars
            side = position["side"]
            if side == "SHORT":
                if bar.high >= position["stop"]:
                    exit_px, reason = position["stop"], "stop"
                elif bar.low <= position["target"]:
                    exit_px, reason = position["target"], "target"
                else:
                    continue
            else:
                if bar.low <= position["stop"]:
                    exit_px, reason = position["stop"], "stop"
                elif bar.high >= position["target"]:
                    exit_px, reason = position["target"], "target"
                else:
                    continue
            pts = (position["entry"] - exit_px) if side == "SHORT" else (exit_px - position["entry"])
            trades.append(IntradayTrade(
                entered_at=position["ts"], exited_at=bar.ts, side=side,
                entry=round(position["entry"], 2), exit=round(exit_px, 2),
                points=round(pts, 2), return_pct=round(pts / position["entry"] * 100, 3),
                reason=reason, zone_created_for=position["zone"].created_for,
            ))
            position = None
            break  # one trade per session

        # still open at the close -> exit at the session close
        if position is not None:
            last = bars[-1]
            side = position["side"]
            pts = (position["entry"] - last.close) if side == "SHORT" else (last.close - position["entry"])
            trades.append(IntradayTrade(
                entered_at=position["ts"], exited_at=last.ts, side=side,
                entry=round(position["entry"], 2), exit=round(last.close, 2),
                points=round(pts, 2), return_pct=round(pts / position["entry"] * 100, 3),
                reason="session-close", zone_created_for=position["zone"].created_for,
            ))

    filled = [z for z in zones if z.filled_on is not None]
    ages = sorted(z.fill_age_days for z in filled if z.fill_age_days is not None)
    median_age = None
    if ages:
        m = len(ages) // 2
        median_age = float(ages[m]) if len(ages) % 2 else (ages[m - 1] + ages[m]) / 2

    wins = [t.points for t in trades if t.points > 0]
    losses = [t.points for t in trades if t.points <= 0]
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t.points
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    return VirginBacktestResult(
        total_sessions=len(session_dates),
        zones_created=len(zones),
        zones_filled=len(filled),
        fill_rate_pct=round(len(filled) / len(zones) * 100, 1) if zones else 0.0,
        fill_within_5_sessions_pct=round(
            sum(1 for a in ages if a <= 5) / len(zones) * 100, 1) if zones else 0.0,
        median_fill_age_days=median_age,
        trades=trades,
        total_points=round(sum(t.points for t in trades), 2),
        win_rate_pct=round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        avg_win_points=round(sum(wins) / len(wins), 2) if wins else 0.0,
        avg_loss_points=round(sum(losses) / len(losses), 2) if losses else 0.0,
        profit_factor=round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        max_drawdown_points=round(max_dd, 2),
    )
