"""Traffic generator: issues real CNRP (UDP) resolutions and HTTP/1.1 (TCP) fetches.

Resolution modes
  per_request  every request performs a fresh UDP resolution, so the routing strategy is
               consulted per request (an explicit, non-DNS-like mode for routing experiments)
  dns          DNS-like behaviour: the answer is cached for its TTL and every request in that
               window goes to the same edge; a transport failure invalidates the cached answer
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from app.config import CDN_HOSTNAME
from app.core.cluster import Cluster
from app.networking.http import HTTPClientError, http_request
from app.networking.resolver import RCODE_NAMES, ResolverClient

DISTRIBUTIONS = ("uniform", "random", "zipf")


@dataclass
class Workload:
    total_requests: int = 300
    concurrency: int = 16
    rate_rps: float = 0.0  # 0 = as fast as concurrency allows
    distribution: str = "zipf"
    zipf_s: float = 1.1
    seed: int = 42
    objects: list[str] = field(default_factory=list)  # empty = whole catalogue
    resolution: str = "per_request"
    hostname: str = CDN_HOSTNAME
    timeout_s: float = 10.0


def build_sequence(w: Workload, catalogue: list[str]) -> list[str]:
    """Deterministic request sequence for a given seed.

    uniform  every object requested equally often (round-robin, shuffled)
    random   independent uniform random choice per request
    zipf     P(rank k) proportional to 1 / k^s; popularity ranks are a seeded shuffle of the catalogue
    """
    objs = sorted(w.objects or catalogue)
    if not objs:
        raise ValueError("object catalogue is empty")
    rng = random.Random(w.seed)
    n = w.total_requests
    if w.distribution == "uniform":
        seq = [objs[i % len(objs)] for i in range(n)]
        rng.shuffle(seq)
        return seq
    if w.distribution == "random":
        return [rng.choice(objs) for _ in range(n)]
    if w.distribution == "zipf":
        ranked = objs[:]
        rng.shuffle(ranked)
        weights = [1 / (k ** w.zipf_s) for k in range(1, len(ranked) + 1)]
        return rng.choices(ranked, weights=weights, k=n)
    raise ValueError(f"unknown distribution {w.distribution!r}")


class CDNClient:
    """What a CDN user agent does: resolve the CDN hostname, then GET the object from the edge."""

    def __init__(self, resolver: ResolverClient, cluster: Cluster | None, resolution: str = "per_request",
                 hostname: str = CDN_HOSTNAME, timeout_s: float = 10.0):
        if resolution not in ("per_request", "dns"):
            raise ValueError("resolution must be per_request or dns")
        self.resolver, self.cluster = resolver, cluster
        self.resolution, self.hostname, self.timeout_s = resolution, hostname, timeout_s
        self._cached: tuple[object, float] | None = None

    async def fetch(self, object_id: str, explain: bool = False) -> dict:
        rec: dict = {"object_id": object_id, "edge_id": None, "status_code": None, "ok": 0, "error": None,
                     "latency_ms": None, "resolve_ms": None, "bytes": 0, "cache_status": None,
                     "service_ms": None, "sim_delay_ms": None, "explanation": None}
        now = time.monotonic()
        if self.resolution == "dns" and self._cached and self._cached[1] > now:
            ans = self._cached[0]
        else:
            try:
                ans, rms = await self.resolver.resolve(self.hostname, explain=explain)
            except TimeoutError as exc:
                return self._done(rec, error=f"resolve: {exc}")
            rec["resolve_ms"] = round(rms, 3)
            if not ans.ok:
                return self._done(rec, error=f"resolve: {RCODE_NAMES.get(ans.rcode, ans.rcode)} {ans.explanation}".strip())
            if self.resolution == "dns":
                self._cached = (ans, now + ans.ttl)
        rec["edge_id"] = ans.edge_id
        rec["explanation"] = ans.explanation or None
        if self.cluster:
            self.cluster.inflight(ans.edge_id, +1)
        try:
            r = await http_request(ans.address, ans.port, "GET", f"/content/{object_id}", timeout=self.timeout_s)
        except HTTPClientError as exc:
            self._cached = None
            if self.cluster:
                self.cluster.report_client_result(ans.edge_id, True)
            return self._done(rec, error=str(exc))
        finally:
            if self.cluster:
                self.cluster.inflight(ans.edge_id, -1)
        if self.cluster:
            self.cluster.report_client_result(ans.edge_id, False)
        rec.update(status_code=r.status, ok=int(r.status == 200), latency_ms=round(r.elapsed_ms, 3),
                   bytes=len(r.body), cache_status=r.headers.get("x-cache"),
                   service_ms=_f(r.headers.get("x-edge-service-ms")), sim_delay_ms=_f(r.headers.get("x-sim-delay-ms")))
        if r.status != 200:
            rec["error"] = f"HTTP {r.status}"
        return self._done(rec)

    @staticmethod
    def _done(rec: dict, error: str | None = None) -> dict:
        if error:
            rec["error"] = error[:300]
        rec["ts"] = time.time()
        return rec


def _f(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


class TrafficRun:
    """Dispatches a request sequence with bounded concurrency and an optional rate limit."""

    def __init__(self, workload: Workload, client: CDNClient, sequence: list[str],
                 on_record: Callable[[dict], None]):
        self.w, self.client, self.sequence, self.on_record = workload, client, sequence, on_record
        self.total = len(sequence)
        self.dispatched = self.completed = self.failed = 0
        self.stop_event = asyncio.Event()
        self.started_at: float | None = None

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        self.started_at = time.time()
        sem = asyncio.Semaphore(self.w.concurrency)
        tasks: set[asyncio.Task] = set()
        t0 = time.perf_counter()
        for i, oid in enumerate(self.sequence):
            if self.stop_event.is_set():
                break
            if self.w.rate_rps > 0:
                delay = t0 + i / self.w.rate_rps - time.perf_counter()
                if delay > 0:
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), delay)
                        break
                    except TimeoutError:
                        pass
            await sem.acquire()
            if self.stop_event.is_set():
                sem.release()
                break
            self.dispatched += 1
            t = asyncio.create_task(self._one(oid))
            tasks.add(t)
            t.add_done_callback(lambda task: (tasks.discard(task), sem.release()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _one(self, oid: str) -> None:
        rec = await self.client.fetch(oid)
        self.completed += 1
        if not rec["ok"]:
            self.failed += 1
        self.on_record(rec)
