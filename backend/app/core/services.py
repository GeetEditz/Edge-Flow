"""Composition root for the control plane: owns every long-lived component."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from collections import deque

from app.config import CDN_HOSTNAME, Settings
from app.core.cluster import Cluster
from app.core.launcher import Launcher
from app.core.metrics import LiveMetrics
from app.experiments.runner import ExperimentRunner
from app.networking.http import HTTPClientError, http_request
from app.origin import content_gen
from app.networking.resolver import (RCODE_NXDOMAIN, RCODE_OK, RCODE_SERVFAIL, Answer, ResolverClient,
                                     ResolverServer)
from app.persistence.db import Database
from app.routing.engine import RoutingEngine

log = logging.getLogger("cdn.services")


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        topo = settings.topology
        self.db = Database(settings.db_path)
        self.events: deque[dict] = deque(maxlen=500)
        self._listeners: list = []
        persisted_routing = self.db.get_setting("routing", topo["routing"])
        self._cache_cfg = self.db.get_setting("cache", topo["cache"])
        self.routing = RoutingEngine(persisted_routing["strategy"], seed=None, weights=persisted_routing.get("weights"))
        self.cluster = Cluster(topo, self.emit, probe_interval_s=settings.probe_interval_s,
                               probe_timeout_s=settings.probe_timeout_s, stats_interval_s=settings.stats_interval_s,
                               fail_threshold=settings.fail_threshold, recover_threshold=settings.recover_threshold)
        self.launcher = Launcher(settings.launch, topo, settings.content_dir,
                                 settings.db_path.parent / "logs", self._cache_cfg)
        self.resolver = ResolverServer(self.resolve, settings.resolver_host, settings.resolver_port)
        self.resolver_client = ResolverClient(settings.resolver_host, settings.resolver_port, timeout=1.0)
        self.live = LiveMetrics()
        self.runner = ExperimentRunner(self)
        self._catalogue: tuple[float, list[dict]] | None = None
        self._tasks: list[asyncio.Task] = []
        self.started_at = time.time()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.settings.launch != "external":
            await asyncio.to_thread(content_gen.ensure, self.settings.content_dir)
        await self.launcher.start()
        await self.cluster.start()
        await self.resolver.start()
        if self.resolver_client.port != self.resolver.port:
            self.resolver_client.port = self.resolver.port
        await self.resolver_client.open()
        self._tasks.append(asyncio.create_task(self._snapshot_loop()))
        self.emit("control_plane_started", None, {"mode": self.settings.mode, "launch": self.settings.launch})

    async def stop(self) -> None:
        await self.runner.shutdown()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.resolver_client.close()
        self.resolver.stop()
        await self.cluster.stop()
        await self.launcher.stop()
        self.db.close()

    # ------------------------------------------------------------------ events
    def subscribe(self, fn) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def emit(self, kind: str, edge_id: str | None, detail: dict) -> None:
        ev = {"ts": time.time(), "kind": kind, "edge_id": edge_id, "detail": detail}
        self.events.append(ev)
        try:
            self.db.insert_event(ev["ts"], kind, edge_id, detail)
        except Exception:  # noqa: BLE001 - events are best effort
            log.exception("failed to persist event")
        for fn in list(self._listeners):
            fn(ev)

    # ------------------------------------------------------------------ resolver
    def resolve(self, hostname: str, explain: bool) -> Answer:
        """CNRP resolution: cdn.sim.local -> routed edge; <edge-prefix>.cdn.sim.local -> that edge."""
        ttl = self.settings.resolver_ttl_s
        if hostname == CDN_HOSTNAME:
            d = self.routing.select(self.cluster.views())
            if d.edge_id is None:
                return Answer(0, RCODE_SERVFAIL, explanation=d.reason)
            e = self.cluster.edges[d.edge_id].cfg
            return Answer(0, RCODE_OK, ttl, _ipv4(e["host"]), e["data_port"], d.edge_id, f"[{d.strategy}] {d.reason}")
        if hostname.endswith("." + CDN_HOSTNAME):
            label = hostname[: -len(CDN_HOSTNAME) - 1]
            for e in self.cluster.edges.values():
                if label in (e.edge_id, e.edge_id.removesuffix("-edge")):
                    if e.status != "healthy":
                        return Answer(0, RCODE_SERVFAIL, explanation=f"pinned edge {e.edge_id} is {e.status}")
                    return Answer(0, RCODE_OK, ttl, _ipv4(e.cfg["host"]), e.cfg["data_port"], e.edge_id,
                                  "explicitly pinned edge hostname")
        return Answer(0, RCODE_NXDOMAIN, explanation=f"unknown hostname {hostname}")

    # ------------------------------------------------------------------ helpers
    def cache_config(self) -> dict:
        return dict(self._cache_cfg)

    def set_cache_config(self, cfg: dict) -> None:
        self._cache_cfg = {"policy": cfg["policy"], "capacity_bytes": cfg["capacity_bytes"],
                           "ttl_seconds": cfg.get("ttl_seconds")}
        self.db.set_setting("cache", self._cache_cfg)

    def persist_routing(self) -> None:
        self.db.set_setting("routing", self.routing.config())

    async def catalogue(self, max_age_s: float = 10.0) -> list[dict]:
        if self._catalogue and time.time() - self._catalogue[0] < max_age_s:
            return self._catalogue[1]
        o = self.settings.topology["origin"]
        try:
            r = await http_request(o["host"], o["port"], "GET", "/catalog", timeout=5.0)
            items = r.json()["objects"]
        except (HTTPClientError, ValueError, KeyError, TypeError):
            if self._catalogue:
                return self._catalogue[1]
            raise
        self._catalogue = (time.time(), items)
        return items

    def server_summaries(self) -> list[dict]:
        return [self.cluster.describe(eid) for eid in self.cluster.edges]

    def snapshot(self) -> dict:
        s = self.live.summary()
        return {
            "summary": {k: s[k] for k in ("total_requests", "successful", "failed", "failure_rate", "throughput_rps",
                                          "latency", "cache", "origin", "bytes_served", "per_edge")},
            "recent": self.live.summary(window_s=10),
            "servers": [{"edge_id": d["edge_id"], "status": d["status"], "load": d["load"]["value"],
                         "active_requests": d["load"]["active_requests"], "latency_ms": d["latency"]["probe_rtt_ewma_ms"],
                         "packet_loss_est": d["packet_loss"]["estimated"], "cache_used_bytes": d["cache"].get("used_bytes"),
                         "cache_hit_ratio": d["cache"].get("hit_ratio"), "requests": d["requests"]}
                        for d in self.server_summaries()],
            "origin": {"status": self.cluster.origin_status,
                       "content_requests": (self.cluster.origin_stats or {}).get("content_requests"),
                       "bytes_served": (self.cluster.origin_stats or {}).get("bytes_served")},
            "experiment": self.runner.progress(),
            "routing": self.routing.config(),
        }

    async def _snapshot_loop(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(5)
            n += 1
            try:
                snap = self.snapshot()
                await asyncio.to_thread(self.db.insert_snapshot, {"summary": snap["recent"], "servers": snap["servers"],
                                                                  "origin": snap["origin"]})
                if n % 720 == 0:
                    await asyncio.to_thread(self.db.prune_snapshots)
            except Exception:  # noqa: BLE001
                log.exception("snapshot failed")


def _ipv4(host: str) -> str:
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        return "127.0.0.1" if host == "localhost" else host
