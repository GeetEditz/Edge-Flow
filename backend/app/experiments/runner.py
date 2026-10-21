"""Experiment orchestration.

An experiment expands into variants (one per routing strategy / cache policy being compared)
times repetitions. For every run the runner:
  1. restores all edges, resets their network profiles to the experiment baseline,
  2. applies the variant's routing strategy and cache policy (which empties every cache, so each
     run starts cold with identical state),
  3. replays the same seeded request sequence through the real UDP + HTTP path,
  4. injects scheduled events (edge failure / link degradation) at fixed offsets,
  5. persists every request record and the run summary to SQLite.
Afterwards the routing strategy, cache configuration and profiles in place before the
experiment are restored. Only one experiment runs at a time.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from statistics import mean
from typing import TYPE_CHECKING

from app.core.metrics import summarize
from app.traffic.generator import CDNClient, TrafficRun, Workload, build_sequence

if TYPE_CHECKING:
    from app.core.services import Services

log = logging.getLogger("cdn.experiments")

COMPARE_KEYS = ("avg_ms", "p50_ms", "p95_ms", "p99_ms", "throughput_rps", "hit_ratio", "origin_fetches",
                "origin_bytes", "origin_reduction", "failure_rate", "evictions", "total_requests", "failed")


class ExperimentBusy(Exception):
    pass


def flat_metrics(s: dict) -> dict:
    return {"avg_ms": s["latency"]["avg_ms"], "p50_ms": s["latency"]["p50_ms"], "p95_ms": s["latency"]["p95_ms"],
            "p99_ms": s["latency"]["p99_ms"], "throughput_rps": s["throughput_rps"],
            "hit_ratio": s["cache"]["hit_ratio"], "origin_fetches": s["origin"]["fetches"],
            "origin_bytes": s["origin"]["bytes"], "origin_reduction": s["origin"]["reduction"],
            "failure_rate": s["failure_rate"], "evictions": s.get("evictions"),
            "total_requests": s["total_requests"], "failed": s["failed"]}


def timeline(records: list[dict], start: float, buckets_max: int = 120) -> list[dict]:
    if not records:
        return []
    span = max(r["ts"] for r in records) - start
    width = max(0.5, round(span / buckets_max + 0.49, 0) if span > buckets_max else 1.0)
    out: dict[int, dict] = {}
    for r in records:
        i = int(max(0.0, r["ts"] - start) // width)
        b = out.setdefault(i, {"t": round(i * width, 2), "requests": 0, "errors": 0, "lat": [], "per_edge": {}})
        b["requests"] += 1
        eid = r.get("edge_id") or "unrouted"
        b["per_edge"][eid] = b["per_edge"].get(eid, 0) + 1
        if r.get("ok"):
            b["lat"].append(r["latency_ms"])
        else:
            b["errors"] += 1
    res = []
    for i in sorted(out):
        b = out[i]
        lat = b.pop("lat")
        b["avg_ms"] = round(mean(lat), 2) if lat else None
        res.append(b)
    return res


class ExperimentRunner:
    def __init__(self, services: "Services"):
        self.s = services
        self.current: dict | None = None
        self._task: asyncio.Task | None = None
        self._traffic: TrafficRun | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ API
    async def create(self, cfg: dict) -> dict:
        exp_id = uuid.uuid4().hex[:12]
        await asyncio.to_thread(self.s.db.create_experiment, exp_id, cfg["name"], cfg["type"], cfg)
        self.s.emit("experiment_created", None, {"experiment_id": exp_id, "name": cfg["name"]})
        return await asyncio.to_thread(self.s.db.get_experiment, exp_id)

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, exp_id: str) -> dict:
        if self.running():
            raise ExperimentBusy(f"experiment {self.current['experiment_id']} is already running")
        exp = await asyncio.to_thread(self.s.db.get_experiment, exp_id)
        if exp is None:
            raise KeyError(exp_id)
        if exp["status"] not in ("created", "completed", "stopped", "failed", "interrupted"):
            raise ExperimentBusy(f"experiment is {exp['status']}")
        variants = self.variants(exp["config"])
        total = len(variants) * exp["config"]["repetitions"] * exp["config"]["workload"]["total_requests"]
        self._stop = asyncio.Event()
        self.current = {"experiment_id": exp_id, "name": exp["name"], "type": exp["type"], "status": "running",
                        "completed": 0, "failed": 0, "total": total, "run_index": 0,
                        "run_count": len(variants) * exp["config"]["repetitions"], "variant": None,
                        "started_at": time.time()}
        await asyncio.to_thread(self.s.db.update_experiment, exp_id, status="running", started_at=time.time(),
                                error=None, finished_at=None)
        self._task = asyncio.create_task(self._execute(exp, variants))
        self.s.emit("experiment_started", None, {"experiment_id": exp_id, "name": exp["name"]})
        return self.progress()

    async def stop(self, exp_id: str) -> dict:
        if not self.running() or self.current["experiment_id"] != exp_id:
            raise KeyError(exp_id)
        self.current["status"] = "stopping"
        self._stop.set()
        if self._traffic:
            self._traffic.stop()
        return self.progress()

    def progress(self) -> dict | None:
        if not self.current:
            return None
        p = dict(self.current)
        if self._traffic and p["status"] in ("running", "stopping"):
            p["completed"] = self.current["completed"] + self._traffic.completed
            p["failed"] = self.current["failed"] + self._traffic.failed
        p["fraction"] = round(p["completed"] / p["total"], 4) if p["total"] else None
        return p

    async def shutdown(self) -> None:
        if self.running():
            self._stop.set()
            if self._traffic:
                self._traffic.stop()
            try:
                await asyncio.wait_for(self._task, 15)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    @staticmethod
    def variants(cfg: dict) -> list[dict]:
        base = {"routing": cfg["routing_strategy"], "cache": cfg["cache_policy"]}
        if cfg["type"] == "routing_comparison":
            return [{"label": s, "routing": s, "cache": cfg["cache_policy"]} for s in cfg["strategies"]]
        if cfg["type"] == "cache_comparison":
            return [{"label": p, "routing": cfg["routing_strategy"], "cache": p} for p in cfg["policies"]]
        return [{"label": f"{base['routing']} / {base['cache']}", **base}]

    # ------------------------------------------------------------------ execution
    async def _execute(self, exp: dict, variants: list[dict]) -> None:
        cfg, exp_id = exp["config"], exp["id"]
        cluster, routing = self.s.cluster, self.s.routing
        saved_routing = routing.config()
        saved_cache = self.s.cache_config()
        # read the edges' current profiles fresh: cached stats may predate the previous experiment's restore
        await cluster.refresh_stats()
        baseline_profiles = {eid: (e.stats or {}).get("profile", e.cfg.get("profile"))
                             for eid, e in cluster.edges.items()}
        baseline_limits = {eid: e.concurrency_limit for eid, e in cluster.edges.items()}
        results: dict[str, list[dict]] = {v["label"]: [] for v in variants}
        status, error = "completed", None
        try:
            catalogue = [o["object_id"] for o in await self.s.catalogue()]
            wl = Workload(**cfg["workload"])
            sequence = build_sequence(wl, catalogue)
            for rep in range(cfg["repetitions"]):
                for v in variants:
                    if self._stop.is_set():
                        break
                    self.current["run_index"] += 1
                    self.current["variant"] = v["label"]
                    summary = await self._run_one(exp_id, cfg, v, rep, wl, sequence, baseline_profiles, baseline_limits)
                    results[v["label"]].append(summary)
            if self._stop.is_set():
                status = "stopped"
        except Exception as exc:  # noqa: BLE001 - recorded on the experiment
            log.exception("experiment %s failed", exp_id)
            status, error = "failed", str(exc)[:500]
        finally:
            await self._restore(saved_routing, saved_cache, baseline_profiles, baseline_limits)
            summary = self._aggregate(cfg, variants, results)
            await asyncio.to_thread(self.s.db.update_experiment, exp_id, status=status, finished_at=time.time(),
                                    error=error, summary=summary)
            if self.current:
                self.current["status"] = status
                self.current["finished_at"] = time.time()
                if self._traffic:
                    self.current["completed"] += self._traffic.completed
                    self.current["failed"] += self._traffic.failed
            self._traffic = None
            self.s.emit("experiment_finished", None, {"experiment_id": exp_id, "status": status, "error": error})

    async def _prepare(self, v: dict, cfg: dict, rep: int, baseline_profiles: dict, baseline_limits: dict) -> None:
        cluster = self.s.cluster
        for eid, e in cluster.edges.items():
            if (e.stats or {}).get("failed"):
                await cluster.restore_edge(eid)
            await cluster.admin(eid, "POST", "/profile", {**(baseline_profiles[eid] or {}),
                                                          "concurrency_limit": baseline_limits[eid]})
        self.s.routing.set_strategy(v["routing"], cfg.get("weights"))
        self.s.routing.random.reseed(cfg["workload"]["seed"] + rep)
        await cluster.admin_all("POST", "/cache/config", {"policy": v["cache"],
                                                          "capacity_bytes": cfg["cache_capacity_bytes"],
                                                          "ttl_seconds": cfg["cache_ttl_seconds"]})
        # wait until every edge is healthy again (bounded) so all runs start from the same state
        deadline = time.monotonic() + 10
        while any(e.status != "healthy" for e in cluster.edges.values()) and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        await cluster.refresh_stats()

    async def _run_one(self, exp_id: str, cfg: dict, v: dict, rep: int, wl: Workload, sequence: list[str],
                       baseline_profiles: dict, baseline_limits: dict) -> dict:
        db, cluster = self.s.db, self.s.cluster
        await self._prepare(v, cfg, rep, baseline_profiles, baseline_limits)
        before = {eid: (e.stats or {}).get("cache", {}).get("evictions", 0) for eid, e in cluster.edges.items()}
        origin_before = (cluster.origin_stats or {}).get("content_requests")
        run_id = await asyncio.to_thread(db.create_run, exp_id, v["label"], rep, v["routing"], v["cache"])
        records: list[dict] = []
        events: list[dict] = []

        def on_record(rec: dict) -> None:
            rec["experiment_id"], rec["run_id"] = exp_id, run_id
            records.append(rec)
            self.s.live.add(rec)

        client = CDNClient(self.s.resolver_client, cluster, wl.resolution, wl.hostname, wl.timeout_s)
        traffic = TrafficRun(wl, client, sequence, on_record)
        self._traffic = traffic
        start = time.time()
        sched = [asyncio.create_task(t) for t in self._schedule(cfg, start, events)]
        try:
            await traffic.run()
        finally:
            for t in sched:
                t.cancel()
            await asyncio.gather(*sched, return_exceptions=True)
            self.current["completed"] += traffic.completed
            self.current["failed"] += traffic.failed
            self._traffic = None
        await asyncio.sleep(cluster.stats_interval_s + 0.2)  # let one stats poll capture final counters
        await cluster.refresh_stats()
        after = {eid: (e.stats or {}).get("cache", {}).get("evictions", 0) for eid, e in cluster.edges.items()}
        origin_after = (cluster.origin_stats or {}).get("content_requests")
        summary = summarize(records)
        summary["evictions"] = sum(max(0, after[k] - before.get(k, 0)) for k in after)
        summary["origin"]["observed_at_origin"] = (origin_after - origin_before
                                                   if origin_before is not None and origin_after is not None else None)
        summary["timeline"] = timeline(records, start)
        summary["events"] = events
        if cfg["type"] == "failure":
            summary["failure"] = self._failure_metrics(cfg["failure"]["edge_id"], records, events)
        summary["variant"], summary["routing"], summary["cache_policy"], summary["repetition"] = \
            v["label"], v["routing"], v["cache"], rep
        await asyncio.to_thread(db.insert_requests, records)
        await asyncio.to_thread(db.finish_run, run_id, summary, events)
        return summary

    def _schedule(self, cfg: dict, start: float, events: list[dict]):
        cluster = self.s.cluster

        async def at(offset: float, coro_fn, kind: str, detail: dict):
            await asyncio.sleep(max(0.0, start + offset - time.time()))
            t_inject = time.time()  # effects (e.g. refused connections) can be observed before the call returns
            try:
                await coro_fn()
                events.append({"t": round(t_inject - start, 3), "at": t_inject, "kind": kind, **detail})
            except Exception as exc:  # noqa: BLE001
                events.append({"t": round(time.time() - start, 3), "kind": kind + "_error", "error": str(exc)})

        if cfg["type"] == "failure" and cfg.get("failure"):
            f = cfg["failure"]
            yield at(f["at_s"], lambda: cluster.fail_edge(f["edge_id"]), "edge_failed", {"edge_id": f["edge_id"]})
            if f.get("restore_at_s") is not None:
                yield at(f["restore_at_s"], lambda: cluster.restore_edge(f["edge_id"]), "edge_restored",
                         {"edge_id": f["edge_id"]})
        if cfg["type"] == "degradation" and cfg.get("degradation"):
            d = cfg["degradation"]
            patch = {k: v for k, v in d["profile"].items() if v is not None}
            yield at(d["at_s"], lambda: cluster.admin(d["edge_id"], "POST", "/profile", patch), "link_degraded",
                     {"edge_id": d["edge_id"], "profile": patch})

    def _failure_metrics(self, edge_id: str, records: list[dict], events: list[dict]) -> dict:
        inj = next((e["at"] for e in events if e["kind"] == "edge_failed"), None)
        res = next((e["at"] for e in events if e["kind"] == "edge_restored"), None)
        if inj is None:
            return {"edge_id": edge_id, "injected": False}
        trans = self.s.cluster.edges[edge_id].transitions
        detected = next((t for t in trans if t["at"] >= inj and t["to"] == "unhealthy"), None)
        recovered = next((t for t in trans if res and t["at"] >= res and t["to"] == "healthy"), None)
        after = [r for r in records if r["ts"] >= inj and (res is None or r["ts"] < res)]
        failed_after = [r for r in after if not r["ok"]]
        failed_on_edge = [r for r in failed_after if r.get("edge_id") == edge_id]
        first_ok_other = next((r for r in sorted(after, key=lambda r: r["ts"])
                               if r["ok"] and r.get("edge_id") != edge_id), None)
        return {
            "edge_id": edge_id, "injected": True,
            "detection_ms": round((detected["at"] - inj) * 1000, 1) if detected else None,
            "detection_reason": detected["reason"] if detected else None,
            "failed_requests": len(failed_after), "failed_on_failed_edge": len(failed_on_edge),
            "last_failure_ms": round((max(r["ts"] for r in failed_on_edge) - inj) * 1000, 1) if failed_on_edge else None,
            "first_success_elsewhere_ms": round((first_ok_other["ts"] - inj) * 1000, 1) if first_ok_other else None,
            "requests_during_failure": len(after),
            "recovery_ms": round((recovered["at"] - res) * 1000, 1) if recovered else None,
        }

    async def _restore(self, saved_routing: dict, saved_cache: dict, profiles: dict, limits: dict) -> None:
        cluster = self.s.cluster
        try:
            self.s.routing.set_strategy(saved_routing["strategy"], saved_routing["weights"])
            for eid, e in cluster.edges.items():
                if (e.stats or {}).get("failed") or e.failure_injected_at:
                    await cluster.restore_edge(eid)
                await cluster.admin(eid, "POST", "/profile", {**(profiles[eid] or {}), "concurrency_limit": limits[eid]})
            await cluster.admin_all("POST", "/cache/config", {"policy": saved_cache["policy"],
                                                              "capacity_bytes": saved_cache["capacity_bytes"],
                                                              "ttl_seconds": saved_cache["ttl_seconds"]})
            await cluster.refresh_stats()
        except Exception:  # noqa: BLE001
            log.exception("failed to restore pre-experiment configuration")

    @staticmethod
    def _aggregate(cfg: dict, variants: list[dict], results: dict[str, list[dict]]) -> dict:
        out = []
        for v in variants:
            runs = results.get(v["label"], [])
            if not runs:
                continue
            flats = [flat_metrics(r) for r in runs]
            metrics = {}
            for k in COMPARE_KEYS:
                vals = [f[k] for f in flats if f[k] is not None]
                metrics[k] = round(mean(vals), 4) if vals else None
            edges = sorted({e for r in runs for e in r["per_edge"]})
            share = {e: round(mean(r["per_edge"].get(e, {}).get("share") or 0 for r in runs), 4) for e in edges}
            entry = {"label": v["label"], "routing": v["routing"], "cache_policy": v["cache"], "runs": len(runs),
                     "metrics": metrics, "edge_share": share}
            if cfg["type"] == "failure":
                entry["failure"] = runs[0].get("failure")
            out.append(entry)
        return {"type": cfg["type"], "variants": out, "repetitions": cfg["repetitions"]}
