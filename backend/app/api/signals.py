from fastapi import APIRouter

from app.models.schemas import ActiveSignalsOut, BiasOut, SignalHistoryRowOut
from app.services import signal_service

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/bias", response_model=BiasOut)
def get_bias(underlying: str = "NIFTY50"):
    return signal_service.get_bias(underlying)


@router.get("/active", response_model=ActiveSignalsOut)
def get_active(underlying: str = "NIFTY50"):
    return signal_service.get_active_signals(underlying)


@router.get("/history", response_model=list[SignalHistoryRowOut])
def get_history():
    return signal_service.get_signal_history()
