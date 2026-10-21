"""WebSocket /ws/metrics — live push channel for the dashboard.

Message schema (JSON text frames, server -> client):
  {"type": "hello",    "ts": <epoch>, "interval_s": 1.0}
  {"type": "snapshot", "ts": <epoch>, "data": <Services.snapshot()>, "point": <latest 1 s timeseries bucket|null>}
  {"type": "event",    "ts": <epoch>, "event": {"ts", "kind", "edge_id", "detail"}}
Snapshots are sent once per second; events are pushed as they happen. Each connection has a
bounded queue (events are dropped, never buffered without limit, if a client is too slow).
Client -> server messages are ignored except "ping", which is answered with {"type": "pong"}.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("cdn.ws")
router = APIRouter()
INTERVAL_S = 1.0


@router.websocket("/ws/metrics")
async def metrics_ws(ws: WebSocket) -> None:
    services = ws.app.state.services
    await ws.accept()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)

    def on_event(ev: dict) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait({"type": "event", "ts": time.time(), "event": ev})

    services.subscribe(on_event)

    async def pump_events() -> None:
        while True:
            await ws.send_text(json.dumps(await queue.get(), default=str))

    async def pump_snapshots() -> None:
        while True:
            pts = services.live.timeseries(window_s=3, bucket_s=1)
            await ws.send_text(json.dumps({"type": "snapshot", "ts": time.time(), "data": services.snapshot(),
                                           "point": pts[-2] if len(pts) >= 2 else None}, default=str))
            await asyncio.sleep(INTERVAL_S)

    async def receive() -> None:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(json.dumps({"type": "pong", "ts": time.time()}))

    await ws.send_text(json.dumps({"type": "hello", "ts": time.time(), "interval_s": INTERVAL_S}))
    tasks = [asyncio.create_task(c) for c in (pump_events(), pump_snapshots(), receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, RuntimeError, ConnectionError)):
                log.warning("websocket closed with error: %r", exc)
    finally:
        services.unsubscribe(on_event)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await ws.close()
