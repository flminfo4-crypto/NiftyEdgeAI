"""
Strategy signal generation — powers strategy-signals.html: a primary signal
(highest confidence, derived straight from compute_bias()), zero or more
alternative signals, and a signal history log with realized outcomes.

Every signal carries the same "Why?" reasoning list the bias engine produced,
so the UI can show *why* a signal fired, not just that it fired.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from .bias import BiasFactor, BiasResult, compute_bias

SignalAction = Literal[
    "SELL CALL (CE WRITING)", "BUY PUT (PE BUYING)", "SELL PUT (PE WRITING)", "BUY CALL (CE BUYING)", "IRON CONDOR"
]


@dataclass
class Signal:
    action: str
    instrument: str
    entry_zone: str
    target: str
    stop_loss: str
    confidence_pct: int
    generated_at: datetime
    reasoning: list[str] = field(default_factory=list)


@dataclass
class SignalHistoryRow:
    when: str
    signal: str
    confidence_pct: int
    entry: str
    target: str
    stop_loss: str
    result: Literal["Target Hit", "SL Hit", "Expired ITM Range", "Open"]
    pnl: float


def _reasoning_from_factors(factors: list[BiasFactor]) -> list[str]:
    text = {
        "price_vs_value": "Spot is {value} — {dir} pressure from Market Profile.",
        "order_flow": "Cumulative delta shows {value} on the Footprint chart.",
        "oi_trend": "{value} dominates Open Interest buildup.",
        "cpr_two_day": "Two-day CPR relationship: {value} [Pivot Boss Ch. 6].",
        "delta": "Portfolio delta is {value} ({dir}).",
        "gamma": "Portfolio gamma is {value} ({dir}).",
    }
    out = []
    for f in factors:
        template = text.get(f.key, "{value}")
        out.append(template.format(value=f.value, dir=f.direction.lower()))
    return out


def generate_primary_signal(bias: Optional[BiasResult] = None, strike: Optional[float] = None) -> Signal:
    bias = bias or compute_bias()
    now = datetime.now(timezone.utc)

    if bias.direction == "BEARISH":
        strike = strike or 23600
        action = "SELL CALL (CE WRITING)"
        instrument = f"{strike:,.0f} CE"
        entry_zone = f"{strike - 20:,.0f}–{strike + 20:,.0f}"
        target = f"{strike - 300:,.0f}"
        stop_loss = f"{strike + 110:,.0f}"
    elif bias.direction == "BULLISH":
        strike = strike or 23400
        action = "BUY CALL (CE BUYING)"
        instrument = f"{strike:,.0f} CE"
        entry_zone = f"Above {strike:,.0f}"
        target = f"{strike + 250:,.0f}"
        stop_loss = f"{strike - 90:,.0f}"
    else:
        strike = strike or 23500
        action = "IRON CONDOR"
        instrument = f"{strike - 200:,.0f}/{strike + 200:,.0f}"
        entry_zone = "—"
        target = "—"
        stop_loss = "—"

    return Signal(
        action=action,
        instrument=instrument,
        entry_zone=entry_zone,
        target=target,
        stop_loss=stop_loss,
        confidence_pct=bias.confidence_pct,
        generated_at=now,
        reasoning=_reasoning_from_factors(bias.factors),
    )


def generate_alternative_signal(bias: Optional[BiasResult] = None) -> Signal:
    """A lower-conviction counter-trend hedge idea, shown alongside the primary signal."""
    bias = bias or compute_bias()
    now = datetime.now(timezone.utc)
    if bias.direction == "BEARISH":
        strike = 23400
        return Signal(
            action="BUY PUT (PE BUYING)",
            instrument=f"{strike:,.0f} PE",
            entry_zone=f"Below {strike:,.0f}",
            target=f"{strike - 150:,.0f}",
            stop_loss=f"{strike + 60:,.0f}",
            confidence_pct=max(50, bias.confidence_pct - 10),
            generated_at=now,
            reasoning=["Hedge idea: momentum continuation below value area low."],
        )
    strike = 23600
    return Signal(
        action="SELL CALL (CE WRITING)",
        instrument=f"{strike:,.0f} CE",
        entry_zone=f"{strike - 20:,.0f}–{strike + 20:,.0f}",
        target=f"{strike - 300:,.0f}",
        stop_loss=f"{strike + 110:,.0f}",
        confidence_pct=max(50, bias.confidence_pct - 10),
        generated_at=now,
        reasoning=["Hedge idea: fade extension into resistance."],
    )


def generate_signals(bias: Optional[BiasResult] = None) -> dict:
    bias = bias or compute_bias()
    return {
        "bias": bias,
        "primary": generate_primary_signal(bias),
        "alternative": generate_alternative_signal(bias),
        "history": get_signal_history(),
    }


def get_signal_history() -> list[SignalHistoryRow]:
    """Static mock history matching strategy-signals.html's log table, until real
    signal outcomes accumulate in the datastore."""
    return [
        SignalHistoryRow("09:20 AM", "SELL CALL 23550 CE", 78, "23,540", "23,420", "23,610", "Target Hit", 3120),
        SignalHistoryRow("Yesterday", "BUY PUT 23600 PE", 71, "23,610", "23,480", "23,660", "Target Hit", 2640),
        SignalHistoryRow("Yesterday", "SELL PUT 23300 PE", 65, "23,320", "23,410", "23,260", "SL Hit", -1480),
        SignalHistoryRow("2 days ago", "IRON CONDOR 23300/23700", 69, "—", "—", "—", "Expired ITM Range", 4210),
        SignalHistoryRow("3 days ago", "SELL CALL 23650 CE", 74, "23,660", "23,520", "23,730", "Target Hit", 2910),
        SignalHistoryRow("4 days ago", "BUY CALL 23450 CE", 58, "23,460", "23,590", "23,390", "SL Hit", -2050),
    ]
