"""Control-plane view of the edge fleet: health checking, stats polling and admin calls.

Health model
  * Active probes: GET /_health on each edge's DATA port every probe_interval_s.
    fail_threshold consecutive failures   -> unhealthy
    recover_threshold consecutive successes -> healthy
  * Passive signal: 3 consecutive client transport failures (refused/reset/timeout) on an edge
    mark it unhealthy immediately, without waiting for the next probe.
  * Detection time = unhealthy transition time - failure injection time (when injected via the API).
    Recovery time  = healthy transition time - restore time.

Routing inputs (EdgeView)
  latency_ms   EWMA (alpha 0.3) of probe RTTs — measured
  load         max(edge active+queued, client in-flight) / concurrency_limit — measured
  packet_loss  EWMA of per-interval lost/sent segment ratio reported by the edge — estimated
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.networking.http import HTTPClientError, http_request
from app.routing.strategies import EdgeView

log = logging.getLogger("cdn.cluster")

EWMA_ALPHA = 0.3
PASSIVE_THRESHOLD = 3

Emit = Callable[[str, str | None, dict], None]


class ClusterError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass
class EdgeRuntime:
    cfg: dict
    status: str = "unknown"  # unknown | healthy | unhealthy
    consecutive_ok: int = 0
    consecutive_fail: int = 0
    passive_fail: int = 0
    latency_ewma: float | None = None
    last_probe_ms: float | None = None
    last_probe_at: float | None = None
    last_error: str | None = None
    loss_ewma: float | None = None
    stats: dict | None = None
    stats_at: float | None = None
    local_inflight: int = 0
    failure_injected_at: float | None = None
    restore_requested_at: float | None = None
    transitions: list[dict] = field(default_factory=list)
    _seg_base: tuple[int, int] | None = None

    @property
    def edge_id(self) -> str:
        return self.cfg["edge_id"]

    @property
    def concurrency_limit(self) -> int:
        return (self.stats or {}).get("concurrency_limit") or self.cfg.get("concurrency_limit", 16)

    @property
    def load(self) -> float:
        reported = 0
        if self.stats and self.status == "healthy":
            reported = self.stats.get("active_requests", 0) + self.stats.get("queued_requests", 0)
        return max(reported, self.local_inflight) / max(1, self.concurrency_limit)


class Cluster:
    def __init__(self, topology: dict, emit: Emit | None = None, *, probe_interval_s: float = 1.0,
                 probe_timeout_s: float = 1.5, stats_interval_s: float = 1.0, fail_threshold: int = 2,
                 recover_threshold: int = 2):
        self.topology = topology
        self.origin_cfg = topology["origin"]
        self.edges: dict[str, EdgeRuntime] = {e["edge_id"]: EdgeRuntime(dict(e)) for e in topology["edges"]}
        self.emit = emit or (lambda *a: None)
        self.probe_interval_s, self.probe_timeout_s = probe_interval_s, probe_timeout_s
        self.stats_interval_s = stats_interval_s
        self.fail_threshold, self.recover_threshold = fail_threshold, recover_threshold
        self.origin_status = "unknown"
        self.origin_stats: dict | None = None
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await asyncio.gather(*(self._probe(e, initial=True) for e in self.edges.values()), self._poll_origin())
        await asyncio.gather(*(self._poll_stats(e) for e in self.edges.values()))
        self._tasks = [asyncio.create_task(self._probe_loop()), asyncio.create_task(self._stats_loop())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def get(self, edge_id: str) -> EdgeRuntime:
        try:
            return self.edges[edge_id]
        except KeyError:
            raise ClusterError(f"unknown edge {edge_id!r}", 404) from None

    # ------------------------------------------------------------------ health
    async def _probe_loop(self) -> None:
        while True:
            await asyncio.sleep(self.probe_interval_s)
            await asyncio.gather(*(self._probe(e) for e in self.edges.values()), self._poll_origin(),
                                 return_exceptions=True)

    async def _probe(self, e: EdgeRuntime, initial: bool = False) -> None:
        try:
            r = await http_request(e.cfg["host"], e.cfg["data_port"], "GET", "/_health", timeout=self.probe_timeout_s)
            ok = r.status == 200
            err = None if ok else f"HTTP {r.status}"
            rtt = r.elapsed_ms
        except HTTPClientError as exc:
            ok, err, rtt = False, str(exc), None
        e.last_probe_at = time.time()
        if ok:
            e.consecutive_ok += 1
            e.consecutive_fail = 0
            e.last_probe_ms = rtt
            e.latency_ewma = rtt if e.latency_ewma is None else EWMA_ALPHA * rtt + (1 - EWMA_ALPHA) * e.latency_ewma
            e.last_error = None
            if e.status != "healthy" and (initial or e.status == "unknown" or e.consecutive_ok >= self.recover_threshold):
                self._transition(e, "healthy", "active probe succeeded")
        else:
            e.consecutive_fail += 1
            e.consecutive_ok = 0
            e.last_error = err
            if e.status != "unhealthy" and (initial or e.status == "unknown" or e.consecutive_fail >= self.fail_threshold):
                self._transition(e, "unhealthy", f"{e.consecutive_fail} failed probe(s): {err}")

    def _transition(self, e: EdgeRuntime, status: str, reason: str) -> None:
        prev, now = e.status, time.time()
        e.status = status
        detail: dict[str, Any] = {"from": prev, "to": status, "reason": reason}
        if status == "unhealthy" and e.failure_injected_at:
            detail["detection_ms"] = round((now - e.failure_injected_at) * 1000, 1)
        if status == "healthy" and e.restore_requested_at:
            detail["recovery_ms"] = round((now - e.restore_requested_at) * 1000, 1)
            e.restore_requested_at = None
            e.failure_injected_at = None
        if status == "healthy":
            e.passive_fail = 0
        e.transitions.append({"at": now, **detail})
        del e.transitions[:-50]
        log.info("edge %s %s -> %s (%s)", e.edge_id, prev, status, reason)
        self.emit("health_transition", e.edge_id, detail)

    def report_client_result(self, edge_id: str, transport_failure: bool) -> None:
        e = self.edges.get(edge_id)
        if not e:
            return
        if not transport_failure:
            e.passive_fail = 0
            return
        e.passive_fail += 1
        if e.status == "healthy" and e.passive_fail >= PASSIVE_THRESHOLD:
            self._transition(e, "unhealthy", f"passive: {e.passive_fail} consecutive client transport failures")

    def inflight(self, edge_id: str, delta: int) -> None:
        if edge_id in self.edges:
            self.edges[edge_id].local_inflight = max(0, self.edges[edge_id].local_inflight + delta)

    # ------------------------------------------------------------------ stats
    async def _stats_loop(self) -> None:
        while True:
            await asyncio.sleep(self.stats_interval_s)
            await asyncio.gather(*(self._poll_stats(e) for e in self.edges.values()), return_exceptions=True)

    async def _poll_stats(self, e: EdgeRuntime) -> None:
        try:
            r = await http_request(e.cfg["host"], e.cfg["admin_port"], "GET", "/stats", timeout=1.0)
            if r.status != 200:
                return
            s = r.json()
        except (HTTPClientError, ValueError):
            return
        segs, lost = s.get("sim_segments", 0), s.get("sim_lost_segments", 0)
        if e._seg_base is not None and segs >= e._seg_base[0]:
            d_seg, d_lost = segs - e._seg_base[0], lost - e._seg_base[1]
            if d_seg > 0:
                sample = d_lost / d_seg
                e.loss_ewma = sample if e.loss_ewma is None else EWMA_ALPHA * sample + (1 - EWMA_ALPHA) * e.loss_ewma
        e._seg_base = (segs, lost)
        e.stats, e.stats_at = s, time.time()

    async def _poll_origin(self) -> None:
        o = self.origin_cfg
        try:
            r = await http_request(o["host"], o["port"], "GET", "/stats", timeout=self.probe_timeout_s)
            new = "healthy" if r.status == 200 else "unhealthy"
            if r.status == 200:
                self.origin_stats = r.json()
        except (HTTPClientError, ValueError):
            new = "unhealthy"
        if new != self.origin_status and self.origin_status != "unknown":
            self.emit("origin_health", None, {"from": self.origin_status, "to": new})
        self.origin_status = new

    async def refresh_stats(self) -> None:
        await asyncio.gather(*(self._poll_stats(e) for e in self.edges.values()), self._poll_origin())

    # ------------------------------------------------------------------ admin
    async def admin(self, edge_id: str, method: str, path: str, body: Any = None, timeout: float = 5.0) -> Any:
        e = self.get(edge_id)
        try:
            r = await http_request(e.cfg["host"], e.cfg["admin_port"], method, path, json_body=body, timeout=timeout)
        except HTTPClientError as exc:
            raise ClusterError(f"edge {edge_id} admin unreachable: {exc}") from exc
        data = r.json()
        if r.status >= 400:
            raise ClusterError((data or {}).get("detail", f"HTTP {r.status}"), 400 if r.status < 500 else 502)
        return data

    async def admin_all(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        ids = list(self.edges)
        res = await asyncio.gather(*(self.admin(i, method, path, body) for i in ids), return_exceptions=True)
        errors = [f"{i}: {r}" for i, r in zip(ids, res) if isinstance(r, Exception)]
        if errors:
            raise ClusterError("; ".join(errors))
        return dict(zip(ids, res))

    async def origin_admin(self, method: str, path: str, body: Any = None) -> Any:
        o = self.origin_cfg
        try:
            r = await http_request(o["host"], o["port"], method, path, json_body=body, timeout=5.0)
        except HTTPClientError as exc:
            raise ClusterError(f"origin unreachable: {exc}") from exc
        if r.status >= 400:
            raise ClusterError((r.json() or {}).get("detail", f"HTTP {r.status}"), 400 if r.status < 500 else 502)
        return r.json()

    async def fail_edge(self, edge_id: str) -> None:
        e = self.get(edge_id)
        e.failure_injected_at = time.time()
        await self.admin(edge_id, "POST", "/fail")
        self.emit("failure_injected", edge_id, {"at": e.failure_injected_at})

    async def restore_edge(self, edge_id: str) -> None:
        e = self.get(edge_id)
        e.restore_requested_at = time.time()
        await self.admin(edge_id, "POST", "/restore")
        self.emit("restore_requested", edge_id, {"at": e.restore_requested_at})

    # ------------------------------------------------------------------ views
    def views(self) -> list[EdgeView]:
        return [EdgeView(edge_id=e.edge_id, host=e.cfg["host"], port=e.cfg["data_port"],
                         healthy=e.status == "healthy", latency_ms=e.latency_ewma, load=e.load,
                         packet_loss=e.loss_ewma, distance_km=e.cfg.get("distance_km"),
                         priority=e.cfg.get("priority", 100)) for e in self.edges.values()]

    def describe(self, edge_id: str) -> dict:
        e = self.get(edge_id)
        s = e.stats or {}
        cache = s.get("cache", {})
        return {
            "edge_id": e.edge_id, "name": e.cfg.get("name", e.edge_id), "location": e.cfg.get("location"),
            "location_is_simulated": True, "host": e.cfg["host"], "data_port": e.cfg["data_port"],
            "admin_port": e.cfg["admin_port"], "distance_km": e.cfg.get("distance_km"),
            "priority": e.cfg.get("priority"), "status": e.status, "failed_simulated": s.get("failed", False),
            "last_error": e.last_error, "last_probe_at": e.last_probe_at,
            "latency": {"probe_rtt_ewma_ms": _r(e.latency_ewma), "last_probe_rtt_ms": _r(e.last_probe_ms),
                        "kind": "measured", "simulated_base_ms": s.get("profile", e.cfg.get("profile", {})).get("base_latency_ms")},
            "load": {"value": round(e.load, 4), "active_requests": s.get("active_requests"),
                     "queued_requests": s.get("queued_requests"), "client_inflight": e.local_inflight,
                     "concurrency_limit": e.concurrency_limit},
            "packet_loss": {"estimated": _r(e.loss_ewma, 5), "configured": s.get("profile", e.cfg.get("profile", {})).get("packet_loss")},
            "profile": s.get("profile", e.cfg.get("profile")), "profile_kind": "simulated",
            "requests": s.get("requests"), "responses_ok": s.get("responses_ok"), "bytes_served": s.get("bytes_served"),
            "errors_4xx": s.get("errors_4xx"), "errors_5xx": s.get("errors_5xx"),
            "cpu_percent": s.get("cpu_percent"), "memory_rss_bytes": s.get("memory_rss_bytes"),
            "pid": s.get("pid"), "shared_process": s.get("shared_process"),
            "cache": cache, "stats_age_s": _r(time.time() - e.stats_at, 1) if e.stats_at else None,
            "transitions": e.transitions[-10:],
        }


def _r(v: float | None, d: int = 2) -> float | None:
    return None if v is None else round(v, d)
