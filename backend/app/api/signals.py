from fastapi import APIRouter

from app.models.schemas import (
    ActiveSignalsOut,
    BiasOut,
    SellSetupsOut,
    SignalHistoryRowOut,
    SignalStatsOut,
)
from app.services import signal_ledger, signal_service

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/bias", response_model=BiasOut)
def get_bias(underlying: str = "NIFTY50"):
    return signal_service.get_bias(underlying)


@router.get("/active", response_model=ActiveSignalsOut)
def get_active(underlying: str = "NIFTY50"):
    return signal_service.get_active_signals(underlying)


@router.get("/sell-setups", response_model=SellSetupsOut)
def get_sell_setups(underlying: str = "NIFTY50"):
    """Whether today's real conditions clear the same entry gate the
    short-premium backtest templates use — CPR width regime and volatility
    rank — per structure, with the live gamma regime overlaid on the
    undefined-risk builds.

    Reports conditions, not instructions: every structure is returned with its
    status and reasoning, nothing is ranked, and an input that cannot be
    resolved blocks a setup rather than passing it."""
    return signal_service.get_sell_setups(underlying)


@router.get("/history", response_model=list[SignalHistoryRowOut])
def get_history():
    return signal_service.get_signal_history()


@router.get("/stats", response_model=SignalStatsOut)
def get_stats(days: int = 30):
    return signal_ledger.get_stats(days)
