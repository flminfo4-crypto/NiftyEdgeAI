"""Internal WebSocket relay.

Story 0: skeleton only - manages browser connections and broadcasts a
heartbeat every 5 seconds. Story 1 will push live ticks through the
same broadcast() call.
"""
import asyncio
import datetime as dt
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("niftyedge.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        log.info("Browser connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def heartbeat_loop() -> None:
    while True:
        await manager.broadcast({
            "type": "heartbeat",
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
        })
        await asyncio.sleep(5)


async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # ignore client messages for now
    except WebSocketDisconnect:
        manager.disconnect(ws)
