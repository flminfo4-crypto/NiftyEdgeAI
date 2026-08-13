"""
Exchange contract specifications for the index derivatives this engine trades.

These are FACTS ABOUT THE MARKET, not tunable parameters, and they changed
materially in 2025-26. They live in one place because the backtest engine had
them scattered as hardcoded constants (`LOT_SIZE = 75`, `_next_thursday`,
`weekday() != 3`) that silently went stale:

- Expiry days were restructured in Sept 2025 under SEBI's one-weekly-expiry-
  per-exchange rule. NSE moved NIFTY from Thursday to **Tuesday** (1 Sep 2025)
  and BSE moved SENSEX from Tuesday to **Thursday** (4 Sep 2025) — the two
  exchanges swapped. BANKNIFTY, FINNIFTY and MIDCPNIFTY lost their weeklies
  entirely and are **monthly-only**.
- Lot sizes were revised from the January 2026 series: NIFTY 75 -> **65**,
  BANKNIFTY 35 -> **30**. SENSEX stayed at 20 (it had already gone 10 -> 20
  earlier in 2025).

Why this matters more than it looks: expiry date feeds time-to-expiry, which
feeds every Black-Scholes premium, every solved IV and every Greek. Being two
days wrong on a seven-day contract is a large error exactly where gamma and
theta are steepest, and it silently flatters or punishes every strategy that
holds to expiry.

Known limitation, stated rather than hidden: exchange trading holidays are not
modelled. Both expiry weekdays (Tue/Thu) are always weekdays, so the only case
this misses is an expiry falling on a declared holiday, where the real contract
settles on the previous trading day. Callers with a real holiday calendar should
apply that shift themselves; `next_expiry` documents the assumption in place.
"""

from dataclasses import dataclass
from datetime import date, timedelta

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY = 0, 1, 2, 3, 4


@dataclass(frozen=True)
class ContractSpec:
    """One index's tradable contract shape.

    weekly_expiry_weekday is None for indices whose weekly contracts were
    discontinued — for those, "weekly" and "monthly" resolve to the same
    monthly expiry, because asking for a weekly BANKNIFTY contract is asking
    for something that does not exist.
    """
    symbol: str
    label: str
    lot_size: int
    strike_step: int
    weekly_expiry_weekday: int | None
    monthly_expiry_weekday: int

    @property
    def has_weekly(self) -> bool:
        return self.weekly_expiry_weekday is not None


SPECS: dict[str, ContractSpec] = {
    "NIFTY50": ContractSpec(
        symbol="NIFTY50", label="NIFTY 50", lot_size=65, strike_step=50,
        weekly_expiry_weekday=TUESDAY, monthly_expiry_weekday=TUESDAY,
    ),
    "NIFTYBANK": ContractSpec(
        symbol="NIFTYBANK", label="BANK NIFTY", lot_size=30, strike_step=100,
        weekly_expiry_weekday=None,  # weeklies discontinued — monthly only
        monthly_expiry_weekday=TUESDAY,
    ),
    "SENSEX": ContractSpec(
        symbol="SENSEX", label="SENSEX", lot_size=20, strike_step=100,
        weekly_expiry_weekday=THURSDAY, monthly_expiry_weekday=THURSDAY,
    ),
}

_DEFAULT = "NIFTY50"


def spec_for(symbol: str | None) -> ContractSpec:
    """Spec for a symbol, defaulting to NIFTY. Unknown symbols fall back
    rather than raising: the engine is called from several places that pass a
    loosely-typed instrument string, and a wrong-but-sane default beats a
    crash mid-backtest. Callers that care should check membership first."""
    if not symbol:
        return SPECS[_DEFAULT]
    return SPECS.get(symbol.upper().replace(" ", ""), SPECS[_DEFAULT])


def lot_size(symbol: str | None) -> int:
    return spec_for(symbol).lot_size


def strike_step(symbol: str | None) -> int:
    return spec_for(symbol).strike_step


def _next_weekday_on_or_after(d: date, weekday: int) -> date:
    return d + timedelta(days=(weekday - d.weekday()) % 7)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last <weekday> of the month — the monthly expiry convention."""
    if month == 12:
        first_of_next = date(year + 1, 1, 1)
    else:
        first_of_next = date(year, month + 1, 1)
    last_day = first_of_next - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - weekday) % 7)


def monthly_expiry(symbol: str | None, d: date) -> date:
    """The next monthly expiry strictly after `d`."""
    s = spec_for(symbol)
    candidate = _last_weekday_of_month(d.year, d.month, s.monthly_expiry_weekday)
    if candidate > d:
        return candidate
    nxt_year, nxt_month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return _last_weekday_of_month(nxt_year, nxt_month, s.monthly_expiry_weekday)


def next_expiry(symbol: str | None, d: date, kind: str = "weekly") -> date:
    """The next expiry strictly after `d` for this symbol.

    `kind` is "weekly" or "monthly". Requesting a weekly on an index whose
    weeklies were discontinued (BANKNIFTY) returns the MONTHLY expiry — the
    only contract that actually exists — rather than inventing a Tuesday that
    nothing settles on.

    Does not adjust for exchange trading holidays (see module docstring).
    """
    s = spec_for(symbol)
    if kind == "monthly" or not s.has_weekly:
        return monthly_expiry(symbol, d)
    nxt = _next_weekday_on_or_after(d, s.weekly_expiry_weekday)
    return nxt if nxt > d else nxt + timedelta(days=7)


def is_expiry_day(symbol: str | None, d: date, kind: str = "weekly") -> bool:
    """Whether `d` is itself an expiry for this symbol — what the 0DTE
    strategies need, and what the old `weekday() != 3` checks got wrong for
    every index once the Sept 2025 restructuring landed."""
    s = spec_for(symbol)
    if kind == "monthly" or not s.has_weekly:
        return d == _last_weekday_of_month(d.year, d.month, s.monthly_expiry_weekday)
    return d.weekday() == s.weekly_expiry_weekday


def days_to_expiry(symbol: str | None, d: date, kind: str = "weekly") -> int:
    return (next_expiry(symbol, d, kind) - d).days
