"""Instrument master: download Dhan's scrip list once per day, search it,
and resolve the security IDs of the four indices + India VIX.
"""
import datetime as dt
import logging

import httpx
import pandas as pd

from . import config

log = logging.getLogger("niftyedge.instruments")

_CACHE = config.DATA_DIR / "scrip-master.csv"
_df: pd.DataFrame | None = None

# The instruments the terminal is built around.
INDEX_QUERIES = {
    "NIFTY":      {"exch": "NSE", "name": "NIFTY"},
    "BANKNIFTY":  {"exch": "NSE", "name": "BANKNIFTY"},
    "FINNIFTY":   {"exch": "NSE", "name": "FINNIFTY"},
    "SENSEX":     {"exch": "BSE", "name": "SENSEX"},
    "INDIA VIX":  {"exch": "NSE", "name": "INDIA VIX"},
}


def _fresh_today() -> bool:
    if not _CACHE.exists():
        return False
    mtime = dt.date.fromtimestamp(_CACHE.stat().st_mtime)
    return mtime == dt.date.today()


def load() -> pd.DataFrame:
    """Download (if stale) and load the scrip master into memory."""
    global _df
    if _df is not None:
        return _df
    if not _fresh_today():
        log.info("Downloading instrument master ...")
        with httpx.Client(timeout=60) as http:
            r = http.get(config.SCRIP_MASTER_URL)
            r.raise_for_status()
            _CACHE.write_bytes(r.content)
        log.info("Instrument master saved (%d KB)", len(r.content) // 1024)
    _df = pd.read_csv(_CACHE, low_memory=False)
    # Normalise column names across master versions.
    _df.columns = [c.strip() for c in _df.columns]
    return _df


def _col(df: pd.DataFrame, *candidates: str) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    raise KeyError(f"None of {candidates} in scrip master columns: {list(df.columns)[:12]}")


def search(query: str, limit: int = 20) -> list[dict]:
    df = load()
    name_col = _col(df, "SEM_TRADING_SYMBOL", "SM_SYMBOL_NAME", "TRADING_SYMBOL")
    id_col = _col(df, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
    exch_col = _col(df, "SEM_EXM_EXCH_ID", "EXCH_ID")
    seg_col = _col(df, "SEM_SEGMENT", "SEGMENT") if any(
        c in df.columns for c in ("SEM_SEGMENT", "SEGMENT")) else None

    mask = df[name_col].astype(str).str.contains(query, case=False, na=False)
    out = []
    for _, row in df[mask].head(limit).iterrows():
        out.append({
            "symbol": str(row[name_col]),
            "security_id": str(row[id_col]),
            "exchange": str(row[exch_col]),
            "segment": str(row[seg_col]) if seg_col else "",
        })
    return out


def core_indices() -> dict[str, dict]:
    """Resolve the 4 indices + VIX to security IDs from the master.

    AC (Story 0 #4): all five must resolve, else raise loudly at startup.
    """
    df = load()
    name_col = _col(df, "SEM_TRADING_SYMBOL", "SM_SYMBOL_NAME", "TRADING_SYMBOL")
    id_col = _col(df, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
    exch_col = _col(df, "SEM_EXM_EXCH_ID", "EXCH_ID")
    inst_col = None
    for c in ("SEM_INSTRUMENT_NAME", "INSTRUMENT", "SEM_EXCH_INSTRUMENT_TYPE"):
        if c in df.columns:
            inst_col = c
            break

    resolved: dict[str, dict] = {}
    for key, want in INDEX_QUERIES.items():
        sub = df[
            (df[exch_col].astype(str).str.upper() == want["exch"])
            & (df[name_col].astype(str).str.upper() == want["name"])
        ]
        if inst_col is not None and len(sub) > 1:
            idx_rows = sub[sub[inst_col].astype(str).str.contains("INDEX", case=False, na=False)]
            if len(idx_rows):
                sub = idx_rows
        if len(sub) == 0:
            log.error("Could not resolve %s in instrument master", key)
            continue
        row = sub.iloc[0]
        resolved[key] = {
            "symbol": key,
            "security_id": str(row[id_col]),
            "exchange": want["exch"],
        }
    missing = set(INDEX_QUERIES) - set(resolved)
    if missing:
        raise RuntimeError(f"Instrument master missing: {missing}")
    return resolved
