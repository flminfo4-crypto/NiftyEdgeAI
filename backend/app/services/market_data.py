"""
Market data gateway (Business Service Layer). Normalizes calls to the
active broker adapter into the shapes the API layer returns. This is
also the natural place to add Redis caching later (see backend/README.md) —
callers don't need to change when that lands.
"""

from datetime import datetime

from app.services.broker import get_broker


def get_quotes(symbols: list[str]):
    return get_broker().get_quote(symbols)


def get_option_chain(underlying: str, expiry: str):
    return get_broker().get_option_chain(underlying, expiry)


def get_candles(symbol: str, interval: str, frm: datetime, to: datetime):
    return get_broker().get_historical_candles(symbol, interval, frm, to)
