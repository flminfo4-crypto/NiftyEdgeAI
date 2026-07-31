"""
Unit tests for the Dhan adapter's pure parsing helpers (no network) plus
credential guardrails. Live-API integration can only run with real
DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN set, which CI won't have.
"""

import pytest

from broker_plugins.core.interface import BrokerConnectionError
from broker_plugins.dhan.adapter import DhanBrokerAdapter, parse_candles, parse_option_chain


def test_parse_candles_parallel_arrays():
    data = {
        "open": [23450.0, 23500.0],
        "high": [23580.0, 23610.0],
        "low": [23400.0, 23480.0],
        "close": [23532.45, 23590.0],
        "volume": [1000, 2000],
        "timestamp": [1721871000, 1721957400],
    }
    candles = parse_candles(data)
    assert len(candles) == 2
    assert candles[0].high == 23580.0 and candles[0].close == 23532.45
    assert candles[1].ts > candles[0].ts


def test_parse_candles_empty():
    assert parse_candles({}) == []


def test_parse_option_chain_shape():
    data = {
        "last_price": 23532.45,
        "oc": {
            "23500.000000": {
                "ce": {"last_price": 58.41, "oi": 3340000, "previous_oi": 3370000, "volume": 2170000, "implied_volatility": 12.8},
                "pe": {"last_price": 57.95, "oi": 3880000, "previous_oi": 3590000, "volume": 2540000, "implied_volatility": 13.3},
            },
            "23600.000000": {
                "ce": {"last_price": 40.77, "oi": 1740000, "previous_oi": 1940000, "volume": 650000, "implied_volatility": 12.8},
                "pe": {"last_price": 144.70, "oi": 530000, "previous_oi": 0, "volume": 250000, "implied_volatility": 13.2},
            },
        },
    }
    snap = parse_option_chain("NIFTY50", "2026-08-06", data)
    assert snap.spot_price == 23532.45
    assert [r.strike for r in snap.rows] == [23500.0, 23600.0]
    row = snap.rows[0]
    assert row.ce_ltp == 58.41 and row.pe_ltp == 57.95
    assert row.ce_oi_change == -30000.0  # oi - previous_oi
    assert row.pe_oi_change == 290000.0


def test_connect_without_credentials_raises(monkeypatch):
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    adapter = DhanBrokerAdapter(client_id="", access_token="")
    with pytest.raises(BrokerConnectionError):
        adapter.connect({})
