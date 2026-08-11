from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    StrategyConfigOut,
    StrategyCreateIn,
    StrategyTemplateOut,
    StrategyUpdateIn,
)
from app.services import strategy_config_service

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("/templates", response_model=list[StrategyTemplateOut])
def list_templates():
    """The strategy templates the Add Strategy form can build an instance
    from, each with a self-describing param schema so the frontend doesn't
    hardcode field lists."""
    return strategy_config_service.list_templates()


@router.get("", response_model=list[StrategyConfigOut])
def list_strategies(include_inactive: bool = True):
    """Every strategy — built-in (code) and custom (data-driven, built via
    this page) — with its active flag. The Backtester/Strategy Lab pickers
    use GET /backtests/strategies instead, which only shows active ones."""
    return strategy_config_service.list_strategies(include_inactive=include_inactive)


@router.post("", response_model=StrategyConfigOut, status_code=201)
def create_strategy(body: StrategyCreateIn):
    try:
        return strategy_config_service.create_strategy(body.label, body.template, body.params, body.active)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/{key}", response_model=StrategyConfigOut)
def update_strategy(key: str, body: StrategyUpdateIn):
    """Edits a custom strategy's label/params/active flag. For a built-in
    strategy, only label (display override) and active are settable —
    params is ignored since the logic is real code, not data."""
    try:
        return strategy_config_service.update_strategy(key, body.label, body.params, body.active)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{key}", status_code=204)
def delete_strategy(key: str):
    try:
        strategy_config_service.delete_strategy(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
