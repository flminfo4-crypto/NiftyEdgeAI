from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app.models.schemas import ReportSummaryOut
from app.services import order_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary", response_model=ReportSummaryOut)
def get_report_summary(
    frm: str | None = Query(default=None, alias="from", description="YYYY-MM-DD, defaults to start of this month"),
    to: str | None = Query(default=None, description="YYYY-MM-DD, defaults to today"),
):
    today = datetime.now(timezone.utc).date()
    to_date = to or str(today)
    from_date = frm or str(today.replace(day=1))
    return ReportSummaryOut(**order_service.get_report_summary(from_date, to_date))
