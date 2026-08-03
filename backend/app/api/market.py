from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models.schemas import (
    CandleOut,
    CprAnalysisOut,
    CprDashboardOut,
    CprLevelsOut,
    CvdOut,
    IvRankOut,
    MarketBreadthOut,
    MarketProfileHistoryRowOut,
    MarketProfileOut,
    OiBuildupRowOut,
    OiSummaryOut,
    OptionChainOut,
    QuoteOut,
    TopNarrowStocksOut,
    VolumeProfileOut,
)
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


@router.get("/cpr-analysis", response_model=CprAnalysisOut)
def get_cpr_analysis(underlying: str = "NIFTY50"):
    """CPR levels plus the composite execution layer: percentile-based
    Narrow/Normal/Wide regime (vs. trailing real sessions), the consecutive-
    narrow-day breakout flag, S1/PDL + R1/PDH support/resistance clusters,
    the No-Trade Zone rule, and an entry/SL/T1/T2 trade plan.

    Percentile/flag/trade-plan fields are only populated when a real broker
    is active (they need real trailing daily history) — under the mock
    adapter they come back null rather than fabricated."""
    result = pivot_service.get_cpr_analysis(underlying)
    if not result:
        raise HTTPException(status_code=404, detail=f"No data for underlying '{underlying}'")
    lv = result["levels"]
    return CprAnalysisOut(
        underlying=result["underlying"],
        session_date=result["session_date"],
        source=result.get("source", "mock"),
        floor=asdict(lv.floor),
        cpr=asdict(lv.cpr_today),
        camarilla=asdict(lv.camarilla),
        width=asdict(lv.width),
        two_day=asdict(lv.two_day) if lv.two_day else None,
        pdh=lv.pdh, pdl=lv.pdl, pdc=lv.pdc,
        current_ltp=result.get("current_ltp"),
        percentile_regime=result.get("percentile_regime"),
        percentile_rank=result.get("percentile_rank"),
        p20_threshold=result.get("p20_threshold"),
        p70_threshold=result.get("p70_threshold"),
        consecutive_narrow_flag=result.get("consecutive_narrow_flag", False),
        support_cluster=result["support_cluster"],
        resistance_cluster=result["resistance_cluster"],
        trade_plan=result["trade_plan"],
    )


@router.get("/top-narrow-stocks", response_model=TopNarrowStocksOut)
def get_top_narrow_stocks():
    """Narrowest-CPR stocks in the broker's tracked universe (NIFTY 50
    constituents for Dhan), ranked from today's real prior-session OHLC.
    Cached ~30min server-side since it needs one historical-candles call per
    stock and prior-day OHLC is fixed until the next close anyway."""
    return pivot_service.get_top_narrow_stocks()


@router.get("/expiries")
def get_expiries(underlying: str = "NIFTY50"):
    return {"underlying": underlying, "expiries": market_data.get_expiries(underlying)}


@router.get("/oi-summary", response_model=OiSummaryOut)
def get_oi_summary(underlying: str = "NIFTY50", expiry: str = settings.default_expiry):
    data = market_data.get_oi_summary(underlying, expiry)
    return OiSummaryOut(pcr=data["pcr"], max_pain=data["max_pain"])


@router.get("/oi-buildup", response_model=list[OiBuildupRowOut])
def get_oi_buildup(underlying: str = "NIFTY50", expiry: str = settings.default_expiry):
    return market_data.get_oi_buildup(underlying, expiry)


@router.get("/volume-profile", response_model=VolumeProfileOut)
def get_volume_profile(underlying: str = "NIFTY50"):
    try:
        return market_data.get_volume_profile(underlying)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No candles available for underlying '{underlying}'")


@router.get("/profile", response_model=MarketProfileOut)
def get_profile(underlying: str = "NIFTY50"):
    try:
        return market_data.get_market_profile(underlying, previous=False)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No candles available for underlying '{underlying}'")


@router.get("/profile/previous-day", response_model=MarketProfileOut)
def get_profile_previous_day(underlying: str = "NIFTY50"):
    try:
        return market_data.get_market_profile(underlying, previous=True)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No candles available for underlying '{underlying}'")


@router.get("/profile/history", response_model=list[MarketProfileHistoryRowOut])
def get_profile_history(underlying: str = "NIFTY50", days: int = 5):
    """VAH/VAL/POC + range for each of the last `days` trading sessions
    (default 5 = one trading week) so market-profile.html can show more than
    a single-session snapshot."""
    return market_data.get_market_profile_history(underlying, days)


@router.get("/cpr-dashboard", response_model=CprDashboardOut)
def get_cpr_dashboard(underlying: str = "NIFTY50"):
    result = market_data.get_cpr_dashboard(underlying)
    if not result:
        raise HTTPException(status_code=404, detail=f"No data for underlying '{underlying}'")
    return result


@router.get("/breadth", response_model=MarketBreadthOut)
def get_market_breadth():
    return market_data.get_market_breadth()


@router.get("/iv-rank", response_model=IvRankOut)
def get_iv_rank(underlying: str = "NIFTY50", expiry: str = settings.default_expiry):
    return market_data.get_iv_rank(underlying, expiry)


@router.get("/cvd", response_model=CvdOut)
def get_cvd(underlying: str = "NIFTY50"):
    return market_data.get_cvd(underlying)
