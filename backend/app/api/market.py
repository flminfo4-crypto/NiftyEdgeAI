from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models.schemas import (
    AtmAnalysisOut,
    CandleOut,
    CprAnalysisOut,
    CprDashboardOut,
    CprLevelsOut,
    CvdOut,
    GammaProfileOut,
    IvRankOut,
    MarketBreadthOut,
    MarketProfileOut,
    OiBuildupRowOut,
    OiSummaryOut,
    OptionChainOut,
    OptionPressureOut,
    QuoteOut,
    StrikeGreeksOut,
    TopNarrowStocksOut,
    TpoProfileCompositeSessionOut,
    TpoProfileOut,
    VirginPocsOut,
    VolumeProfileCompositeSessionOut,
    VolumeProfileOut,
)
from app.services import atm_analysis_service, market_data, pivot_service, strike_greeks_service

# TPO bracket sizes offered by the timeframe dropdown on market-profile.html:
# 1/5/15/30/45 min, 1hr, 4hr and a whole session as one bracket.
_VALID_BRACKETS = (1, 5, 15, 30, 45, 60, 240, 1440)

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
        session_open=result.get("session_open"),
        bias_confirmation=result.get("bias_confirmation"),
        pivot_trend=result.get("pivot_trend"),
    )


@router.get("/pressure", response_model=OptionPressureOut)
def get_option_pressure(underlying: str = "NIFTY50", expiry: str | None = None):
    """CE vs PE pressure read from the live chain's real OI and premium
    changes around ATM — who is writing, buying, unwinding or covering, and
    which way that leans. Defaults to the nearest real expiry."""
    try:
        if not expiry:
            expiries = market_data.get_expiries(underlying)
            if not expiries:
                raise HTTPException(status_code=404, detail=f"No expiries listed for '{underlying}'")
            expiry = expiries[0]
        return market_data.get_option_pressure(underlying, expiry)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/atm-analysis", response_model=AtmAnalysisOut)
def get_atm_analysis(
    underlying: str = "NIFTY50",
    frm: str = Query(..., alias="from", description="YYYY-MM-DD"),
    to: str = Query(..., description="YYYY-MM-DD"),
    expiry: str = Query("weekly", description="weekly | monthly"),
    interval: str = Query("15m", description="1m | 5m | 15m | 30m"),
):
    """Rolling-ATM time grid: per intraday bucket — real spot H/L/C, the
    strike that WAS ATM at that moment, its real CE/PE premium H/L/C, the
    straddle, and a computed reason for the move. Click-triggered and
    heavily cached; option premiums are bounded by Dhan's live-contract
    retention (no expired-contract history exists)."""
    try:
        return atm_analysis_service.get_atm_analysis(underlying, frm, to, expiry, interval)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/strike-greeks", response_model=StrikeGreeksOut)
def get_strike_greeks(
    underlying: str = "NIFTY50",
    frm: str = Query(..., alias="from", description="YYYY-MM-DD"),
    to: str = Query(..., description="YYYY-MM-DD"),
    expiry: str = Query("weekly", description="weekly | monthly"),
    interval: str = Query("15m", description="1m | 5m | 15m | 30m"),
    depth: int = Query(2, ge=1, le=3, description="strikes either side of ATM"),
):
    """ATM±depth strike ladder over time: per intraday bucket, every CE and PE
    leg's real premium, OI, solved IV, Greeks and OI-weighted gamma exposure.
    Moneyness is per side (a call is ITM below spot, a put above it). Ranges
    are capped tighter than /atm-analysis because one query fans out to roughly
    five times as many Dhan contract fetches."""
    try:
        return strike_greeks_service.get_strike_greeks(underlying, frm, to, expiry, interval, depth)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/gamma-profile", response_model=GammaProfileOut)
def get_gamma_profile(
    underlying: str = "NIFTY50",
    expiry: str | None = Query(None, description="YYYY-MM-DD; defaults to the nearest expiry"),
    width: int = Query(15, ge=3, le=40, description="strikes either side of ATM"),
):
    """Live gamma exposure by strike, with the zero-gamma flip, call wall and
    put wall. Complements /strike-greeks: that one is gamma through time on a
    few strikes, this is gamma across the chain right now."""
    try:
        return strike_greeks_service.get_gamma_profile(underlying, expiry, width)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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


@router.get("/tpo-profile", response_model=TpoProfileOut)
def get_tpo_profile(underlying: str = "NIFTY50", previous: bool = False, bracket: int = 30):
    """Full TPO Market Profile for a session — letter grid, value area,
    Initial Balance, range extension, single prints, poor high/low, day-type
    classification with its reasoning, and the two-day value-area shift."""
    if bracket not in _VALID_BRACKETS:
        raise HTTPException(status_code=400, detail=f"bracket must be one of {_VALID_BRACKETS} minutes")
    try:
        return market_data.get_tpo_profile(underlying, previous=previous, bracket_minutes=bracket)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/virgin-pocs", response_model=VirginPocsOut)
def get_virgin_pocs(underlying: str = "NIFTY50", bracket: int = 30):
    """Points of control from recent sessions that price has not traded back
    to. The book treats an untested POC as a magnet — the fairest price of a
    session, left unvisited. Needs one intraday fetch per prior session, so
    it is a separate slow endpoint (cached ~1h) rather than part of the
    2s-polled profile."""
    if bracket not in _VALID_BRACKETS:
        raise HTTPException(status_code=400, detail=f"bracket must be one of {_VALID_BRACKETS} minutes")
    try:
        return market_data.get_virgin_pocs(underlying, bracket_minutes=bracket)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/tpo-profile/composite", response_model=list[TpoProfileCompositeSessionOut])
def get_tpo_profile_composite(underlying: str = "NIFTY50", sessions: int = 5, bracket: int = 30, offset: int = 0):
    """Multi-session TPO composite (full letter-grid per session, IB/VA,
    poor high/low, volume, and a best-effort bar-structure label) behind
    market-profile.html's composite chart. `offset` skips the N most recent
    sessions, letting the page's Older/Newer controls page back through
    history without re-fetching everything up to today."""
    if bracket not in _VALID_BRACKETS:
        raise HTTPException(status_code=400, detail=f"bracket must be one of {_VALID_BRACKETS} minutes")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    return market_data.get_tpo_profile_composite(underlying, sessions, bracket_minutes=bracket, offset=offset)


@router.get("/volume-profile/composite", response_model=list[VolumeProfileCompositeSessionOut])
def get_volume_profile_composite(underlying: str = "NIFTY50", sessions: int = 5, offset: int = 0):
    """Multi-session composite of the real volume-by-price histogram behind
    volume-profile.html's composite chart. `offset` skips the N most recent
    sessions (see get_tpo_profile_composite)."""
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    return market_data.get_volume_profile_composite(underlying, sessions, offset=offset)


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
