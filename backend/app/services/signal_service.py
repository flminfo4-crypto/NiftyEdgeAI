"""
Wraps ai-engine (the Analytics Engine layer) for the API. Calls it in-process,
per docs/Architecture/Architecture.md's note that this is preferred where
practical for latency-sensitive signal scoring.
"""

from niftyedge_ai_engine import compute_bias, generate_signals

from app.services import signal_ledger


def get_bias(underlying: str = "NIFTY50"):
    return compute_bias()


def get_active_signals(underlying: str = "NIFTY50"):
    result = generate_signals()
    # Feed the real signal-attribution ledger (see signal_ledger.py) so
    # /signals/stats and /signals/history can eventually reconcile these
    # against real price action — a no-op on repeat calls within a few
    # minutes thanks to record_signal's own debounce.
    for kind, signal in (("primary", result["primary"]), ("alternative", result["alternative"])):
        signal_ledger.record_signal(
            underlying=underlying, kind=kind, action=signal.action, instrument=signal.instrument,
            entry_zone=signal.entry_zone, target=signal.target, stop_loss=signal.stop_loss,
            confidence_pct=signal.confidence_pct, direction=result["bias"].direction,
        )
    return result


def get_signal_history():
    from niftyedge_ai_engine.signals import get_signal_history as _history

    return _history()
