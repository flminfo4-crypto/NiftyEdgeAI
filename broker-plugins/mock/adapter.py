"""
MockBrokerAdapter — a fake broker that returns the same illustrative
numbers baked into the frontend/ prototype, so the rest of the stack
(backend, ai-engine) can be built and demoed with zero real broker
connection. Swap this for broker-plugins/dhan, /fyers, or /angel-one
by changing one line of config once real credentials exist.

Nothing here talks to a network. Orders are "filled" in-memory
immediately (or rejected ~5% of the time, to exercise error handling).
"""

import random
import uuid
from datetime import datetime, timedelta, timezone

from broker_plugins.core.interface import (
    BrokerAdapter,
    BrokerPosition,
    Candle,
    Margins,
    OptionChainRow,
    OptionChainSnapshot,
    OrderRequest,
    OrderResult,
    Quote,
)

# NOTE: these mirror the numbers hand-authored into frontend/*.html so
# the two stay consistent while there's no live feed. Update both places
# together until the frontend is wired to this API for real.

_QUOTES = {
    "NIFTY50": {"ltp": 23532.45, "change": 182.60, "change_pct": 0.78},
    "NIFTYBANK": {"ltp": 50145.80, "change": 412.30, "change_pct": 0.83},
    "FINNIFTY": {"ltp": 22744.90, "change": 176.20, "change_pct": 0.78},
    "SENSEX": {"ltp": 77259.01, "change": 605.40, "change_pct": 0.79},
    "INDIAVIX": {"ltp": 13.85, "change": -0.45, "change_pct": -3.15},
}

_OPTION_CHAIN_ROWS = [
    # strike, ce_oi, ce_oi_chg, ce_vol, ce_iv, ce_ltp, pe_oi, pe_oi_chg, pe_vol, pe_iv, pe_ltp
    (23100, 5.6, 3.5, 2.3, 13.0, 402.34, 18.5, 4.8, 8.1, 13.1, 0.05),
    (23150, 10.1, 0.4, 8.1, 12.8, 353.97, 8.5, 1.5, 5.7, 13.1, 11.21),
    (23200, 15.2, 4.6, 13.2, 13.0, 314.87, 24.3, 4.0, 18.9, 13.7, 17.55),
    (23250, 12.2, 0.9, 6.0, 13.6, 277.36, 15.8, 1.4, 5.9, 13.6, 24.41),
    (23300, 13.0, -0.9, 7.4, 12.8, 227.53, 26.6, 3.4, 10.8, 13.2, 29.59),
    (23350, 15.2, -1.0, 6.7, 12.8, 183.23, 17.5, 1.9, 7.7, 13.0, 39.01),
    (23400, 24.6, 3.8, 8.0, 13.5, 146.39, 19.1, 5.5, 7.4, 13.5, 41.60),
    (23450, 13.8, 5.1, 5.8, 12.9, 98.87, 40.3, 4.4, 32.7, 13.3, 57.21),
    (23500, 33.4, -0.3, 21.7, 12.8, 58.41, 38.8, 2.9, 25.4, 13.3, 57.95),
    (23550, 12.2, -1.1, 4.2, 13.1, 47.89, 20.6, -0.2, 14.1, 13.1, 101.90),
    (23600, 17.4, -2.0, 6.5, 12.8, 40.77, 5.3, 6.0, 2.5, 13.2, 144.70),
    (23650, 16.6, -2.0, 9.9, 13.2, 39.20, 19.9, 0.7, 17.5, 13.1, 185.47),
    (23700, 5.2, 5.3, 2.4, 13.6, 30.88, 12.0, 3.9, 8.5, 12.9, 228.56),
    (23750, 10.7, -0.4, 3.5, 12.8, 23.50, 7.4, 0.8, 4.0, 13.6, 267.54),
    (23800, 23.5, 1.8, 11.7, 13.2, 18.64, 5.3, 2.4, 2.5, 12.9, 311.12),
    (23850, 23.1, 0.4, 7.4, 13.2, 11.26, 5.4, 1.1, 2.0, 13.3, 362.40),
    (23900, 4.2, -1.0, 2.1, 13.5, 0.05, 9.5, 4.4, 7.9, 13.0, 397.48),
]

_POSITIONS = [
    {"instrument": "NIFTY24JUL23600CE", "side": "SHORT", "qty": 75, "avg_price": 114.25, "ltp": 108.40},
    {"instrument": "NIFTY24JUL23500CE", "side": "SHORT", "qty": 75, "avg_price": 67.40, "ltp": 69.10},
    # NOTE: the original frontend/*.html mockup showed this LONG PE position at
    # LTP 54.15 (below its 58.30 avg) *and* a positive +311.25 P&L, which is
    # internally inconsistent for a long option (LTP below cost = a loss).
    # Kept LTP here at the value that makes the real P&L formula land on the
    # same +311.25 / +7.3% the frontend always displayed, so the live-wired
    # Dashboard (see frontend/js/api.js) and the static mockup agree.
    {"instrument": "NIFTY24JUL23400PE", "side": "LONG", "qty": 75, "avg_price": 58.30, "ltp": 62.45},
]


class MockBrokerAdapter(BrokerAdapter):
    name = "mock"

    def __init__(self) -> None:
        self._connected = False
        self._orders: dict[str, OrderResult] = {}
        random.seed(42)  # deterministic-ish so demo runs are reproducible

    # -- auth ---------------------------------------------------------
    def connect(self, credentials: dict) -> None:
        self._connected = True

    def refresh_token(self) -> None:
        # mock tokens never expire
        return None

    # -- market data ----------------------------------------------------
    def get_quote(self, symbols: list[str]) -> list[Quote]:
        now = datetime.now(timezone.utc)
        out = []
        for sym in symbols:
            key = sym.upper().replace(" ", "")
            data = _QUOTES.get(key)
            if not data:
                continue
            # small deterministic-looking jitter so repeated calls don't look frozen
            jitter = random.uniform(-0.05, 0.05)
            out.append(
                Quote(
                    symbol=sym,
                    ltp=round(data["ltp"] + jitter, 2),
                    change=data["change"],
                    change_pct=data["change_pct"],
                    as_of=now,
                )
            )
        return out

    def get_option_chain(self, underlying: str, expiry: str) -> OptionChainSnapshot:
        rows = [
            OptionChainRow(
                strike=r[0], ce_oi=r[1], ce_oi_change=r[2], ce_volume=r[3], ce_iv=r[4], ce_ltp=r[5],
                pe_oi=r[6], pe_oi_change=r[7], pe_volume=r[8], pe_iv=r[9], pe_ltp=r[10],
            )
            for r in _OPTION_CHAIN_ROWS
        ]
        return OptionChainSnapshot(
            underlying=underlying,
            expiry=expiry,
            as_of=datetime.now(timezone.utc),
            spot_price=_QUOTES["NIFTY50"]["ltp"],
            rows=rows,
        )

    def get_historical_candles(self, symbol: str, interval: str, frm: datetime, to: datetime) -> list[Candle]:
        # simple deterministic synthetic walk — good enough to exercise charting/backtesting code paths
        candles = []
        price = 23450.0
        t = frm
        step = timedelta(minutes=5) if interval.endswith("m") else timedelta(days=1)
        while t <= to and len(candles) < 500:
            o = price
            drift = random.uniform(-12, 14)
            c = o + drift
            h = max(o, c) + random.uniform(1, 8)
            l = min(o, c) - random.uniform(1, 8)
            v = random.uniform(50_000, 400_000)
            candles.append(Candle(ts=t, open=round(o, 2), high=round(h, 2), low=round(l, 2), close=round(c, 2), volume=round(v, 0)))
            price = c
            t += step
        return candles

    # -- orders -----------------------------------------------------------
    def place_order(self, order: OrderRequest) -> OrderResult:
        broker_order_id = str(uuid.uuid4().int)[:15]
        # ~5% simulated broker-side rejection, to exercise error handling downstream
        if random.random() < 0.05:
            result = OrderResult(broker_order_id=broker_order_id, status="REJECTED", rejection_reason="Simulated broker rejection (mock)")
        else:
            fill_price = order.price if order.price else _estimate_ltp(order.instrument)
            result = OrderResult(broker_order_id=broker_order_id, status="EXECUTED", filled_price=fill_price)
        self._orders[broker_order_id] = result
        return result

    def modify_order(self, broker_order_id: str, changes: dict) -> OrderResult:
        existing = self._orders.get(broker_order_id)
        if not existing:
            raise KeyError(f"Unknown order {broker_order_id}")
        return existing

    def cancel_order(self, broker_order_id: str) -> None:
        self._orders.pop(broker_order_id, None)

    # -- account state ------------------------------------------------------
    def get_positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(
                instrument=p["instrument"],
                side=p["side"],
                quantity_lots=p["qty"],
                avg_price=p["avg_price"],
                ltp=p["ltp"],
            )
            for p in _POSITIONS
        ]

    def get_margins(self) -> Margins:
        return Margins(used=324560.00, available=675440.00)


def _estimate_ltp(instrument: str) -> float:
    for r in _OPTION_CHAIN_ROWS:
        strike = r[0]
        if f"{strike:g}CE" in instrument.upper():
            return r[5]
        if f"{strike:g}PE" in instrument.upper():
            return r[10]
    return 0.0
