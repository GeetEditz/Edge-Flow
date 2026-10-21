"""REST endpoints of the control plane. All responses are JSON; errors are {"error", "detail"}."""

from __future__ import annotations

import asyncio
import csv
import io
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from app.config import CDN_HOSTNAME
from app.core.metrics import summarize
from app.core.services import Services
from app.experiments.runner import COMPARE_KEYS, flat_metrics
from app.schemas.models import (CachePolicyUpdate, ExperimentCreate, InvalidateRequest, OriginConfig, ProbeRequest,
                                ProfileUpdate, RoutingUpdate, WarmRequest)
from app.traffic.generator import CDNClient

router = APIRouter(prefix="/api")


def svc(request: Request) -> Services:
    return request.app.state.services


# ---------------------------------------------------------------- system
@router.get("/health")
async def health(request: Request):
    s = svc(request)
    edges = {eid: e.status for eid, e in s.cluster.edges.items()}
    return {"status": "ok", "mode": s.settings.mode, "launch": s.settings.launch,
            "uptime_s": round(time.time() - s.started_at, 1), "origin": s.cluster.origin_status, "edges": edges,
            "healthy_edges": sum(1 for v in edges.values() if v == "healthy"),
            "resolver": {"host": s.resolver.host, "port": s.resolver.port, "protocol": "CNRP/1 over UDP"},
            "experiment_running": s.runner.running()}


@router.get("/config")
async def config(request: Request):
    s = svc(request)
    return {"mode": s.settings.mode, "launch": s.settings.launch, "hostname": CDN_HOSTNAME,
            "resolver": {"host": s.resolver.host, "port": s.resolver.port, "ttl_s": s.settings.resolver_ttl_s},
            "health": {"probe_interval_s": s.settings.probe_interval_s, "fail_threshold": s.settings.fail_threshold,
                       "recover_threshold": s.settings.recover_threshold},
            "client": s.settings.topology.get("client"), "routing": s.routing.config(),
            "strategies": s.routing.strategies, "cache": s.cache_config()}


@router.get("/events")
async def events(request: Request, limit: int = Query(100, ge=1, le=500)):
    return {"events": list(reversed(svc(request).events))[:limit]}


@router.post("/reset")
async def reset_all(request: Request):
    s = svc(request)
    if s.runner.running():
        raise HTTPException(409, "stop the running experiment first")
    await asyncio.to_thread(s.db.reset)
    s.live.reset()
    s.events.clear()
    s.routing.recent.clear()
    s.routing.counts.clear()
    await s.cluster.admin_all("POST", "/reset-counters")
    await s.cluster.origin_admin("POST", "/reset-counters")
    s.emit("data_reset", None, {})
    return {"ok": True}


# ---------------------------------------------------------------- servers
@router.get("/servers")
async def servers(request: Request):
    s = svc(request)
    return {"servers": s.server_summaries(),
            "origin": {"status": s.cluster.origin_status, "stats": s.cluster.origin_stats,
                       **{k: s.settings.topology["origin"].get(k) for k in ("host", "port", "location")}}}


@router.get("/servers/{server_id}")
async def server(request: Request, server_id: str):
    return svc(request).cluster.describe(server_id)


@router.post("/servers/{server_id}/simulate-failure")
async def simulate_failure(request: Request, server_id: str):
    s = svc(request)
    await s.cluster.fail_edge(server_id)
    return {"edge_id": server_id, "failed": True, "note": "data listener closed; health monitor will detect it"}


@router.post("/servers/{server_id}/restore")
async def restore(request: Request, server_id: str):
    s = svc(request)
    await s.cluster.restore_edge(server_id)
    return {"edge_id": server_id, "failed": False}


@router.post("/servers/{server_id}/profile")
async def set_profile(request: Request, server_id: str, body: ProfileUpdate):
    s = svc(request)
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(400, "no profile fields supplied")
    res = await s.cluster.admin(server_id, "POST", "/profile", patch)
    s.emit("profile_changed", server_id, patch)
    await s.cluster.refresh_stats()
    return res


@router.get("/origin")
async def origin(request: Request):
    s = svc(request)
    return {"status": s.cluster.origin_status, "stats": s.cluster.origin_stats}


@router.post("/origin/config")
async def origin_config(request: Request, body: OriginConfig):
    s = svc(request)
    res = await s.cluster.origin_admin("POST", "/config", body.model_dump())
    s.emit("origin_config_changed", None, body.model_dump())
    return res


@router.get("/content")
async def content(request: Request):
    items = await svc(request).catalogue(max_age_s=0)
    return {"objects": items, "count": len(items), "total_bytes": sum(i["size"] for i in items)}


# ---------------------------------------------------------------- metrics
@router.get("/metrics/summary")
async def metrics_summary(request: Request, experiment_id: str | None = None,
                          window_s: float | None = Query(None, gt=0, le=86400)):
    s = svc(request)
    if experiment_id:
        recs = await asyncio.to_thread(s.db.experiment_requests, experiment_id)
        if not recs and not await asyncio.to_thread(s.db.get_experiment, experiment_id):
            raise HTTPException(404, f"experiment {experiment_id} not found")
        return {"source": "experiment", "experiment_id": experiment_id, **summarize(recs)}
    return {"source": "live", **s.live.summary(window_s)}


@router.get("/metrics/timeseries")
async def metrics_timeseries(request: Request, window_s: int = Query(300, ge=10, le=3600),
                             bucket_s: int = Query(1, ge=1, le=60), source: str = Query("live", pattern="^(live|snapshots)$")):
    s = svc(request)
    if source == "snapshots":
        return {"source": source, "points": await asyncio.to_thread(s.db.snapshots, time.time() - window_s)}
    return {"source": source, "bucket_s": bucket_s, "points": s.live.timeseries(window_s, bucket_s)}


@router.get("/metrics/errors")
async def metrics_errors(request: Request, limit: int = Query(20, ge=1, le=200)):
    return {"errors": svc(request).live.recent_errors(limit)}


@router.post("/metrics/reset")
async def metrics_reset(request: Request):
    svc(request).live.reset()
    return {"ok": True}


# ---------------------------------------------------------------- routing
@router.get("/routing")
async def routing(request: Request):
    s = svc(request)
    return {**s.routing.config(), "strategies": s.routing.strategies, "decision_counts": dict(s.routing.counts)}


@router.post("/routing/strategy")
async def routing_strategy(request: Request, body: RoutingUpdate):
    s = svc(request)
    if s.runner.running():
        raise HTTPException(409, "an experiment is controlling the routing strategy; stop it first")
    s.routing.set_strategy(body.strategy, body.weights.model_dump() if body.weights else None)
    s.persist_routing()
    s.emit("routing_changed", None, s.routing.config())
    return s.routing.config()


@router.get("/routing/decisions")
async def routing_decisions(request: Request, limit: int = Query(50, ge=1, le=200)):
    s = svc(request)
    return {"decisions": [d.to_dict() for d in list(s.routing.recent)[-limit:]][::-1]}


@router.get("/routing/preview")
async def routing_preview(request: Request):
    """Evaluate every strategy against the current fleet state without recording a decision."""
    s = svc(request)
    views = s.cluster.views()
    return {"inputs": [v.__dict__ for v in views],
            "decisions": [s.routing.select(views, st["name"], record=False).to_dict() for st in s.routing.strategies]}


@router.post("/requests/probe")
async def probe(request: Request, body: ProbeRequest):
    """Resolve over UDP and fetch over HTTP exactly like a client; the result enters live metrics."""
    s = svc(request)
    client = CDNClient(s.resolver_client, s.cluster, "per_request", body.hostname)
    rec = await client.fetch(body.object_id, explain=True)
    rec["experiment_id"] = rec["run_id"] = None
    s.live.add(rec)
    await asyncio.to_thread(s.db.insert_requests, [rec])
    return rec


# ---------------------------------------------------------------- cache
@router.get("/cache/stats")
async def cache_stats(request: Request):
    s = svc(request)
    per = {d["edge_id"]: d["cache"] for d in s.server_summaries()}
    hits = sum(c.get("hits", 0) for c in per.values())
    elig = sum(c.get("eligible_lookups", 0) for c in per.values())
    return {"config": s.cache_config(), "edges": per,
            "totals": {"hits": hits, "eligible_lookups": elig, "hit_ratio": hits / elig if elig else None,
                       "used_bytes": sum(c.get("used_bytes", 0) for c in per.values()),
                       "capacity_bytes": sum(c.get("capacity_bytes", 0) for c in per.values()),
                       "evictions": sum(c.get("evictions", 0) for c in per.values()),
                       "origin_fetches": sum(c.get("origin_fetches", 0) for c in per.values())}}


@router.post("/cache/policy")
async def cache_policy(request: Request, body: CachePolicyUpdate):
    s = svc(request)
    if s.runner.running():
        raise HTTPException(409, "an experiment is controlling the cache policy; stop it first")
    payload = {"policy": body.policy, "capacity_bytes": body.capacity_bytes, "ttl_seconds": body.ttl_seconds}
    ids = body.edge_ids or list(s.cluster.edges)
    for eid in ids:
        await s.cluster.admin(eid, "POST", "/cache/config", payload)
    if body.edge_ids is None:
        s.set_cache_config(payload)
    s.emit("cache_policy_changed", None, {**payload, "edges": ids})
    await s.cluster.refresh_stats()
    return {"config": payload, "edges": ids, "note": "caches restart empty after a policy change"}


@router.get("/cache/{server_id}")
async def cache_detail(request: Request, server_id: str):
    return await svc(request).cluster.admin(server_id, "GET", "/cache")


@router.post("/cache/{server_id}/clear")
async def cache_clear(request: Request, server_id: str):
    s = svc(request)
    res = await s.cluster.admin(server_id, "POST", "/cache/clear")
    s.emit("cache_cleared", server_id, res)
    return res


@router.post("/cache/{server_id}/invalidate")
async def cache_invalidate(request: Request, server_id: str, body: InvalidateRequest):
    return await svc(request).cluster.admin(server_id, "POST", "/cache/invalidate", body.model_dump())


@router.post("/cache/{server_id}/warm")
async def cache_warm(request: Request, server_id: str, body: WarmRequest):
    s = svc(request)
    ids = body.object_ids or [o["object_id"] for o in (await s.catalogue())[:10]]
    return await s.cluster.admin(server_id, "POST", "/cache/warm", {"object_ids": ids}, timeout=60)


# ---------------------------------------------------------------- experiments
@router.get("/experiments")
async def experiments(request: Request):
    s = svc(request)
    return {"experiments": await asyncio.to_thread(s.db.list_experiments), "current": s.runner.progress()}


@router.post("/experiments", status_code=201)
async def create_experiment(request: Request, body: ExperimentCreate):
    s = svc(request)
    cfg = body.model_dump()
    start = cfg.pop("start")
    for ev in ("failure", "degradation"):
        if cfg.get(ev) and cfg[ev]["edge_id"] not in s.cluster.edges:
            raise HTTPException(400, f"unknown edge {cfg[ev]['edge_id']!r} in {ev} event")
    exp = await s.runner.create(cfg)
    if start:
        await s.runner.start(exp["id"])
        exp = await asyncio.to_thread(s.db.get_experiment, exp["id"])
    return exp


@router.get("/experiments/{experiment_id}")
async def experiment(request: Request, experiment_id: str):
    s = svc(request)
    exp = await asyncio.to_thread(s.db.get_experiment, experiment_id)
    if not exp:
        raise HTTPException(404, f"experiment {experiment_id} not found")
    exp["runs"] = await asyncio.to_thread(s.db.list_runs, experiment_id)
    cur = s.runner.progress()
    exp["progress"] = cur if cur and cur["experiment_id"] == experiment_id else None
    return exp


@router.post("/experiments/{experiment_id}/start")
async def start_experiment(request: Request, experiment_id: str):
    return await svc(request).runner.start(experiment_id)


@router.post("/experiments/{experiment_id}/stop")
async def stop_experiment(request: Request, experiment_id: str):
    try:
        return await svc(request).runner.stop(experiment_id)
    except KeyError:
        raise HTTPException(409, f"experiment {experiment_id} is not running") from None


@router.delete("/experiments/{experiment_id}")
async def delete_experiment(request: Request, experiment_id: str):
    s = svc(request)
    cur = s.runner.progress()
    if s.runner.running() and cur and cur["experiment_id"] == experiment_id:
        raise HTTPException(409, "cannot delete a running experiment")
    if not await asyncio.to_thread(s.db.delete_experiment, experiment_id):
        raise HTTPException(404, f"experiment {experiment_id} not found")
    return {"deleted": experiment_id}


def _csv(rows: list[dict], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


@router.get("/experiments/{experiment_id}/export.csv")
async def export_csv(request: Request, experiment_id: str, kind: str = Query("runs", pattern="^(runs|requests)$")):
    s = svc(request)
    exp = await asyncio.to_thread(s.db.get_experiment, experiment_id)
    if not exp:
        raise HTTPException(404, f"experiment {experiment_id} not found")
    if kind == "requests":
        rows = await asyncio.to_thread(s.db.experiment_requests, experiment_id)
        cols = ["variant", "repetition", "ts", "object_id", "edge_id", "status_code", "ok", "error", "latency_ms",
                "resolve_ms", "bytes", "cache_status", "service_ms", "sim_delay_ms"]
    else:
        runs = await asyncio.to_thread(s.db.list_runs, experiment_id)
        rows = [{"experiment_id": experiment_id, "experiment": exp["name"], "variant": r["variant"],
                 "repetition": r["repetition"], "routing_strategy": r["routing_strategy"],
                 "cache_policy": r["cache_policy"], **flat_metrics(r["summary"])} for r in runs if r["summary"]]
        cols = ["experiment_id", "experiment", "variant", "repetition", "routing_strategy", "cache_policy",
                *COMPARE_KEYS]
    fname = f"experiment-{experiment_id}-{kind}.csv"
    return Response(_csv(rows, cols), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/experiments/export/all.csv")
async def export_all(request: Request):
    s = svc(request)
    rows = []
    for exp in await asyncio.to_thread(s.db.list_experiments):
        for v in (exp["summary"] or {}).get("variants", []):
            rows.append({"experiment_id": exp["id"], "experiment": exp["name"], "type": exp["type"],
                         "status": exp["status"], "variant": v["label"], "routing_strategy": v["routing"],
                         "cache_policy": v["cache_policy"], "runs": v["runs"], **v["metrics"]})
    cols = ["experiment_id", "experiment", "type", "status", "variant", "routing_strategy", "cache_policy", "runs",
            *COMPARE_KEYS]
    return Response(_csv(rows, cols), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="experiments.csv"'})


# ---------------------------------------------------------------- topology
@router.get("/topology")
async def topology(request: Request):
    s = svc(request)
    topo = s.settings.topology
    o = topo["origin"]
    nodes = [
        {"id": "client", "kind": "client", "label": topo.get("client", {}).get("name", "Client"),
         "detail": topo.get("client", {}).get("location"), "status": "healthy"},
        {"id": "resolver", "kind": "resolver", "label": "CNRP resolver", "status": "healthy",
         "detail": f"udp {s.resolver.host}:{s.resolver.port}"},
        {"id": "control", "kind": "control", "label": "Control plane", "status": "healthy",
         "detail": f"http {s.settings.api_host}:{s.settings.api_port}"},
        {"id": "origin", "kind": "origin", "label": "Origin", "status": s.cluster.origin_status,
         "detail": f"http {o['host']}:{o['port']} · {o.get('location', '')}", "delay_ms": (s.cluster.origin_stats or {}).get("delay_ms")},
    ]
    links = [{"source": "client", "target": "resolver", "protocol": "UDP", "label": "CNRP query/answer"},
             {"source": "control", "target": "origin", "protocol": "TCP", "label": "health + stats"}]
    for e in s.cluster.edges.values():
        d = s.cluster.describe(e.edge_id)
        nodes.append({"id": e.edge_id, "kind": "edge", "label": d["name"], "status": d["status"],
                      "detail": f"http {e.cfg['host']}:{e.cfg['data_port']} · {d['location']}"})
        links.append({"source": "client", "target": e.edge_id, "protocol": "HTTP/TCP", "label": "content",
                      "profile": d["profile"], "profile_kind": "simulated" if s.settings.mode == "local" else "mininet",
                      "measured_rtt_ms": d["latency"]["probe_rtt_ewma_ms"], "status": d["status"],
                      "share": (s.live.summary(60)["per_edge"].get(e.edge_id) or {}).get("share")})
        links.append({"source": e.edge_id, "target": "origin", "protocol": "HTTP/TCP", "label": "cache fill"})
        links.append({"source": "control", "target": e.edge_id, "protocol": "TCP", "label": "probes + admin"})
    return {"mode": s.settings.mode,
            "kind": "running-mininet" if s.settings.mode == "mininet" else "conceptual-local",
            "note": ("Processes run on Mininet hosts; links are shaped by tc/netem." if s.settings.mode == "mininet" else
                     "Local Mode: all servers run on this machine over loopback; link conditions are simulated "
                     "inside each edge, not by a real network."),
            "nodes": nodes, "links": links}
