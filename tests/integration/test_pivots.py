"""
Formula-correctness tests for the Pivot Boss module (docs/PivotBoss-Roadmap.md
MVP criteria 1-3): floor pivots, CPR, Camarilla against hand-computed fixtures,
all seven two-day relationships, width regimes, and the /market/cpr endpoint.
"""

import pytest
from fastapi.testclient import TestClient

from niftyedge_ai_engine.pivots import (
    CPR, camarilla, classify_two_day, classify_width, cpr, daily_levels, floor_pivots,
)

from app.main import app

client = TestClient(app)
API = "/api/v1"

# Fixture: prior session H=23580, L=23400, C=23532.45 → range 180,
# pivot = (23580+23400+23532.45)/3 = 23504.15 exactly.
H, L, C = 23580.0, 23400.0, 23532.45


def test_floor_pivots_hand_computed():
    fp = floor_pivots(H, L, C)
    assert fp.pivot == 23504.15
    assert fp.r1 == 23608.30 and fp.s1 == 23428.30      # 2*pivot -/+ low/high
    assert fp.r2 == 23684.15 and fp.s2 == 23324.15      # pivot +/- range
    assert fp.r3 == 23788.30 and fp.s3 == 23248.30      # r1/s1 +/- range
    assert fp.r4 == 23864.15                            # r3 + (r2 - r1)


def test_cpr_hand_computed():
    c = cpr(H, L, C)
    assert c.bc == 23490.00                             # (H+L)/2
    assert c.pivot == 23504.15
    assert c.tc == 23518.30                             # (pivot - bc) + pivot
    assert c.width == 28.30
    assert c.width_pct == pytest.approx(0.1203, abs=0.0002)


def test_cpr_tc_always_above_bc():
    # A session shape where the raw formula inverts TC/BC — labels must swap.
    c = cpr(high=23700, low=23520, close=23600)
    assert c.tc >= c.bc


def test_camarilla_hand_computed():
    cam = camarilla(H, L, C)                            # range*1.1 = 198
    assert cam.h4 == 23631.45 and cam.l4 == 23433.45    # close +/- 99
    assert cam.h3 == 23581.95 and cam.l3 == 23482.95    # close +/- 49.50
    assert cam.h1 == pytest.approx(23548.95, abs=0.01)  # close + 16.50


def test_width_regimes():
    assert classify_width(cpr(H, L, C)).regime == "NARROW"          # 0.12%
    wide = CPR(tc=23700, pivot=23600, bc=23500, width=200, width_pct=0.85)
    assert classify_width(wide).regime == "WIDE"
    normal = CPR(tc=23600, pivot=23560, bc=23520, width=80, width_pct=0.34)
    assert classify_width(normal).regime == "NORMAL"


def test_all_seven_two_day_relationships():
    y = cpr(23500, 23300, 23420)  # width ~13.33

    def mk(dtc, dbc):
        return CPR(tc=y.tc + dtc, pivot=y.pivot + (dtc + dbc) / 2, bc=y.bc + dbc,
                   width=(y.tc + dtc) - (y.bc + dbc), width_pct=1.0)

    expectations = [
        (mk(+200, +200), "HIGHER_VALUE", "BULLISH"),
        (mk(+10, +5), "OVERLAPPING_HIGHER_VALUE", "BULLISH"),
        (mk(-200, -200), "LOWER_VALUE", "BEARISH"),
        (mk(-10, -5), "OVERLAPPING_LOWER_VALUE", "BEARISH"),
        (mk(+1, -1), "UNCHANGED_VALUE", "NEUTRAL"),
        (mk(+8, -8), "OUTSIDE_VALUE", "NEUTRAL"),
        (mk(-4, +4), "INSIDE_VALUE", "NEUTRAL"),
    ]
    for today, rel, direction in expectations:
        got = classify_two_day(today, y)
        assert got.relationship == rel
        assert got.direction == direction


def test_daily_levels_bundle():
    dl = daily_levels(H, L, C, 23700, 23520, 23600)
    assert dl.two_day is not None
    assert dl.two_day.relationship == "LOWER_VALUE"     # today's CPR fully below
    assert dl.pdh == H and dl.pdl == L and dl.pdc == C


def test_market_cpr_endpoint():
    r = client.get(f"{API}/market/cpr", params={"underlying": "NIFTY50"})
    assert r.status_code == 200
    body = r.json()
    assert body["cpr"]["tc"] == 23518.30
    assert body["cpr"]["bc"] == 23490.00
    assert body["floor"]["r1"] == 23608.30
    assert body["camarilla"]["h3"] == 23581.95
    assert body["width"]["regime"] == "NARROW"
    assert body["twoDay"]["relationship"] == "LOWER_VALUE"
    assert body["twoDay"]["direction"] == "BEARISH"
    assert body["pdh"] == 23580.0


def test_market_cpr_unknown_underlying_404s():
    r = client.get(f"{API}/market/cpr", params={"underlying": "NOPE"})
    assert r.status_code == 404


def test_bias_includes_cpr_factor_and_stays_consistent():
    r = client.get(f"{API}/signals/bias")
    assert r.status_code == 200
    body = r.json()
    keys = [f["key"] for f in body["factors"]]
    assert "cpr_two_day" in keys
    # All six mock factors are bearish → unanimous score → confidence still 72,
    # matching the frontend Dashboard card.
    assert body["direction"] == "BEARISH"
    assert body["confidencePct"] == 72
