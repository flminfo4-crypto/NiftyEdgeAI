"""
Persistence + lifecycle for user-managed strategy configs (the Strategies
page) — separate from the hand-written strategies baked into ai-engine's
STRATEGY_REGISTRY at import time. Two kinds of entries live in the same
JSON file:

  - "builtin": active/label overrides for the hand-written strategies (code
    can't be deleted through this page, only shown/hidden and relabeled).
  - "custom": full configs (template + params) for strategies created
    through the page — these ARE registered into STRATEGY_REGISTRY at
    startup (if active) via register_custom_strategy, so the backtest
    engine, Strategy Lab sweep, and /backtests see them exactly like any
    hand-written strategy; they don't know the difference.

No database in this app yet (see backend/README.md) — a JSON file next to
the rest of the mock/sample data, same spirit as everything else here.
"""

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from niftyedge_ai_engine import (
    STRATEGY_REGISTRY,
    STRATEGY_TEMPLATES,
    register_custom_strategy,
    unregister_custom_strategy,
)

_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "strategies.json"
_LOCK = threading.Lock()

# Self-describing param schema per template, so the frontend can render the
# add/edit form without hardcoding field lists — add a template here and to
# ai-engine's STRATEGY_TEMPLATES and the page picks it up automatically.
_TEMPLATE_META = {
    "ma_delta_spread": {
        "label": "MA Trend + Delta Credit Spread",
        "description": (
            "Sells a defined-risk vertical credit spread (bull put in an uptrend, "
            "bear call in a downtrend) when a fast/slow SMA crossover confirms the "
            "trend and the CPR width isn't NARROW. The short strike is chosen by "
            "Black-Scholes delta; a further-OTM long leg caps max loss at the "
            "strike width minus the credit received, instead of being exposed to "
            "however far the underlying moves."
        ),
        "params": [
            {"name": "fast_ma", "type": "int", "default": 20, "min": 5, "max": 100,
             "label": "Fast MA (periods)"},
            {"name": "slow_ma", "type": "int", "default": 50, "min": 10, "max": 250,
             "label": "Slow MA (periods)"},
            {"name": "delta_target", "type": "float", "default": 0.25, "min": 0.05, "max": 0.45,
             "label": "Short strike delta"},
            {"name": "wing_offset", "type": "int", "default": 100, "min": 50, "max": 300,
             "label": "Wing width (points)"},
            {"name": "target_scale", "type": "float", "default": 10.0, "min": 1.0, "max": 30.0,
             "label": "Profit-target scale (x the run's target %)"},
            {"name": "skip_narrow", "type": "bool", "default": True,
             "label": "Skip NARROW CPR-width days"},
        ],
    },
}

# Snapshot of registered keys taken at import time — before bootstrap() (or
# any create_strategy call) registers any custom strategy — so this set is
# reliably "real code", never a data-driven one, regardless of call order.
_BUILTIN_KEYS: set[str] = set(STRATEGY_REGISTRY.keys())


def _slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return s or "strategy"


def _to_snake(s: str) -> str:
    return re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), s)


def _to_camel(s: str) -> str:
    head, *tail = s.split("_")
    return head + "".join(w.capitalize() for w in tail)


def _params_to_snake(params: dict) -> dict:
    """The `params` field is a raw dict, so it doesn't go through
    CamelModel's alias generator like every other field in this app —
    incoming camelCase keys (fastMa) are converted to the snake_case
    MaDeltaSpreadParams actually expects (fast_ma) here, once, rather than
    forcing every caller (including register_custom_strategy at startup) to
    handle both spellings."""
    return {_to_snake(k): v for k, v in params.items()}


def _params_to_camel(params: dict) -> dict:
    """Inverse of _params_to_snake — applied when returning a stored config
    to the frontend, so params comes back in the same camelCase convention
    as every other field in the API."""
    return {_to_camel(k): v for k, v in params.items()}


def list_templates() -> list[dict]:
    return [{"template": key, **meta} for key, meta in _TEMPLATE_META.items()]


def _load() -> dict:
    if not _DATA_PATH.exists():
        return {"builtin": {}, "custom": {}}
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def bootstrap() -> None:
    """Loads persisted custom strategy configs and registers the active ones
    into STRATEGY_REGISTRY. Call once at backend startup — before this runs,
    any custom strategy created in a prior process simply isn't runnable
    yet, same as a fresh install."""
    with _LOCK:
        data = _load()
        for key, cfg in data.get("custom", {}).items():
            if not cfg.get("active", True):
                continue
            try:
                register_custom_strategy(key, cfg["label"], cfg["template"], cfg["params"])
            except ValueError:
                continue  # a template that no longer exists — skip rather than crash startup


def list_strategies(include_inactive: bool = True) -> list[dict]:
    with _LOCK:
        data = _load()
    builtin_meta = data.get("builtin", {})
    custom_meta = data.get("custom", {})

    rows = []
    for key in sorted(_BUILTIN_KEYS):
        d = STRATEGY_REGISTRY.get(key)
        if not d:
            continue
        meta = builtin_meta.get(key, {})
        active = meta.get("active", True)
        if not active and not include_inactive:
            continue
        rows.append({
            "key": key, "label": meta.get("label") or d.label, "description": d.description,
            "is_builtin": True, "active": active, "template": None, "params": None,
            "created_at": None, "updated_at": None,
        })
    for key, cfg in custom_meta.items():
        active = cfg.get("active", True)
        if not active and not include_inactive:
            continue
        d = STRATEGY_REGISTRY.get(key)
        rows.append({
            "key": key, "label": cfg["label"], "description": d.description if d else "(inactive — not registered)",
            "is_builtin": False, "active": active, "template": cfg["template"],
            "params": _params_to_camel(cfg["params"]),
            "created_at": cfg.get("created_at"), "updated_at": cfg.get("updated_at"),
        })
    return rows


def is_active(key: str) -> bool:
    """Used by strategy_lab_service to skip inactive strategies in the
    sweep — builtins default active (a strategy nobody has touched via this
    page is on), customs also default active (matches create_strategy's
    own default)."""
    with _LOCK:
        data = _load()
    if key in _BUILTIN_KEYS:
        return data.get("builtin", {}).get(key, {}).get("active", True)
    return data.get("custom", {}).get(key, {}).get("active", True)


def create_strategy(label: str, template: str, params: dict, active: bool = True) -> dict:
    if template not in STRATEGY_TEMPLATES:
        raise ValueError(f"unknown template '{template}' (known: {sorted(STRATEGY_TEMPLATES)})")
    key = _slugify(label)
    params = _params_to_snake(params)
    with _LOCK:
        data = _load()
        if key in _BUILTIN_KEYS or key in data.get("custom", {}):
            raise ValueError(f"a strategy named '{label}' already exists")
        now = datetime.now(timezone.utc).isoformat()
        cfg = {"label": label, "template": template, "params": params, "active": active,
               "created_at": now, "updated_at": now}
        data.setdefault("custom", {})[key] = cfg
        if active:
            register_custom_strategy(key, label, template, params)  # validates params too
        _save(data)
    out = {"key": key, "is_builtin": False, "description": STRATEGY_REGISTRY[key].description if active else "", **cfg}
    out["params"] = _params_to_camel(out["params"])
    return out


def update_strategy(key: str, label: str | None, params: dict | None, active: bool | None) -> dict:
    with _LOCK:
        data = _load()
        if key in _BUILTIN_KEYS:
            meta = data.setdefault("builtin", {}).setdefault(key, {"active": True})
            if label is not None:
                meta["label"] = label
            if active is not None:
                meta["active"] = active
            _save(data)
            d = STRATEGY_REGISTRY[key]
            return {"key": key, "label": meta.get("label") or d.label, "description": d.description,
                    "is_builtin": True, "active": meta.get("active", True), "template": None, "params": None,
                    "created_at": None, "updated_at": None}

        cfg = data.get("custom", {}).get(key)
        if not cfg:
            raise ValueError(f"no strategy '{key}'")
        if label is not None:
            cfg["label"] = label
        if params is not None:
            cfg["params"] = _params_to_snake(params)
        if active is not None:
            cfg["active"] = active
        cfg["updated_at"] = datetime.now(timezone.utc).isoformat()

        if cfg["active"]:
            register_custom_strategy(key, cfg["label"], cfg["template"], cfg["params"])  # validates too
        else:
            unregister_custom_strategy(key)
        _save(data)
        description = STRATEGY_REGISTRY[key].description if cfg["active"] else "(inactive — not registered)"
        out = {"key": key, "is_builtin": False, "description": description, **cfg}
        out["params"] = _params_to_camel(out["params"])
        return out


def delete_strategy(key: str) -> None:
    if key in _BUILTIN_KEYS:
        raise ValueError("built-in strategies can't be deleted, only deactivated")
    with _LOCK:
        data = _load()
        if key not in data.get("custom", {}):
            raise ValueError(f"no strategy '{key}'")
        del data["custom"][key]
        unregister_custom_strategy(key)
        _save(data)
