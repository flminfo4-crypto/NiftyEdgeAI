"""
Selects and holds the single active broker-plugins adapter for the process,
per `config.settings.broker_adapter`. Every other service imports
`get_broker()` rather than instantiating an adapter itself, so switching
from mock to a real broker is a one-line config change (see config.py).
"""

from functools import lru_cache

from broker_plugins.core.interface import BrokerAdapter

from app.config import settings


@lru_cache(maxsize=1)
def get_broker() -> BrokerAdapter:
    if settings.broker_adapter == "mock":
        from broker_plugins.mock import MockBrokerAdapter

        adapter = MockBrokerAdapter()
    elif settings.broker_adapter == "dhan":
        from broker_plugins.dhan import DhanBrokerAdapter

        adapter = DhanBrokerAdapter()
    else:
        # fyers / angel_one still pending — see broker-plugins/README.md.
        raise NotImplementedError(
            f"Broker adapter '{settings.broker_adapter}' is not implemented yet. "
            "Set BROKER_ADAPTER=mock or dhan."
        )
    adapter.connect({})
    return adapter
