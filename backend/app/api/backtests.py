from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from niftyedge_ai_engine import STRATEGY_REGISTRY

from app.models.schemas import (
    BacktestRequestIn,
    BacktestResultOut,
    IvRealityCheckOut,
    PeriodReportOut,
    StrategyOut,
    WeeklyStatsOut,
    _to_camel,
)
from app.services import backtest_service

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _to_result_out(job: dict) -> BacktestResultOut:
    r = job["result"]
    ivc = job.get("iv_reality_check")
    return BacktestResultOut(
        job_id=job["job_id"],
        status=job["status"],
        starting_capital=r.starting_capital,
        net_profit=r.net_profit,
        net_profit_pct=r.net_profit_pct,
        win_rate_pct=r.win_rate_pct,
        profit_factor=r.profit_factor,
        sharpe_ratio=r.sharpe_ratio,
        sortino_ratio=r.sortino_ratio,
        max_drawdown_pct=r.max_drawdown_pct,
        volatility_pct=r.volatility_pct,
        total_trades=r.total_trades,
        winning_trades=r.winning_trades,
        losing_trades=r.losing_trades,
        equity_curve=r.equity_curve,
        weekly=WeeklyStatsOut(**asdict(r.weekly)) if r.weekly else None,
        iv_reality_check=IvRealityCheckOut(**ivc) if ivc else None,
    )


@router.get("/virgin-intraday")
def virgin_intraday(
    underlying: str = "NIFTY50",
    years: float = 2.0,
    interval: str = "15m",
    stop_buffer_pct: float = 0.25,
    reward_multiple: float = 2.0,
    morning_window_min: int = 60,
):
    """Virgin-CPR intraday backtest on REAL intraday candles (Dhan serves
    these back to roughly early 2022). Reports both the trading result and
    the measured fill-rate of virgin zones, so the book's claim that most
    get tested within about a week can be checked rather than assumed.
    Heavy on first call (paced 90-day chunk fetches); cached ~1h."""
    from app.services import virgin_intraday_service

    try:
        return virgin_intraday_service.run_virgin_backtest(
            underlying=underlying, years=years, interval=interval,
            stop_buffer_pct=stop_buffer_pct, reward_multiple=reward_multiple,
            morning_window_min=morning_window_min,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/periods", response_model=PeriodReportOut)
def period_report(
    strategy: str = "expiry-theta-crush-seller",
    instrument: str = "NIFTY50_OPTIONS",
    years: float = 1.0,
    period: str = "weekly",
    capital: float = 100_000.0,
    lots: int = 1,
    target: float = 1.0,
    frm: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    stop_loss_pct: float = 1.5,
    target_pct: float = 3.0,
    hold: str = "strategy",
    hold_days: int = 5,
):
    """One row per week (or month) over the requested span — 1 year weekly
    gives 52 rows, monthly gives 12, 2 years weekly gives ~104. Each row
    shows that period's trades, P&L and return measured against the equity
    it started with, plus whether it met the target %. Pass from/to to use
    an explicit range instead of the trailing `years` window."""
    from app.services import period_report_service

    try:
        return period_report_service.run_period_report(
            strategy=strategy, instrument=instrument, years=years, period=period,
            starting_capital=capital, position_size_lots=lots, target_return_pct=target,
            frm_date=frm, to_date=to, stop_loss_pct=stop_loss_pct, target_pct=target_pct,
            hold_mode=hold, custom_hold_days=hold_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _camelize(value):
    """Recursively convert snake_case dict keys to camelCase.

    /lab returns a plain dict rather than a Pydantic model, so it never went
    through CamelModel's alias generator and shipped snake_case while every
    other endpoint in this app ships camelCase (see docs/API/API.md). The
    frontend reads camelCase uniformly, so the payload silently arrived as a
    wall of `undefined`. Converting here keeps the wire contract consistent
    without forcing a dozen nested response models onto a shape that is still
    changing."""
    if isinstance(value, dict):
        return {_to_camel(k): _camelize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camelize(v) for v in value]
    return value


@router.get("/lab")
def strategy_lab(instrument: str = "NIFTY50_OPTIONS", years: int = 6,
                 capital: float = 100_000.0, lots: int = 1):
    """Runs every registered strategy over ~6 years of real data, split into
    in-sample and held-back out-of-sample periods, ranked by weekly-goal
    fitness. Reports out-of-sample degradation so overfitting is visible
    rather than hidden. Heavy: cached ~30min server-side."""
    from app.services import strategy_lab_service

    try:
        return _camelize(strategy_lab_service.run_sweep(
            instrument=instrument, years=years,
            starting_capital=capital, position_size_lots=lots,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/strategies", response_model=list[StrategyOut])
def list_strategies():
    """Every ACTIVE strategy the backtest engine can run — built-in code
    plus any custom strategies created on the Strategies page — sourced
    from strategy_config_service so a deactivated strategy stops showing up
    here even though /backtests will still run it directly by key if asked.
    See GET /strategies for the full list including inactive ones."""
    from app.services import strategy_config_service

    return [
        StrategyOut(key=row["key"], label=row["label"], description=row["description"])
        for row in strategy_config_service.list_strategies(include_inactive=False)
    ]


@router.post("", response_model=BacktestResultOut, status_code=201)
def submit_backtest(body: BacktestRequestIn):
    if body.strategy not in STRATEGY_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{body.strategy}'. Valid: {', '.join(STRATEGY_REGISTRY)}",
        )
    job = backtest_service.submit_backtest(body.model_dump())
    return _to_result_out(job)


@router.get("/{job_id}", response_model=BacktestResultOut)
def get_backtest(job_id: str):
    job = backtest_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Backtest job not found")
    return _to_result_out(job)


@router.get("/{job_id}/trades")
def get_backtest_trades(job_id: str, page: int = 1, page_size: int = 20):
    job = backtest_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Backtest job not found")
    trades = job["result"].trades
    start = (page - 1) * page_size
    items = trades[start : start + page_size]
    return {
        "items": [
            {
                "openedAt": t.opened_at.isoformat(),
                "closedAt": t.closed_at.isoformat(),
                "label": t.label,
                "pnl": t.pnl,
                "result": t.result,
                "entryPrice": t.entry_price,
                "exitPrice": t.exit_price,
            }
            for t in items
        ],
        "total": len(trades),
    }


@router.get("")
def list_backtests():
    jobs = backtest_service.list_jobs()
    return {"items": [_to_result_out(j) for j in jobs], "total": len(jobs)}
