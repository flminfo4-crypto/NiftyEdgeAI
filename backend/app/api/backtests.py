from fastapi import APIRouter, HTTPException

from app.models.schemas import BacktestRequestIn, BacktestResultOut
from app.services import backtest_service

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _to_result_out(job: dict) -> BacktestResultOut:
    r = job["result"]
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
    )


@router.post("", response_model=BacktestResultOut, status_code=201)
def submit_backtest(body: BacktestRequestIn):
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
