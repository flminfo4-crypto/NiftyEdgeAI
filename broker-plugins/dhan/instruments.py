"""
Maps NiftyEdgeAI's internal instrument strings (the same ones broker-plugins/mock
uses, e.g. "NIFTY50", "NIFTY24JUL23600CE") to the (securityId, exchangeSegment)
pairs Dhan's v2 API requires instead of trading symbols.

The five index securityIds below were looked up from Dhan's public scrip
master (https://images.dhan.co/api-data/api-scrip-master.csv) on 2026-07-29
and pinned here since Dhan documents "IDX_I" as the segment for indices but
doesn't publish the numeric ids as stable named constants. Option contracts
are resolved by searching that same CSV at runtime (cached in-process for
the life of the adapter).
"""

import csv
import io
import re
from dataclasses import dataclass

import httpx

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

IDX_I = "IDX_I"

# internal symbol -> (securityId, exchangeSegment)
INDEX_SECURITY_IDS: dict[str, tuple[str, str]] = {
    "NIFTY50": ("13", IDX_I),
    "NIFTYBANK": ("25", IDX_I),
    "FINNIFTY": ("27", IDX_I),
    "SENSEX": ("51", IDX_I),
    "INDIAVIX": ("21", IDX_I),
}

# Dhan's own underlying root differs from our internal symbol for a couple of these
_DHAN_UNDERLYING_ROOT = {
    "NIFTY50": "NIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "SENSEX": "SENSEX",
}

_MONTHS = {
    "JAN": "Jan", "FEB": "Feb", "MAR": "Mar", "APR": "Apr", "MAY": "May", "JUN": "Jun",
    "JUL": "Jul", "AUG": "Aug", "SEP": "Sep", "OCT": "Oct", "NOV": "Nov", "DEC": "Dec",
}

# e.g. "NIFTY24JUL23600CE" -> root=NIFTY, yy=24, mon=JUL, strike=23600, opt_type=CE
_OPTION_RE = re.compile(r"^([A-Z]+)(\d{2})([A-Z]{3})(\d+)(CE|PE)$")

@dataclass
class _Contract:
    security_id: str
    exchange_segment: str
    expiry_flag: str  # "M" (monthly) or "W" (weekly)
    expiry_date: str  # "YYYY-MM-DD HH:MM:SS"


_scrip_cache: dict[str, list[_Contract]] | None = None


def _load_scrip_master() -> dict[str, list[_Contract]]:
    global _scrip_cache
    if _scrip_cache is not None:
        return _scrip_cache
    resp = httpx.get(SCRIP_MASTER_URL, timeout=30.0)
    resp.raise_for_status()
    cache: dict[str, list[_Contract]] = {}
    for row in csv.DictReader(io.StringIO(resp.text)):
        if row.get("SEM_SEGMENT") != "D":  # derivatives only — this module never resolves equities
            continue
        cache.setdefault(row["SEM_TRADING_SYMBOL"], []).append(
            _Contract(
                security_id=row["SEM_SMST_SECURITY_ID"],
                exchange_segment=f"{row['SEM_EXM_EXCH_ID']}_FNO",
                expiry_flag=row["SEM_EXPIRY_FLAG"],
                expiry_date=row["SEM_EXPIRY_DATE"],
            )
        )
    _scrip_cache = cache
    return cache


def resolve(instrument: str) -> tuple[str, str]:
    """Returns (securityId, exchangeSegment) for an internal instrument string.

    Raises KeyError if the instrument can't be resolved (unknown underlying,
    unrecognized format, or no matching live contract in Dhan's scrip master).

    NOTE: our internal instrument strings (e.g. "NIFTY24JUL23600CE") only carry
    a month, not a specific expiry date, but Dhan's SEM_TRADING_SYMBOL collides
    across every weekly expiry in that month for indices with weekly options
    (NIFTY, BANKNIFTY) — same trading symbol, different securityId. When a
    candidate has more than one live contract, this picks the monthly
    (SEM_EXPIRY_FLAG == "M") one if there is one, else the earliest-dated
    weekly. Callers that need a *specific* weekly expiry cannot be represented
    by this instrument format and will silently resolve to the wrong contract —
    this is a real limitation, not just an edge case, until the app's
    instrument strings carry a full expiry date.
    """
    key = instrument.upper().replace(" ", "")
    if key in INDEX_SECURITY_IDS:
        return INDEX_SECURITY_IDS[key]

    match = _OPTION_RE.match(key)
    if not match:
        raise KeyError(f"Don't know how to resolve Dhan instrument for {instrument!r}")
    root, yy, mon, strike, opt_type = match.groups()
    dhan_root = _DHAN_UNDERLYING_ROOT.get(root, root)
    mon_title = _MONTHS.get(mon)
    if not mon_title:
        raise KeyError(f"Unrecognized month code {mon!r} in instrument {instrument!r}")

    candidate = f"{dhan_root}-{mon_title}20{yy}-{strike}-{opt_type}"
    contracts = _load_scrip_master().get(candidate)
    if not contracts:
        raise KeyError(f"No live Dhan contract found matching {instrument!r} (looked for {candidate!r})")
    monthly = [c for c in contracts if c.expiry_flag == "M"]
    chosen = monthly[0] if monthly else min(contracts, key=lambda c: c.expiry_date)
    return chosen.security_id, chosen.exchange_segment
