"""
Dhan broker adapter — DhanHQ API v2 (https://dhanhq.co/docs/v2/).

Auth model: static `client-id` + `access-token` (JWT generated from the Dhan
web app; validity is limited, typically 24h, so regenerate and update the env
when calls start failing 401). Set via environment:

    DHAN_CLIENT_ID=1000000001
    DHAN_ACCESS_TOKEN=eyJ...

Endpoints used (all under https://api.dhan.co/v2):
    POST /marketfeed/quote      quotes with net change
    POST /charts/historical     daily OHLC (CPR inputs)
    POST /charts/intraday       minute OHLC
    POST /optionchain           full chain w/ OI, IV, greeks (rate limit: 1 req / 3 s)
    POST /optionchain/expirylist
    GET  /positions             open positions
    GET  /fundlimit             margins
    POST /orders, PUT /orders/{id}, DELETE /orders/{id}

Requires `httpx` (already in backend/requirements.txt).

NOTE on security IDs: index IDs below are from Dhan's published annexure and
instrument master (https://dhanhq.co/docs/v2/instruments/). If Dhan revises
its instrument master, update _INDEX_MAP accordingly.
"""

import os
from datetime import datetime, timedelta, timezone

import httpx

from broker_plugins.core.interface import (
    BrokerAdapter,
    BrokerConnectionError,
    BrokerOrderError,
    BrokerPosition,
    Candle,
    Margins,
    MarketBreadth,
    OptionChainRow,
    OptionChainSnapshot,
    OrderRequest,
    OrderResult,
    Quote,
)

_BASE = "https://api.dhan.co/v2"

# our symbol -> (securityId, exchangeSegment, instrument)
_INDEX_MAP = {
    "NIFTY50": ("13", "IDX_I", "INDEX"),
    "NIFTYBANK": ("25", "IDX_I", "INDEX"),
    "FINNIFTY": ("27", "IDX_I", "INDEX"),
    "SENSEX": ("51", "IDX_I", "INDEX"),
    "INDIAVIX": ("21", "IDX_I", "INDEX"),
}

_ORDER_TYPE_MAP = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOP_LOSS", "SL-M": "STOP_LOSS_MARKET"}
_PRODUCT_MAP = {"MIS": "INTRADAY", "NRML": "MARGIN"}

# NIFTY 50 constituents -> Dhan security ID, resolved from Dhan's public
# instrument master (images.dhan.co/api-data/api-scrip-master.csv, NSE/EQ
# rows). Dhan has no "index constituents" API, so this is a maintained
# snapshot rather than a live feed — it will drift as the index is
# periodically rebalanced. 48 of 50: TATAMOTORS and LTIM weren't resolvable
# under those trading symbols in the master (likely renamed by a corporate
# action since this list was compiled) and were left out rather than guessed.
_NIFTY50_CONSTITUENTS = {
    "RELIANCE": "2885", "TCS": "11536", "HDFCBANK": "1333", "ICICIBANK": "4963", "INFY": "1594",
    "ITC": "1660", "SBIN": "3045", "BHARTIARTL": "10604", "KOTAKBANK": "1922", "LT": "11483",
    "HINDUNILVR": "1394", "AXISBANK": "5900", "BAJFINANCE": "317", "ASIANPAINT": "236", "MARUTI": "10999",
    "SUNPHARMA": "3351", "TITAN": "3506", "ULTRACEMCO": "11532", "NESTLEIND": "17963", "WIPRO": "3787",
    "ONGC": "2475", "NTPC": "11630", "POWERGRID": "14977", "M&M": "2031", "TATASTEEL": "3499",
    "JSWSTEEL": "11723", "ADANIENT": "25", "ADANIPORTS": "15083", "COALINDIA": "20374", "HCLTECH": "7229",
    "TECHM": "13538", "BAJAJFINSV": "16675", "DRREDDY": "881", "CIPLA": "694", "GRASIM": "1232",
    "BRITANNIA": "547", "EICHERMOT": "910", "HEROMOTOCO": "1348", "DIVISLAB": "10940", "APOLLOHOSP": "157",
    "BPCL": "526", "HINDALCO": "1363", "INDUSINDBK": "5258", "SBILIFE": "21808", "HDFCLIFE": "467",
    "BAJAJ-AUTO": "16669", "TATACONSUM": "3432", "UPL": "11287",
}


class DhanBrokerAdapter(BrokerAdapter):
    name = "dhan"

    def __init__(self, client_id: str | None = None, access_token: str | None = None, timeout: float = 15.0):
        self._client_id = client_id or os.getenv("DHAN_CLIENT_ID", "")
        self._token = access_token or os.getenv("DHAN_ACCESS_TOKEN", "")
        self._timeout = timeout

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": self._token,
            "client-id": self._client_id,
        }

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = httpx.post(f"{_BASE}{path}", json=body, headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise BrokerConnectionError(f"Dhan request failed: {exc}") from exc
        if r.status_code == 401:
            raise BrokerConnectionError("Dhan rejected credentials (401) — regenerate DHAN_ACCESS_TOKEN")
        if r.status_code >= 400:
            raise BrokerOrderError(f"Dhan {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def _get(self, path: str) -> dict | list:
        try:
            r = httpx.get(f"{_BASE}{path}", headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise BrokerConnectionError(f"Dhan request failed: {exc}") from exc
        if r.status_code == 401:
            raise BrokerConnectionError("Dhan rejected credentials (401) — regenerate DHAN_ACCESS_TOKEN")
        if r.status_code >= 400:
            raise BrokerOrderError(f"Dhan {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    # -- auth -------------------------------------------------------------

    def connect(self, credentials: dict) -> None:
        if credentials.get("client_id"):
            self._client_id = credentials["client_id"]
        if credentials.get("access_token"):
            self._token = credentials["access_token"]
        if not self._client_id or not self._token:
            raise BrokerConnectionError(
                "Dhan credentials missing — set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN "
                "(generate the token from web.dhan.co -> My Profile -> DhanHQ Trading APIs)."
            )
        # cheap validation call
        self._get("/fundlimit")

    def refresh_token(self) -> None:
        # Dhan tokens are static JWTs generated manually; nothing to refresh
        # programmatically. connect() will raise once the token expires.
        return None

    # -- market data ------------------------------------------------------

    def get_quote(self, symbols: list[str]) -> list[Quote]:
        # group by segment for the marketfeed request body
        body: dict[str, list[int]] = {}
        wanted: dict[tuple[str, str], str] = {}
        for sym in symbols:
            key = sym.upper().replace(" ", "")
            if key in _INDEX_MAP:
                sec_id, seg, _ = _INDEX_MAP[key]
            elif key in _NIFTY50_CONSTITUENTS:
                sec_id, seg = _NIFTY50_CONSTITUENTS[key], "NSE_EQ"
            else:
                continue
            body.setdefault(seg, []).append(int(sec_id))
            wanted[(seg, sec_id)] = sym
        if not body:
            return []

        data = self._post("/marketfeed/quote", body).get("data", {})
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for seg, items in data.items():
            for sec_id, q in items.items():
                sym = wanted.get((seg, str(sec_id)))
                if not sym:
                    continue
                ltp = float(q.get("last_price", 0))
                change = float(q.get("net_change", 0))
                prev = ltp - change
                out.append(Quote(
                    symbol=sym, ltp=round(ltp, 2), change=round(change, 2),
                    change_pct=round(change / prev * 100, 2) if prev else 0.0,
                    as_of=now,
                ))
        return out

    def get_historical_candles(self, symbol: str, interval: str, frm: datetime, to: datetime) -> list[Candle]:
        key = symbol.upper().replace(" ", "")
        if key in _INDEX_MAP:
            sec_id, seg, inst = _INDEX_MAP[key]
        elif key in _NIFTY50_CONSTITUENTS:
            sec_id, seg, inst = _NIFTY50_CONSTITUENTS[key], "NSE_EQ", "EQUITY"
        else:
            raise BrokerOrderError(f"No Dhan security mapping for symbol '{symbol}'")

        if interval.endswith("m"):
            payload = {
                "securityId": sec_id, "exchangeSegment": seg, "instrument": inst,
                "interval": interval.rstrip("m"),
                "fromDate": frm.strftime("%Y-%m-%d %H:%M:%S"),
                "toDate": to.strftime("%Y-%m-%d %H:%M:%S"),
            }
            data = self._post("/charts/intraday", payload)
        else:
            payload = {
                "securityId": sec_id, "exchangeSegment": seg, "instrument": inst,
                "fromDate": frm.strftime("%Y-%m-%d"),
                # Dhan's toDate is non-inclusive for dailies — pad one day
                "toDate": (to + timedelta(days=1)).strftime("%Y-%m-%d"),
            }
            data = self._post("/charts/historical", payload)
        return parse_candles(data)

    def get_expiry_list(self, underlying: str) -> list[str]:
        key = underlying.upper().replace(" ", "")
        if key not in _INDEX_MAP:
            raise BrokerOrderError(f"No Dhan security mapping for underlying '{underlying}'")
        sec_id, seg, _ = _INDEX_MAP[key]
        data = self._post("/optionchain/expirylist", {"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg})
        return data.get("data", [])

    def get_option_chain(self, underlying: str, expiry: str) -> OptionChainSnapshot:
        key = underlying.upper().replace(" ", "")
        if key not in _INDEX_MAP:
            raise BrokerOrderError(f"No Dhan security mapping for underlying '{underlying}'")
        sec_id, seg, _ = _INDEX_MAP[key]
        data = self._post("/optionchain", {
            "UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg, "Expiry": expiry,
        }).get("data", {})
        return parse_option_chain(underlying, expiry, data)

    def get_market_breadth(self) -> MarketBreadth:
        ids = [int(v) for v in _NIFTY50_CONSTITUENTS.values()]
        data = self._post("/marketfeed/quote", {"NSE_EQ": ids}).get("data", {}).get("NSE_EQ", {})
        advancing = declining = unchanged = new_highs = new_lows = 0
        for q in data.values():
            change = float(q.get("net_change", 0) or 0)
            if change > 0:
                advancing += 1
            elif change < 0:
                declining += 1
            else:
                unchanged += 1
            ltp = float(q.get("last_price", 0) or 0)
            high_52w = float(q.get("52_week_high", 0) or 0)
            low_52w = float(q.get("52_week_low", 0) or 0)
            if high_52w and ltp >= high_52w:
                new_highs += 1
            if low_52w and ltp <= low_52w:
                new_lows += 1
        return MarketBreadth(
            advancing=advancing, declining=declining, unchanged=unchanged,
            new_highs=new_highs, new_lows=new_lows,
            universe_size=len(data), universe_label=f"NIFTY 50 constituents ({len(data)}/50 mapped)",
        )

    def get_universe_symbols(self) -> list[str]:
        return list(_NIFTY50_CONSTITUENTS.keys())

    # -- orders -----------------------------------------------------------

    def place_order(self, order: OrderRequest) -> OrderResult:
        payload = {
            "dhanClientId": self._client_id,
            "transactionType": order.side,
            "exchangeSegment": "NSE_FNO",
            "productType": _PRODUCT_MAP[order.product],
            "orderType": _ORDER_TYPE_MAP[order.order_type],
            "validity": "DAY",
            "securityId": order.instrument,  # NOTE: Dhan wants securityId, not a symbol —
                                             # instrument-master lookup TBD for options
            "quantity": order.quantity_lots,
            "price": order.price or 0,
            "triggerPrice": order.trigger_price or 0,
        }
        resp = self._post("/orders", payload)
        status = str(resp.get("orderStatus", "PENDING")).upper()
        mapped = "EXECUTED" if status == "TRADED" else "REJECTED" if status == "REJECTED" else "PENDING"
        return OrderResult(broker_order_id=str(resp.get("orderId", "")), status=mapped)

    def modify_order(self, broker_order_id: str, changes: dict) -> OrderResult:
        resp = self._post(f"/orders/{broker_order_id}", {"dhanClientId": self._client_id, **changes})
        return OrderResult(broker_order_id=broker_order_id, status="PENDING")

    def cancel_order(self, broker_order_id: str) -> None:
        try:
            r = httpx.delete(f"{_BASE}/orders/{broker_order_id}", headers=self._headers(), timeout=self._timeout)
            if r.status_code >= 400:
                raise BrokerOrderError(f"Dhan cancel -> {r.status_code}: {r.text[:200]}")
        except httpx.HTTPError as exc:
            raise BrokerConnectionError(f"Dhan request failed: {exc}") from exc

    # -- account state ----------------------------------------------------

    def get_positions(self) -> list[BrokerPosition]:
        rows = self._get("/positions") or []
        out: list[BrokerPosition] = []
        for p in rows:
            qty = int(p.get("netQty", 0))
            if qty == 0:
                continue
            out.append(BrokerPosition(
                instrument=p.get("tradingSymbol", p.get("securityId", "?")),
                side="LONG" if qty > 0 else "SHORT",
                quantity_lots=abs(qty),
                avg_price=float(p.get("buyAvg" if qty > 0 else "sellAvg", 0) or 0),
                ltp=float(p.get("lastTradedPrice", 0) or 0),
            ))
        return out

    def get_margins(self) -> Margins:
        f = self._get("/fundlimit")
        # Dhan's field name is genuinely spelled "availabelBalance" in v2
        available = float(f.get("availabelBalance", f.get("availableBalance", 0)) or 0)
        used = float(f.get("utilizedAmount", 0) or 0)
        return Margins(used=round(used, 2), available=round(available, 2))


# -- pure parsing helpers (unit-testable without network) -------------------


def parse_candles(data: dict) -> list[Candle]:
    """Dhan returns parallel arrays (open[], high[], ..., timestamp[])."""
    opens = data.get("open", [])
    highs = data.get("high", [])
    lows = data.get("low", [])
    closes = data.get("close", [])
    vols = data.get("volume", [])
    stamps = data.get("timestamp", [])
    out = []
    for i in range(min(len(opens), len(highs), len(lows), len(closes), len(stamps))):
        out.append(Candle(
            ts=datetime.fromtimestamp(int(stamps[i]), tz=timezone.utc),
            open=float(opens[i]), high=float(highs[i]), low=float(lows[i]),
            close=float(closes[i]),
            volume=float(vols[i]) if i < len(vols) else 0.0,
        ))
    return out


def parse_option_chain(underlying: str, expiry: str, data: dict) -> OptionChainSnapshot:
    """Dhan option chain: data.last_price + data.oc = {"<strike>": {"ce": {...}, "pe": {...}}}.
    Each leg's "greeks" sub-object has delta/theta/gamma/vega (no rho — Dhan doesn't
    provide it, so ce_rho/pe_rho stay 0.0)."""
    spot = float(data.get("last_price", 0) or 0)
    rows: list[OptionChainRow] = []
    for strike_str, legs in sorted(data.get("oc", {}).items(), key=lambda kv: float(kv[0])):
        ce = legs.get("ce", {}) or {}
        pe = legs.get("pe", {}) or {}
        ce_greeks = ce.get("greeks", {}) or {}
        pe_greeks = pe.get("greeks", {}) or {}
        rows.append(OptionChainRow(
            strike=float(strike_str),
            ce_oi=float(ce.get("oi", 0) or 0),
            ce_oi_change=float(ce.get("oi", 0) or 0) - float(ce.get("previous_oi", 0) or 0),
            ce_volume=float(ce.get("volume", 0) or 0),
            ce_iv=float(ce.get("implied_volatility", 0) or 0),
            ce_ltp=float(ce.get("last_price", 0) or 0),
            pe_oi=float(pe.get("oi", 0) or 0),
            pe_oi_change=float(pe.get("oi", 0) or 0) - float(pe.get("previous_oi", 0) or 0),
            pe_volume=float(pe.get("volume", 0) or 0),
            pe_iv=float(pe.get("implied_volatility", 0) or 0),
            pe_ltp=float(pe.get("last_price", 0) or 0),
            ce_delta=float(ce_greeks.get("delta", 0) or 0),
            ce_gamma=float(ce_greeks.get("gamma", 0) or 0),
            ce_theta=float(ce_greeks.get("theta", 0) or 0),
            ce_vega=float(ce_greeks.get("vega", 0) or 0),
            ce_prev_ltp=float(ce.get("previous_close_price", 0) or 0),
            pe_delta=float(pe_greeks.get("delta", 0) or 0),
            pe_gamma=float(pe_greeks.get("gamma", 0) or 0),
            pe_theta=float(pe_greeks.get("theta", 0) or 0),
            pe_vega=float(pe_greeks.get("vega", 0) or 0),
            pe_prev_ltp=float(pe.get("previous_close_price", 0) or 0),
        ))
    return OptionChainSnapshot(
        underlying=underlying, expiry=expiry,
        as_of=datetime.now(timezone.utc), spot_price=spot, rows=rows,
    )
