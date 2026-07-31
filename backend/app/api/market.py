from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import CandleOut, CprLevelsOut, OptionChainOut, QuoteOut
from app.services import market_data, pivot_service

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/quote", response_model=list[QuoteOut])
def get_quote(symbols: str = Query(..., description="Comma-separated symbols, e.g. NIFTY50,INDIAVIX")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    quotes = market_data.get_quotes(syms)
    if not quotes:
        raise HTTPException(status_code=404, detail="No matching symbols")
    return quotes


@router.get("/option-chain", response_model=OptionChainOut)
def get_option_chain(underlying: str = "NIFTY50", expiry: str = "2026-07-31"):
    return market_data.get_option_chain(underlying, expiry)


@router.get("/candles", response_model=list[CandleOut])
def get_candles(
    symbol: str = "NIFTY50",
    interval: str = "5m",
    frm: str | None = Query(default=None, alias="from"),
    to: str | None = None,
):
    to_dt = datetime.fromisoformat(to) if to else datetime.now(timezone.utc)
    frm_dt = datetime.fromisoformat(frm) if frm else to_dt - timedelta(hours=6)
    return market_data.get_candles(symbol, interval, frm_dt, to_dt)


@router.get("/cpr", response_model=CprLevelsOut)
def get_cpr(underlying: str = "NIFTY50", date: str | None = None):
    """Pre-market pivot levels: floor pivots, CPR + width regime, Camarilla,
    two-day CPR relationship, PDH/PDL. See docs/PivotBoss-Roadmap.md."""
    result = pivot_service.get_cpr_levels(underlying, date)
    if not result:
        raise HTTPException(status_code=404, detail=f"No data for underlying '{underlying}'")
    lv = result["levels"]
    return CprLevelsOut(
        underlying=result["underlying"],
        session_date=result["session_date"],
        source=result.get("source", "mock"),
        floor=asdict(lv.floor),
        cpr=asdict(lv.cpr_today),
        camarilla=asdict(lv.camarilla),
        width=asdict(lv.width),
        two_day=asdict(lv.two_day) if lv.two_day else None,
        pdh=lv.pdh, pdl=lv.pdl, pdc=lv.pdc,
    )


@router.get("/expiries")
def get_expiries(underlying: str = "NIFTY50"):
    # Mock: a handful of weekly/monthly expiries from "today". Swap for a real
    # instrument-master lookup once a broker adapter provides one.
    today = datetime.now(timezone.utc).date()
    return {
        "underlying": underlying,
        "expiries": [str(today + timedelta(days=d)) for d in (6, 13, 20, 27)],
    }
