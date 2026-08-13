"""
Wraps ai-engine (the Analytics Engine layer) for the API. Calls it in-process,
per docs/Architecture/Architecture.md's note that this is preferred where
practical for latency-sensitive signal scoring.
"""

from datetime import datetime, timezone

from niftyedge_ai_engine import compute_bias, generate_signals

from app.config import settings
from app.services import market_data, pivot_service, signal_ledger, strike_greeks_service


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


# -- premium-selling setups ---------------------------------------------------
# Live counterpart to the short-premium templates in ai-engine's backtest
# engine. It deliberately evaluates THE SAME two gates those templates use
# (_sell_entry_ok: CPR width regime and a volatility rank), so a setup showing
# ACTIVE here corresponds to a day those strategies would have taken in a
# backtest. If the two ever disagree, one of them is wrong — that
# correspondence is the whole point of not inventing a separate live rule.
#
# This reports CONDITIONS, not instructions: each setup gets a status and the
# reasons behind it, and structures are never ranked against each other.

# Mirrors ShortStraddleParams.min_vol_rank et al in backtest.py.
_MIN_VOL_RANK = 40.0


def _sell_setup_conditions(underlying: str) -> dict:
    """The shared gate's inputs, each fetched from its existing real source.
    Any input that can't be resolved comes back None and is treated as
    'unknown', never as 'passing' — an unknown gate must not green-light a
    short-premium trade."""
    width_regime = None
    try:
        cpr = pivot_service.get_cpr_analysis(underlying)
        if cpr:
            # percentile_regime is the trailing-percentile classification and
            # comes back null without a real broker (it needs real trailing
            # history). levels.width.regime is the always-present formula-based
            # one — a DailyLevels dataclass, not a dict entry. Prefer the
            # percentile read, which is what the backtest gate uses, and fall
            # back to the formula so the page still classifies the day.
            levels = cpr.get("levels")
            width = getattr(levels, "width", None)
            width_regime = cpr.get("percentile_regime") or getattr(width, "regime", None)
    except Exception:
        width_regime = None

    iv_rank = None
    try:
        expiries = market_data.get_expiries(underlying)
        if expiries:
            iv_rank = market_data.get_iv_rank(underlying, expiries[0]).get("iv_rank")
    except Exception:
        iv_rank = None

    gamma_regime = zero_gamma = call_wall = put_wall = None
    try:
        profile = strike_greeks_service.get_gamma_profile(underlying)
        gamma_regime = profile["gamma_regime"]
        zero_gamma = profile.get("zero_gamma_strike")
        call_wall, put_wall = profile.get("call_wall"), profile.get("put_wall")
    except Exception:
        pass

    return {
        "width_regime": width_regime,
        "width_ok": width_regime is not None and width_regime != "NARROW",
        "iv_rank": iv_rank,
        "iv_rank_threshold": _MIN_VOL_RANK,
        "iv_rank_ok": iv_rank is not None and iv_rank >= _MIN_VOL_RANK,
        "gamma_regime": gamma_regime,
        "zero_gamma_strike": zero_gamma,
        "call_wall": call_wall,
        "put_wall": put_wall,
    }


def _setup(name: str, template: str, defined_risk: bool, cond: dict) -> dict:
    """Grades one structure against the gate.

    BLOCKED  — a hard gate failed (or is unknown); the backtested templates
               would not have entered today either.
    WATCH    — the gate passes but the gamma regime argues against this
               particular structure. Negative net GEX means hedging flow
               amplifies moves rather than damping them, which is survivable
               inside a defined-risk structure and is exactly how naked ones
               produce their worst days — so it downgrades the naked builds
               only.
    ACTIVE   — gate passes and nothing argues against the structure.
    """
    reasons = []
    blocked = False

    if cond["width_regime"] is None:
        reasons.append("CPR width regime unavailable — not enough real trailing history to classify today")
        blocked = True
    elif not cond["width_ok"]:
        reasons.append("CPR width is NARROW, which forecasts a trending session — the regime short premium loses in")
        blocked = True
    else:
        reasons.append(f"CPR width regime is {cond['width_regime']}")

    if cond["iv_rank"] is None:
        reasons.append("Volatility rank unavailable")
        blocked = True
    elif not cond["iv_rank_ok"]:
        reasons.append(
            f"Volatility rank {cond['iv_rank']:.0f} is below the {_MIN_VOL_RANK:.0f} floor — "
            "too little credit for the risk"
        )
        blocked = True
    else:
        reasons.append(f"Volatility rank {cond['iv_rank']:.0f} is at or above the {_MIN_VOL_RANK:.0f} floor")

    status = "BLOCKED"
    if not blocked:
        status = "ACTIVE"
        if cond["gamma_regime"] == "NEGATIVE" and not defined_risk:
            status = "WATCH"
            reasons.append(
                "Net gamma exposure is negative — hedging flow amplifies moves, which is where "
                "undefined-risk structures do their worst damage"
            )
        elif cond["gamma_regime"]:
            reasons.append(f"Net gamma exposure is {cond['gamma_regime'].lower()}")

    return {
        "name": name,
        "template": template,
        "defined_risk": defined_risk,
        "status": status,
        "reasons": reasons,
    }


def get_sell_setups(underlying: str = "NIFTY50") -> dict:
    """Whether today's real conditions satisfy the same entry gate the
    short-premium backtest templates use, per structure.

    Every structure is returned with its status and the reasoning behind it.
    Nothing is ranked or recommended, and an unresolvable input blocks rather
    than passes."""
    underlying = underlying.upper().replace(" ", "")
    cond = _sell_setup_conditions(underlying)
    setups = [
        _setup("Short Straddle", "short_straddle", False, cond),
        _setup("Short Strangle", "short_strangle", False, cond),
        _setup("Iron Condor", "iron_condor", True, cond),
        _setup("Iron Fly", "iron_fly", True, cond),
    ]
    active = sum(1 for s in setups if s["status"] == "ACTIVE")
    blocked = sum(1 for s in setups if s["status"] == "BLOCKED")
    verdict = "FAVOURABLE" if active == len(setups) else ("UNFAVOURABLE" if blocked == len(setups) else "MIXED")

    return {
        "underlying": underlying,
        "as_of": datetime.now(timezone.utc),
        "source": "mock" if settings.broker_adapter == "mock" else "broker",
        "verdict": verdict,
        "conditions": cond,
        "setups": setups,
        "note": (
            "These are the same two entry gates the short-premium backtest templates apply "
            "(CPR width regime and volatility rank), evaluated against today's real data, plus "
            "the live gamma regime as an overlay on the undefined-risk builds. This reports "
            "conditions, not instructions — nothing is ranked or recommended, and any input that "
            "cannot be resolved blocks a setup rather than passing it."
        ),
    }
