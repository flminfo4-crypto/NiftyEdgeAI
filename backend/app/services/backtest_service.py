"""
Backtest job orchestration. docs/API/API.md models POST /backtests as an
async job (jobId -> poll GET /backtests/{jobId}) because a real historical
replay can take a while; the ai-engine mock backtester is fast enough to
just run synchronously and store the finished result under a job id, so the
API contract (and any frontend polling code written against it) is honored
without actually needing a task queue yet.
"""

import itertools
import uuid
from datetime import datetime, timezone

from niftyedge_ai_engine import run_backtest

_jobs: dict[str, dict] = {}
_seed_seq = itertools.count(19)


def submit_backtest(request: dict) -> dict:
    job_id = f"BT-{uuid.uuid4().hex[:10]}"
    result = run_backtest(
        starting_capital=request.get("initial_capital", 100_000.0),
        seed=next(_seed_seq),
    )
    record = {
        "job_id": job_id,
        "status": "COMPLETE",
        "request": request,
        "submitted_at": datetime.now(timezone.utc),
        "result": result,
    }
    _jobs[job_id] = record
    return record


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: j["submitted_at"], reverse=True)
