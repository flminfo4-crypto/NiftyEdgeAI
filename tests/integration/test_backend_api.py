"""
Integration tests: backend API against broker-plugins/mock (per tests/README.md
"Integration tests" and "Notes" — risk-limit enforcement gets priority coverage
since a bug there is the difference between a UI annoyance and a real account
blowing past its loss limit).
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
API = "/api/v1"


def test_root_reports_mock_adapter():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["brokerAdapter"] == "mock"


def test_quote_returns_expected_symbols():
    r = client.get(f"{API}/market/quote", params={"symbols": "NIFTY50,INDIAVIX"})
    assert r.status_code == 200
    body = {q["symbol"]: q for q in r.json()}
    assert "NIFTY50" in body and "INDIAVIX" in body
    # LTP should be close to the frontend's baked-in mock value (adapter adds small jitter)
    assert abs(body["NIFTY50"]["ltp"] - 23532.45) < 1.0
    assert body["INDIAVIX"]["changePct"] == -3.15


def test_quote_unknown_symbol_404s():
    r = client.get(f"{API}/market/quote", params={"symbols": "NOT_A_REAL_SYMBOL"})
    assert r.status_code == 404


def test_option_chain_shape():
    r = client.get(f"{API}/market/option-chain", params={"underlying": "NIFTY50", "expiry": "2026-07-31"})
    assert r.status_code == 200
    body = r.json()
    assert body["underlying"] == "NIFTY50"
    assert len(body["rows"]) == 17
    strikes = [row["strike"] for row in body["rows"]]
    assert strikes == sorted(strikes)


def test_bias_matches_dashboard_mock():
    r = client.get(f"{API}/signals/bias")
    assert r.status_code == 200
    body = r.json()
    # Matches the Dashboard's "Market Bias (AI)" card in frontend/index.html exactly,
    # since both are computed from the same ai-engine mock feature defaults.
    assert body["direction"] == "BEARISH"
    assert "23,601" in body["headline"]
    assert body["confidencePct"] == 72
    assert len(body["factors"]) == 6  # incl. the two-day CPR factor (Pivot Boss Ch. 6)


def test_active_signals_include_primary_and_alternative():
    r = client.get(f"{API}/signals/active")
    assert r.status_code == 200
    body = r.json()
    assert body["primary"]["action"]
    assert body["alternative"]["action"]
    assert body["primary"]["confidencePct"] >= body["alternative"]["confidencePct"]


def test_open_positions_have_pnl():
    r = client.get(f"{API}/positions/open")
    assert r.status_code == 200
    positions = r.json()
    assert len(positions) == 3
    by_instrument = {p["instrument"]: p for p in positions}
    # Matches positions.html exactly: SHORT 23600 CE @114.25, LTP 108.40 -> +438.75 / +5.12%
    p = by_instrument["NIFTY24JUL23600CE"]
    assert p["side"] == "SHORT"
    assert p["pnl"] == pytest.approx(438.75, abs=0.01)
    assert p["pnlPct"] == pytest.approx(5.12, abs=0.01)


def test_place_order_success():
    r = client.post(
        f"{API}/orders",
        json={
            "instrument": "NIFTY24JUL23500CE",
            "side": "BUY",
            "orderType": "MARKET",
            "product": "MIS",
            "quantityLots": 75,
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["orderId"].startswith("NE-")
    assert body["status"] in ("EXECUTED", "REJECTED")

    listed = client.get(f"{API}/orders").json()
    assert any(o["orderId"] == body["orderId"] for o in listed)


def test_place_order_rejected_by_risk_engine_exceeding_lot_cap():
    r = client.post(
        f"{API}/orders",
        json={
            "instrument": "NIFTY24JUL23600CE",
            "side": "SELL",
            "orderType": "MARKET",
            "product": "MIS",
            "quantityLots": 5000,
        },
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "RISK_LIMIT_EXCEEDED"
    assert body["limit"] == "maxLotsPerOrder"
    assert body["attemptedValue"] == 5000


def test_place_order_rejected_by_risk_engine_exceeding_exposure():
    # A large deep-ITM writing order (high premium -> high margin block under the
    # mock leverage model) should blow past the 60% max-exposure default while
    # staying under the raw 300-lot cap, isolating the exposure-limit code path.
    r = client.post(
        f"{API}/orders",
        json={
            "instrument": "NIFTY24JUL23100CE",
            "side": "SELL",
            "orderType": "MARKET",
            "product": "MIS",
            "quantityLots": 250,
        },
    )
    assert r.status_code == 422
    assert r.json()["limit"] == "maxExposurePct"


def test_backtest_run_is_deterministic_for_same_seed():
    from niftyedge_ai_engine import run_backtest

    a = run_backtest(seed=123)
    b = run_backtest(seed=123)
    assert a.net_profit == b.net_profit
    assert a.win_rate_pct == b.win_rate_pct
    assert a.total_trades == b.total_trades == 80


def test_submit_backtest_endpoint():
    r = client.post(f"{API}/backtests", json={})
    assert r.status_code == 201
    body = r.json()
    assert body["totalTrades"] == 80
    assert body["winningTrades"] + body["losingTrades"] == body["totalTrades"]

    job_id = body["jobId"]
    r2 = client.get(f"{API}/backtests/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["jobId"] == job_id


def test_system_status_reports_operational_with_mock_adapter():
    r = client.get(f"{API}/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["brokerAdapter"] == "mock"
    assert "OPERATIONAL" in body["marketDataFeed"]
