"""
Wraps ai-engine (the Analytics Engine layer) for the API. Calls it in-process,
per docs/Architecture/Architecture.md's note that this is preferred where
practical for latency-sensitive signal scoring.
"""

from niftyedge_ai_engine import compute_bias, generate_signals


def get_bias(underlying: str = "NIFTY50"):
    return compute_bias()


def get_active_signals(underlying: str = "NIFTY50"):
    return generate_signals()


def get_signal_history():
    from niftyedge_ai_engine.signals import get_signal_history as _history

    return _history()
