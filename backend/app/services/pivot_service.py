"""
Pivot/CPR service — computes the pre-market level set for an underlying via
ai-engine's pivots module (see docs/PivotBoss-Roadmap.md).

Mock mode: prior-session OHLC comes from fixed values consistent with the
rest of the mock stack (frontend ticker, mock adapter quotes) rather than a
real EOD store. When Postgres lands (Roadmap Phase 3), this reads the last
two rows of the daily-OHLC table instead — the ai-engine call is unchanged.
"""

from datetime import date, datetime, timedelta, timezone

from niftyedge_ai_engine.pivots import daily_levels

from app.config import settings

# Prior two sessions' OHLC per underlying (mock). Chosen so NIFTY50's numbers
# sit consistently around the mock spot of 23,532.45.
_MOCK_SESSIONS = {
    "NIFTY50": {
        # prior session (drives today's levels)
        "high": 23580.00, "low": 23400.00, "close": 23532.45,
        # session before that (drives the two-day CPR relationship) — sits
        # higher, so today's CPR classifies LOWER_VALUE (bearish), consistent
        # with the mock stack's overall "BEARISH BELOW VAH" story.
        "prev_high": 23700.00, "prev_low": 23520.00, "prev_close": 23600.00,
    },
    "SENSEX": {
        "high": 77450.00, "low": 76900.00, "close": 77259.01,
        "prev_high": 77100.00, "prev_low": 76500.00, "prev_close": 76980.00,
    },
    "NIFTYBANK": {
        "high": 50320.00, "low": 49890.00, "close": 50145.80,
        "prev_high": 50050.00, "prev_low": 49600.00, "prev_close": 49910.00,
    },
}


def _sessions_from_broker(underlying: str) -> dict | None:
    """When a real adapter (e.g. dhan) is active, pull the last two completed
    daily candles and use them as the CPR inputs. Returns None on any failure
    so the caller can fall back to mock sessions."""
    try:
        from app.services.broker import get_broker

        to = datetime.now(timezone.utc)
        candles = get_broker().get_historical_candles(underlying, "1d", to - timedelta(days=14), to)
        # drop today's still-forming candle if the market is open
        completed = [c for c in candles if c.ts.date() < date.today()]
        if len(completed) < 2:
            return None
        prior, before = completed[-1], completed[-2]
        return {
            "high": prior.high, "low": prior.low, "close": prior.close,
            "prev_high": before.high, "prev_low": before.low, "prev_close": before.close,
            "source": "broker",
        }
    except Exception:
        return None


def get_cpr_levels(underlying: str, session_date: str | None = None) -> dict | None:
    data = None
    source = "mock"
    if settings.broker_adapter != "mock":
        data = _sessions_from_broker(underlying)
        if data:
            source = "broker"
    if data is None:
        data = _MOCK_SESSIONS.get(underlying.upper().replace(" ", ""))
    if not data:
        return None
    levels = daily_levels(
        data["high"], data["low"], data["close"],
        data["prev_high"], data["prev_low"], data["prev_close"],
    )
    return {
        "underlying": underlying,
        "session_date": session_date or str(date.today() + timedelta(days=1)),
        "levels": levels,
        "source": source,
    }
