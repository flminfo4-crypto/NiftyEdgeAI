"""Thin wrapper around the official dhanhq SDK.

Story 0 scope: verify the token works. Later stories add the
live market feed and option-chain polling here.
"""
import logging
from dhanhq import dhanhq

from . import config

log = logging.getLogger("niftyedge.dhan")


class DhanClient:
    def __init__(self) -> None:
        self._sdk = None

    @property
    def sdk(self):
        if self._sdk is None:
            self._sdk = dhanhq(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN)
        return self._sdk

    def verify(self) -> dict:
        """Check credentials by calling a harmless authenticated endpoint.

        Returns {"connected": bool, "detail": str}. Never raises.
        """
        if not config.credentials_present():
            return {
                "connected": False,
                "detail": "DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing. "
                          "Copy .env.example to .env and fill both values.",
            }
        try:
            resp = self.sdk.get_fund_limits()
            if isinstance(resp, dict) and resp.get("status") == "failure":
                msg = str(resp.get("remarks") or resp.get("data") or resp)
                if "token" in msg.lower() or "auth" in msg.lower() or "801" in msg:
                    msg = ("Token rejected or expired. Generate a new access token "
                           "at web.dhan.co -> DhanHQ Trading APIs, then update .env "
                           "and restart. (" + msg + ")")
                return {"connected": False, "detail": msg}
            return {"connected": True, "detail": "API connected"}
        except Exception as exc:  # noqa: BLE001 - report, never crash the app
            log.warning("Dhan verify failed: %s", exc)
            return {"connected": False, "detail": f"Could not reach Dhan: {exc}"}


client = DhanClient()
