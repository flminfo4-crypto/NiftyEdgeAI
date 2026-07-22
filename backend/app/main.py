"""NiftyEdge backend - Story 0.

Endpoints:
  GET /api/health               liveness check
  GET /api/status               Dhan credential verification (green/red dot)
  GET /api/instruments/core     resolved security IDs for the 4 indices + VIX
  GET /api/instruments/search   search the instrument master
  WS  /ws                       internal relay (heartbeat for now)
"""
import asyncio
import logging

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from . import instruments, ws_relay
from .dhan_client import client

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("niftyedge")

app = FastAPI(title="NiftyEdge", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_core: dict = {}


@app.on_event("startup")
async def startup() -> None:
    global _core
    try:
        _core = await asyncio.to_thread(instruments.core_indices)
        log.info("Core instruments resolved: %s",
                 {k: v["security_id"] for k, v in _core.items()})
    except Exception as exc:  # noqa: BLE001
        log.error("Instrument master failed: %s", exc)
    asyncio.create_task(ws_relay.heartbeat_loop())


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/status")
async def status() -> dict:
    result = await asyncio.to_thread(client.verify)
    result["instruments_loaded"] = bool(_core)
    return result


@app.get("/api/instruments/core")
async def core() -> dict:
    return _core


@app.get("/api/instruments/search")
async def search(q: str, limit: int = 20) -> list[dict]:
    return await asyncio.to_thread(instruments.search, q, limit)


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await ws_relay.websocket_endpoint(websocket)
