"""
Real signal-attribution ledger: records each generated signal's prediction
(target/stop/confidence) at the moment it fires, then reconciles it later
against real historical candles to produce genuine hit-rate/R:R/calibration
stats — answering "what did the AI predict, and what actually happened"
instead of ai-engine's synthetic example numbers.

In-memory only, like order_service._orders — a stand-in for a real table
until the backend is wired to Postgres (see backend/README.md, "not wired
up yet"). Resets on restart; that's a disclosed limitation, not a bug: there's
nowhere else in this codebase that persists across restarts either.

Only ai-engine's two real signal generators feed this (see
niftyedge_ai_engine/signals.py): SELL CALL (CE WRITING), BUY CALL (CE BUYING),
BUY PUT (PE BUYING), IRON CONDOR. "SELL PUT (PE WRITING)" and
"Straddle/Strangle" appear in the old static mockup but are never actually
produced, so they never show up here — no fabricated categories.
"""

import itertools
import re
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.services.broker import get_broker

# NIFTY's current standard lot size (75 -> 65 from the Jan 2026 series) — used
# only to turn a real point-move into a real 1-lot currency figure for display;
# no trade is actually implied or placed for these ledger entries. Kept in sync
# with ai-engine's contract_spec, the source of truth.
_LOT_SIZE = 65

_STRATEGY_BUCKETS = {
    "SELL CALL (CE WRITING)": "CE Writing",
    "BUY CALL (CE BUYING)": "CE Buying",
    "BUY PUT (PE BUYING)": "PE Buying",
    "SELL PUT (PE WRITING)": "PE Writing",
    "IRON CONDOR": "Iron Condor",
}

# Don't record a "new" entry for the same (underlying, kind) slot more than once
# within this window, even if content drifted slightly — avoids spamming the
# ledger from rapid page reloads while spot hovers near a strike boundary.
_DEBOUNCE = timedelta(minutes=5)

# Housekeeping: drop entries older than this so an always-on process doesn't
# grow the in-memory dict forever.
_MAX_AGE = timedelta(days=90)

_NUM_RE = re.compile(r"[\d,]+\.?\d*")

_id_seq = itertools.count(1)
_ledger: dict[str, dict] = {}


def _parse_num(s: str) -> float | None:
    m = _NUM_RE.search(s or "")
    return float(m.group().replace(",", "")) if m else None


def record_signal(
    underlying: str, kind: str, action: str, instrument: str, entry_zone: str,
    target: str, stop_loss: str, confidence_pct: int, direction: str,
) -> None:
    """Called every time a signal is generated (see signal_service.get_active_signals).
    Cheap to call repeatedly — the debounce + same-signal check make it a no-op
    on ordinary page reloads where the signal hasn't actually changed."""
    now = datetime.now(timezone.utc)
    same_slot = [e for e in _ledger.values() if e["underlying"] == underlying and e["kind"] == kind]
    if same_slot:
        last = max(same_slot, key=lambda e: e["generated_at"])
        same_signal = (last["action"], last["instrument"], last["entry_zone"]) == (action, instrument, entry_zone)
        if same_signal and now - last["generated_at"] < _DEBOUNCE:
            return

    target_val = _parse_num(target)
    stop_val = _parse_num(stop_loss)
    entry_mid = _parse_num(entry_zone)

    entry_id = f"SIG-{next(_id_seq):06d}"
    _ledger[entry_id] = {
        "id": entry_id,
        "underlying": underlying,
        "kind": kind,
        "action": action,
        "strategy": _STRATEGY_BUCKETS.get(action, action),
        "instrument": instrument,
        "direction": direction,
        "entry_zone": entry_zone,
        "entry_mid": entry_mid,
        "target": target_val,
        "stop_loss": stop_val,
        "confidence_pct": confidence_pct,
        "generated_at": now,
        "expiry": settings.default_expiry,
        # IRON CONDOR's target/stop are "—" (non-numeric) — can't be numerically
        # reconciled against price, so it's tracked but excluded from hit-rate math
        # rather than assigned a fabricated result.
        "status": "OPEN" if target_val is not None and stop_val is not None else "NOT_TRACKED",
        "resolved_at": None,
        "resolved_price": None,
        "pnl": None,
    }

    cutoff = now - _MAX_AGE
    for k in [k for k, e in _ledger.items() if e["generated_at"] < cutoff]:
        del _ledger[k]


def _reconcile() -> None:
    open_entries = [e for e in _ledger.values() if e["status"] == "OPEN"]
    if not open_entries:
        return
    broker = get_broker()
    now = datetime.now(timezone.utc)

    by_underlying: dict[str, list[dict]] = {}
    for e in open_entries:
        by_underlying.setdefault(e["underlying"], []).append(e)

    for underlying, entries in by_underlying.items():
        earliest = min(e["generated_at"] for e in entries)
        try:
            candles = broker.get_historical_candles(underlying, "5m", earliest, now)
        except Exception:
            continue  # broker unavailable right now — stays OPEN, retried on the next call

        for e in entries:
            bullish_setup = e["target"] > e["stop_loss"]
            resolved = False
            for c in candles:
                if c.ts < e["generated_at"]:
                    continue
                hit_target = c.high >= e["target"] if bullish_setup else c.low <= e["target"]
                hit_stop = c.low <= e["stop_loss"] if bullish_setup else c.high >= e["stop_loss"]
                if hit_target and hit_stop:
                    # Both levels traded within the same candle — OHLC alone can't say
                    # which came first. Tie-break on whichever is closer to the candle's
                    # open, a documented approximation, not a coin flip.
                    target_first = abs(c.open - e["target"]) <= abs(c.open - e["stop_loss"])
                    hit_target, hit_stop = target_first, not target_first
                if hit_target:
                    e["status"], e["resolved_at"], e["resolved_price"] = "TARGET_HIT", c.ts, e["target"]
                    resolved = True
                elif hit_stop:
                    e["status"], e["resolved_at"], e["resolved_price"] = "SL_HIT", c.ts, e["stop_loss"]
                    resolved = True
                if resolved:
                    if e["entry_mid"] is not None:
                        distance = abs(e["resolved_price"] - e["entry_mid"])
                        e["pnl"] = round(distance * _LOT_SIZE * (1 if e["status"] == "TARGET_HIT" else -1), 2)
                    break

            if not resolved:
                try:
                    expiry_date = datetime.fromisoformat(e["expiry"]).replace(tzinfo=timezone.utc)
                except ValueError:
                    expiry_date = None
                # +10h clears same-day settlement before calling it expired
                if expiry_date and now > expiry_date + timedelta(hours=10):
                    e["status"] = "EXPIRED"


def get_history(days: int = 30) -> list[dict]:
    _reconcile()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = [e for e in _ledger.values() if e["generated_at"] >= cutoff and e["status"] != "NOT_TRACKED"]
    rows.sort(key=lambda e: e["generated_at"], reverse=True)
    return rows


def get_stats(days: int = 30) -> dict:
    rows = get_history(days)
    resolved = [e for e in rows if e["status"] in ("TARGET_HIT", "SL_HIT")]
    hits = [e for e in resolved if e["status"] == "TARGET_HIT"]
    hit_rate = (len(hits) / len(resolved) * 100) if resolved else 0.0

    today = datetime.now(timezone.utc).date()
    today_resolved = [e for e in resolved if e["generated_at"].date() == today]
    today_hits = [e for e in today_resolved if e["status"] == "TARGET_HIT"]
    today_hit_rate = (len(today_hits) / len(today_resolved) * 100) if today_resolved else 0.0

    rr_values = []
    for e in rows:
        if e["target"] is not None and e["stop_loss"] is not None and e["entry_mid"]:
            reward = abs(e["target"] - e["entry_mid"])
            risk = abs(e["stop_loss"] - e["entry_mid"])
            if risk:
                rr_values.append(reward / risk)
    avg_rr = sum(rr_values) / len(rr_values) if rr_values else 0.0

    by_strategy: dict[str, list[dict]] = {}
    for e in resolved:
        by_strategy.setdefault(e["strategy"], []).append(e)
    strategy_stats = [
        {
            "strategy": strat,
            "hit_rate_pct": round(sum(1 for e in es if e["status"] == "TARGET_HIT") / len(es) * 100, 1),
            "sample_size": len(es),
        }
        for strat, es in sorted(by_strategy.items())
    ]

    buckets = [(0, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    calibration = []
    for lo, hi in buckets:
        bucket_rows = [e for e in resolved if lo <= e["confidence_pct"] < hi]
        if not bucket_rows:
            continue
        bucket_hit_rate = sum(1 for e in bucket_rows if e["status"] == "TARGET_HIT") / len(bucket_rows) * 100
        calibration.append(
            {
                "confidence_range": f"{lo}-{min(hi, 100)}%",
                "hit_rate_pct": round(bucket_hit_rate, 1),
                "sample_size": len(bucket_rows),
            }
        )

    return {
        "today_hit_rate_pct": round(today_hit_rate, 1),
        "hit_rate_pct": round(hit_rate, 1),
        "avg_risk_reward": round(avg_rr, 2),
        "resolved_count": len(resolved),
        "open_count": sum(1 for e in rows if e["status"] == "OPEN"),
        "by_strategy": strategy_stats,
        "confidence_calibration": calibration,
    }
