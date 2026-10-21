"""Edge server: an independent HTTP cache in front of the origin.

Each edge runs two TCP listeners:
  data port   GET /content/<object_id>, GET /_health   (client-facing; closed on simulated crash)
  admin port  stats / cache / profile / failure controls used by the control plane

Response headers on /content:
  X-Cache            HIT | MISS | COALESCED | BYPASS
  X-Edge-Id          id of the edge that served the request
  X-Edge-Service-Ms  measured time inside the edge (queueing + cache/origin + simulated transmission)
  X-Sim-Delay-Ms     simulated delay that was injected (propagation + transmission + loss penalty)
  X-Sim-Lost-Segments simulated lost segments for this response

Run standalone:  python -m app.edge.server --edge-id india-edge --data-port 9001 --admin-port 9101
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import time
from collections import deque

import psutil

from app.caching.edge_cache import CACHE_POLICIES, CachedObject, EdgeCache
from app.core.objects import valid_object_id
from app.core.watchdog import improve_timer_resolution, watch_parent
from app.edge.profile import NetworkProfile
from app.networking.http import HTTPClientError, HTTPError, HTTPServer, Request, Response, http_request

log = logging.getLogger("cdn.edge")


class OriginNotFound(Exception):
    pass


class OriginUnavailable(Exception):
    pass


class EdgeServer:
    def __init__(self, edge_id: str, *, host: str = "127.0.0.1", data_port: int = 0, admin_port: int = 0,
                 origin_host: str = "127.0.0.1", origin_port: int = 9000,
                 profile: NetworkProfile | None = None, cache_policy: str = "lru",
                 cache_capacity_bytes: int = 8 * 1024 * 1024, cache_ttl_seconds: float | None = 30.0,
                 concurrency_limit: int = 16, seed: int | None = None, shared_process: bool = False):
        self.edge_id = edge_id
        self.origin = (origin_host, origin_port)
        self.profile = (profile or NetworkProfile()).validate()
        self.cache = EdgeCache(cache_policy, cache_capacity_bytes, cache_ttl_seconds)
        self.concurrency_limit = concurrency_limit
        self._slots = asyncio.Semaphore(concurrency_limit)
        self.rng = random.Random(seed)
        self.data = HTTPServer(self.handle_data, host, data_port, f"edge[{edge_id}].data", max_body=0)
        self.admin = HTTPServer(self.handle_admin, host, admin_port, f"edge[{edge_id}].admin")
        self.failed = False
        self.shared_process = shared_process
        self._proc = psutil.Process(os.getpid())
        self._proc.cpu_percent(None)
        self.started_at = time.time()
        self.service_ms: deque[float] = deque(maxlen=500)
        self.reset_counters()

    def reset_counters(self) -> None:
        self.active = self.queued = 0
        self.requests = self.responses_ok = self.errors_4xx = self.errors_5xx = 0
        self.bytes_served = self.sim_segments = self.sim_lost_segments = 0
        self.cache.reset_counters()
        self.service_ms.clear()

    async def start(self) -> None:
        await self.admin.start()
        await self.data.start()

    async def stop(self) -> None:
        await self.data.stop()
        await self.admin.stop()

    # ------------------------------------------------------------------ data plane
    async def handle_data(self, req: Request) -> Response:
        if req.path == "/_health":
            await asyncio.sleep(self.profile.propagation_s(self.rng))
            return Response.json({"status": "ok", "edge_id": self.edge_id})
        if not req.path.startswith("/content/"):
            raise HTTPError(404, "no such route")
        if req.method not in ("GET", "HEAD"):
            raise HTTPError(405)
        self.requests += 1
        oid = req.path[len("/content/"):]
        if not valid_object_id(oid):
            self.errors_4xx += 1
            raise HTTPError(400, "invalid object id")
        t0 = time.perf_counter()
        self.queued += 1
        async with self._slots:
            self.queued -= 1
            self.active += 1
            try:
                try:
                    obj, status = await self.cache.get_or_fetch(oid, self._fetch_origin)
                except OriginNotFound:
                    self.errors_4xx += 1
                    return Response.error(404, f"object {oid!r} not found at origin")
                except OriginUnavailable as exc:
                    self.errors_5xx += 1
                    return Response.error(502, str(exc))
                segs, lost, penalty = self.profile.loss(len(obj.value), self.rng)
                tx = self.profile.transmission_s(len(obj.value)) + penalty
                await asyncio.sleep(tx)  # the slot stays busy while "transmitting"
            finally:
                self.active -= 1
        service_ms = (time.perf_counter() - t0) * 1000
        prop = self.profile.propagation_s(self.rng)
        await asyncio.sleep(prop)
        self.sim_segments += segs
        self.sim_lost_segments += lost
        self.responses_ok += 1
        self.bytes_served += len(obj.value)
        self.service_ms.append(service_ms)
        return Response(200, obj.value, {
            "X-Cache": status, "X-Edge-Id": self.edge_id, "ETag": obj.etag,
            "X-Edge-Service-Ms": f"{service_ms:.2f}", "X-Sim-Delay-Ms": f"{(tx + prop) * 1000:.2f}",
            "X-Sim-Lost-Segments": str(lost), "Cache-Control": "public, max-age=60",
        }, obj.content_type)

    async def _fetch_origin(self, oid: str) -> CachedObject:
        try:
            r = await http_request(*self.origin, "GET", f"/content/{oid}",
                                   headers={"X-Edge-Id": self.edge_id}, timeout=10.0)
        except HTTPClientError as exc:
            raise OriginUnavailable(f"origin unreachable: {exc}") from exc
        if r.status == 404:
            raise OriginNotFound(oid)
        if r.status != 200:
            raise OriginUnavailable(f"origin returned HTTP {r.status}")
        return CachedObject(oid, r.body, r.headers.get("content-type", "application/octet-stream"),
                            r.headers.get("etag", ""))

    # ------------------------------------------------------------------ admin plane
    async def handle_admin(self, req: Request) -> Response:
        p, m = req.path, req.method
        if p == "/stats" and m == "GET":
            return Response.json(self.stats())
        if p == "/cache" and m == "GET":
            return Response.json({"edge_id": self.edge_id, "stats": self.cache.stats(),
                                  "entries": self.cache.entries(),
                                  "evictions": [{"object_id": e.key, "size": e.size, "reason": e.reason, "at": e.at}
                                                for e in reversed(self.cache.evictions)]})
        if m != "POST":
            raise HTTPError(404 if m == "GET" else 405)
        body = req.json()
        if p == "/cache/clear":
            return Response.json({"cleared": self.cache.clear()})
        if p == "/cache/invalidate":
            oid = body.get("object_id", "")
            if not isinstance(oid, str) or not valid_object_id(oid):
                raise HTTPError(400, "invalid object id")
            return Response.json({"invalidated": self.cache.invalidate(oid)})
        if p == "/cache/config":
            policy = body.get("policy", self.cache.policy)
            cap = body.get("capacity_bytes", self.cache.capacity)
            ttl = body.get("ttl_seconds", self.cache.ttl)
            if policy not in CACHE_POLICIES:
                raise HTTPError(400, f"policy must be one of {CACHE_POLICIES}")
            if not isinstance(cap, int) or not 1024 <= cap <= 1024 ** 3:
                raise HTTPError(400, "capacity_bytes must be an integer within 1 KiB..1 GiB")
            if ttl is not None and (not isinstance(ttl, (int, float)) or not 0.1 <= ttl <= 86400):
                raise HTTPError(400, "ttl_seconds must be within 0.1..86400")
            self.cache.configure(policy, cap, ttl)
            return Response.json(self.cache.stats())
        if p == "/cache/warm":
            ids = body.get("object_ids", [])
            if not isinstance(ids, list) or len(ids) > 500 or not all(isinstance(i, str) and valid_object_id(i) for i in ids):
                raise HTTPError(400, "object_ids must be a list of valid ids (max 500)")
            warmed = 0
            for oid in ids:
                try:
                    warmed += self.cache.warm(oid, await self._fetch_origin(oid))
                except (OriginNotFound, OriginUnavailable):
                    pass
            return Response.json({"warmed": warmed})
        if p == "/profile":
            try:
                merged = {**self.profile.to_dict(), **{k: float(v) for k, v in body.items()
                                                      if k in NetworkProfile.__dataclass_fields__}}
                self.profile = NetworkProfile(**merged).validate()
            except (TypeError, ValueError) as exc:
                raise HTTPError(400, str(exc)) from exc
            if "concurrency_limit" in body:
                lim = body["concurrency_limit"]
                if not isinstance(lim, int) or not 1 <= lim <= 1024:
                    raise HTTPError(400, "concurrency_limit must be within 1..1024")
                self.concurrency_limit = lim
                self._slots = asyncio.Semaphore(lim)
            return Response.json({"profile": self.profile.to_dict(), "concurrency_limit": self.concurrency_limit})
        if p == "/fail":
            if not self.failed:
                self.failed = True
                await self.data.stop()
                log.warning("edge %s: simulated crash (data listener closed)", self.edge_id)
            return Response.json({"failed": True})
        if p == "/restore":
            if self.failed:
                await self.data.start()
                self.failed = False
                log.warning("edge %s: restored", self.edge_id)
            return Response.json({"failed": False})
        if p == "/reset-counters":
            self.reset_counters()
            return Response.json({"ok": True})
        raise HTTPError(404, "no such route")

    def stats(self) -> dict:
        try:
            cpu, rss = self._proc.cpu_percent(None), self._proc.memory_info().rss
        except psutil.Error:
            cpu = rss = None
        svc = sorted(self.service_ms)
        return {
            "edge_id": self.edge_id, "pid": os.getpid(), "shared_process": self.shared_process,
            "failed": self.failed, "data_port": self.data.port, "admin_port": self.admin.port,
            "profile": self.profile.to_dict(), "concurrency_limit": self.concurrency_limit,
            "active_requests": self.active, "queued_requests": self.queued,
            "requests": self.requests, "responses_ok": self.responses_ok,
            "errors_4xx": self.errors_4xx, "errors_5xx": self.errors_5xx, "bytes_served": self.bytes_served,
            "sim_segments": self.sim_segments, "sim_lost_segments": self.sim_lost_segments,
            "service_ms_p50": svc[len(svc) // 2] if svc else None,
            "cpu_percent": cpu, "memory_rss_bytes": rss, "uptime_s": round(time.time() - self.started_at, 1),
            "cache": self.cache.stats(),
        }


async def _main(args: argparse.Namespace) -> None:
    improve_timer_resolution()
    cfg = json.loads(args.config) if args.config else {}
    edge = EdgeServer(
        args.edge_id, host=args.host, data_port=args.data_port, admin_port=args.admin_port,
        origin_host=args.origin_host, origin_port=args.origin_port,
        profile=NetworkProfile(**cfg.get("profile", {})), cache_policy=cfg.get("cache_policy", "lru"),
        cache_capacity_bytes=cfg.get("cache_capacity_bytes", 8 * 1024 * 1024),
        cache_ttl_seconds=cfg.get("cache_ttl_seconds", 30.0),
        concurrency_limit=cfg.get("concurrency_limit", 16), seed=cfg.get("seed"))
    await edge.start()
    if args.parent_pid:
        asyncio.create_task(watch_parent(args.parent_pid))
    await asyncio.Event().wait()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CDN edge server")
    ap.add_argument("--edge-id", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-port", type=int, required=True)
    ap.add_argument("--admin-port", type=int, required=True)
    ap.add_argument("--origin-host", default="127.0.0.1")
    ap.add_argument("--origin-port", type=int, default=9000)
    ap.add_argument("--config", default="", help="JSON: profile, cache_policy, cache_capacity_bytes, ...")
    ap.add_argument("--parent-pid", type=int, default=0)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(_main(ap.parse_args()))
    except KeyboardInterrupt:
        pass
