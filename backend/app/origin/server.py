"""Origin server: the authoritative source of all content, served over HTTP/1.1.

Routes:
  GET  /content/<object_id>   file bytes (404 if unknown, 400 if the id is invalid)
  GET  /catalog               list of objects with size and content type
  GET  /_health               liveness probe
  GET  /stats                 counters
  POST /config                {"delay_ms": float}  configurable response delay
  POST /reset-counters

Run standalone:  python -m app.origin.server --port 9000 --content-dir content
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import mimetypes
import time
from collections import Counter
from pathlib import Path

from app.core.objects import safe_resolve, valid_object_id
from app.core.watchdog import improve_timer_resolution, watch_parent
from app.networking.http import HTTPError, HTTPServer, Request, Response

log = logging.getLogger("cdn.origin")


class OriginServer:
    def __init__(self, content_dir: str | Path, host: str = "127.0.0.1", port: int = 9000, delay_ms: float = 25.0):
        self.root = Path(content_dir).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"content directory {self.root} does not exist")
        self.delay_ms = delay_ms
        self.http = HTTPServer(self.handle, host, port, "origin")
        self.started_at = time.time()
        self.reset_counters()

    @property
    def port(self) -> int:
        return self.http.port

    def reset_counters(self) -> None:
        self.requests_total = self.content_requests = self.bytes_served = 0
        self.not_found = self.bad_requests = 0
        self.fetches_by_edge: Counter[str] = Counter()

    def catalog(self) -> list[dict]:
        items = []
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                oid = p.relative_to(self.root).as_posix()
                if valid_object_id(oid):
                    items.append({"object_id": oid, "size": p.stat().st_size,
                                  "content_type": mimetypes.guess_type(p.name)[0] or "application/octet-stream"})
        return items

    async def start(self) -> None:
        await self.http.start()

    async def stop(self) -> None:
        await self.http.stop()

    async def handle(self, req: Request) -> Response:
        self.requests_total += 1
        if req.path == "/_health":
            return Response.json({"status": "ok", "role": "origin"})
        if req.path == "/catalog" and req.method == "GET":
            return Response.json({"objects": self.catalog()})
        if req.path == "/stats" and req.method == "GET":
            return Response.json(self.stats())
        if req.path == "/config" and req.method == "POST":
            body = req.json()
            delay = body.get("delay_ms")
            if not isinstance(delay, (int, float)) or not 0 <= delay <= 5000:
                raise HTTPError(400, "delay_ms must be a number within 0..5000")
            self.delay_ms = float(delay)
            return Response.json({"delay_ms": self.delay_ms})
        if req.path == "/reset-counters" and req.method == "POST":
            self.reset_counters()
            return Response.json({"ok": True})
        if req.path.startswith("/content/"):
            if req.method not in ("GET", "HEAD"):
                raise HTTPError(405)
            return await self._content(req)
        raise HTTPError(404, "no such route")

    async def _content(self, req: Request) -> Response:
        self.content_requests += 1
        oid = req.path[len("/content/"):]
        if not valid_object_id(oid):
            self.bad_requests += 1
            raise HTTPError(400, "invalid object id")
        path = safe_resolve(self.root, oid)
        if path is None:
            self.not_found += 1
            raise HTTPError(404, f"object {oid!r} not found")
        data = await asyncio.to_thread(path.read_bytes)
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)
        self.bytes_served += len(data)
        self.fetches_by_edge[req.headers.get("x-edge-id", "direct")] += 1
        return Response(200, data, {"ETag": '"' + hashlib.sha256(data).hexdigest()[:16] + '"',
                                    "X-Origin": "origin"},
                        mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def stats(self) -> dict:
        return {"role": "origin", "delay_ms": self.delay_ms, "requests_total": self.requests_total,
                "content_requests": self.content_requests, "bytes_served": self.bytes_served,
                "not_found": self.not_found, "bad_requests": self.bad_requests,
                "fetches_by_edge": dict(self.fetches_by_edge), "uptime_s": round(time.time() - self.started_at, 1)}


async def _main(args: argparse.Namespace) -> None:
    improve_timer_resolution()
    origin = OriginServer(args.content_dir, args.host, args.port, args.delay_ms)
    await origin.start()
    if args.parent_pid:
        asyncio.create_task(watch_parent(args.parent_pid))
    await asyncio.Event().wait()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CDN origin server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--content-dir", default=str(Path(__file__).resolve().parents[2] / "content"))
    ap.add_argument("--delay-ms", type=float, default=25.0)
    ap.add_argument("--parent-pid", type=int, default=0)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(_main(ap.parse_args()))
    except KeyboardInterrupt:
        pass
