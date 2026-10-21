"""Control-plane API + integration tests: real UDP resolver, real HTTP edges (in-process)."""

import asyncio
import csv
import io
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.persistence.db import Database


@pytest.fixture
async def api(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            c.services = app.state.services
            yield c


async def wait_for(pred, timeout=10.0, step=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = pred()
        if asyncio.iscoroutine(r):
            r = await r
        if r:
            return r
        await asyncio.sleep(step)
    raise AssertionError("condition not met in time")


async def test_health_and_servers(api):
    r = await api.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["healthy_edges"] == 3 and body["origin"] == "healthy"
    servers = (await api.get("/api/servers")).json()["servers"]
    assert [s["edge_id"] for s in servers] == ["india-edge", "singapore-edge", "usa-edge"]
    assert all(s["latency"]["kind"] == "measured" and s["profile_kind"] == "simulated" for s in servers)
    assert (await api.get("/api/servers/india-edge")).json()["status"] == "healthy"
    r = await api.get("/api/servers/mars-edge")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


async def test_configuration_validation(api):
    r = await api.post("/api/routing/strategy", json={"strategy": "teleport"})
    assert r.status_code == 422 and r.json()["error"] == "validation_error"
    r = await api.post("/api/routing/strategy", json={"strategy": "hybrid",
                                                     "weights": {"latency_weight": 0, "load_weight": 0, "packet_loss_weight": 0}})
    assert r.status_code == 422
    r = await api.post("/api/cache/policy", json={"policy": "lru", "capacity_bytes": 10})
    assert r.status_code == 422
    r = await api.post("/api/experiments", json={"name": "x", "type": "failure"})
    assert r.status_code == 422 and "failure" in r.json()["detail"]
    r = await api.post("/api/experiments", json={"name": "x", "type": "failure", "failure": {"edge_id": "nope"}})
    assert r.status_code == 400
    r = await api.post("/api/servers/india-edge/profile", json={"packet_loss": 0.9})
    assert r.status_code == 422


async def test_routing_strategy_change_affects_resolution_and_persists(api, settings):
    r = await api.post("/api/routing/strategy", json={"strategy": "failover"})
    assert r.json()["strategy"] == "failover"
    rec = (await api.post("/api/requests/probe", json={"object_id": "index.html"})).json()
    assert rec["ok"] == 1 and rec["edge_id"] == "india-edge" and rec["explanation"].startswith("[failover]")
    r = await api.post("/api/routing/strategy", json={"strategy": "hybrid",
                                                     "weights": {"latency_weight": 1, "load_weight": 1, "packet_loss_weight": 0}})
    assert r.json()["weights"]["latency_weight"] == pytest.approx(0.5)
    assert Database(settings.db_path).get_setting("routing")["strategy"] == "hybrid"
    prev = (await api.get("/api/routing/preview")).json()
    assert {d["strategy"] for d in prev["decisions"]} >= {"random", "nearest", "hybrid", "least_loaded"}


async def test_cache_miss_origin_fetch_then_hit(api):
    await api.post("/api/routing/strategy", json={"strategy": "nearest"})
    origin_before = api.services.cluster.origin_stats["content_requests"]
    a = (await api.post("/api/requests/probe", json={"object_id": "images/photo-07.png"})).json()
    b = (await api.post("/api/requests/probe", json={"object_id": "images/photo-07.png"})).json()
    assert (a["cache_status"], b["cache_status"]) == ("MISS", "HIT") and a["bytes"] == b["bytes"] > 0
    await api.services.cluster.refresh_stats()
    assert api.services.cluster.origin_stats["content_requests"] - origin_before == 1
    detail = (await api.get("/api/cache/india-edge")).json()
    assert any(e["object_id"] == "images/photo-07.png" for e in detail["entries"])
    stats = (await api.get("/api/cache/stats")).json()
    assert stats["edges"]["india-edge"]["hits"] == 1
    # invalidate -> next request misses again
    r = await api.post("/api/cache/india-edge/invalidate", json={"object_id": "images/photo-07.png"})
    assert r.json()["invalidated"] is True
    c = (await api.post("/api/requests/probe", json={"object_id": "images/photo-07.png"})).json()
    assert c["cache_status"] == "MISS"
    assert (await api.post("/api/cache/india-edge/clear")).json()["cleared"] >= 1
    assert (await api.get("/api/cache/india-edge")).json()["entries"] == []
    summary = (await api.get("/api/metrics/summary")).json()
    assert summary["cache"]["hits"] == 1 and summary["origin"]["fetches"] == 2


async def test_cache_policy_change_applies_to_every_edge(api):
    r = await api.post("/api/cache/policy", json={"policy": "lfu", "capacity_bytes": 1048576, "ttl_seconds": None})
    assert r.status_code == 200
    stats = (await api.get("/api/cache/stats")).json()
    assert {c["policy"] for c in stats["edges"].values()} == {"lfu"}
    assert {c["capacity_bytes"] for c in stats["edges"].values()} == {1048576}
    assert stats["config"]["policy"] == "lfu"


async def test_missing_object_through_cdn(api):
    rec = (await api.post("/api/requests/probe", json={"object_id": "does/not/exist.bin"})).json()
    assert rec["ok"] == 0 and rec["status_code"] == 404
    errors = (await api.get("/api/metrics/errors")).json()["errors"]
    assert errors and errors[0]["status_code"] == 404


async def test_server_failure_detection_and_fallback(api):
    await api.post("/api/routing/strategy", json={"strategy": "nearest"})
    r = await api.post("/api/servers/india-edge/simulate-failure")
    assert r.status_code == 200
    await wait_for(lambda: api.services.cluster.edges["india-edge"].status == "unhealthy")
    rec = (await api.post("/api/requests/probe", json={"object_id": "index.html"})).json()
    assert rec["ok"] == 1 and rec["edge_id"] == "singapore-edge"
    assert "excluded unhealthy: india-edge" in rec["explanation"]
    ev = [e for e in (await api.get("/api/events")).json()["events"] if e["kind"] == "health_transition"
          and e["edge_id"] == "india-edge" and e["detail"]["to"] == "unhealthy"]
    assert ev and ev[0]["detail"]["detection_ms"] > 0
    await api.post("/api/servers/india-edge/restore")
    await wait_for(lambda: api.services.cluster.edges["india-edge"].status == "healthy")
    rec = (await api.post("/api/requests/probe", json={"object_id": "index.html"})).json()
    assert rec["edge_id"] == "india-edge"


async def test_pinned_hostname_and_unknown_hostname(api):
    rec = (await api.post("/api/requests/probe", json={"object_id": "index.html", "hostname": "usa.cdn.sim.local"})).json()
    assert rec["edge_id"] == "usa-edge" and rec["ok"] == 1
    rec = (await api.post("/api/requests/probe", json={"object_id": "index.html", "hostname": "x.example"})).json()
    assert rec["ok"] == 0 and "NXDOMAIN" in rec["error"]


async def test_experiment_lifecycle_persistence_and_csv(api, settings):
    body = {"name": "cache cmp", "type": "cache_comparison", "routing_strategy": "nearest",
            "policies": ["none", "lru"], "cache_capacity_bytes": 2 * 1024 * 1024,
            "workload": {"total_requests": 60, "concurrency": 8, "distribution": "zipf", "seed": 5,
                         "objects": [f"images/photo-{i:02d}.png" for i in range(1, 11)]}}
    exp = (await api.post("/api/experiments", json=body)).json()
    assert exp["status"] == "created"
    r = await api.post(f"/api/experiments/{exp['id']}/start")
    assert r.status_code == 200 and r.json()["total"] == 120
    assert (await api.post(f"/api/experiments/{exp['id']}/start")).status_code == 409
    done = await wait_for(lambda: _status(api, exp["id"]), timeout=60, step=0.3)
    assert done["status"] == "completed", done
    variants = {v["label"]: v["metrics"] for v in done["summary"]["variants"]}
    assert variants["none"]["hit_ratio"] is None and variants["none"]["origin_fetches"] == 60
    assert variants["lru"]["origin_fetches"] < 60 and variants["lru"]["hit_ratio"] > 0
    assert len(done["runs"]) == 2 and all(r["summary"]["total_requests"] == 60 for r in done["runs"])
    # the runner restored the pre-experiment cache configuration
    assert api.services.cache_config()["policy"] == "lru"
    # per-experiment metrics come from SQLite
    m = (await api.get(f"/api/metrics/summary?experiment_id={exp['id']}")).json()
    assert m["total_requests"] == 120
    rows = list(csv.DictReader(io.StringIO((await api.get(f"/api/experiments/{exp['id']}/export.csv")).text)))
    assert [r["variant"] for r in rows] == ["none", "lru"] and rows[0]["origin_fetches"] == "60"
    reqs = list(csv.DictReader(io.StringIO(
        (await api.get(f"/api/experiments/{exp['id']}/export.csv?kind=requests")).text)))
    assert len(reqs) == 120
    # persisted across a fresh connection to the database file
    db = Database(settings.db_path)
    assert db.get_experiment(exp["id"])["status"] == "completed"
    assert len(db.list_runs(exp["id"])) == 2
    db.close()


async def _status(api, exp_id):
    d = (await api.get(f"/api/experiments/{exp_id}")).json()
    return d if d["status"] in ("completed", "failed", "stopped") else None


async def test_experiment_stop(api):
    exp = (await api.post("/api/experiments", json={"name": "long", "start": True,
                                                    "workload": {"total_requests": 5000, "concurrency": 2}})).json()
    assert exp["status"] == "running"
    await asyncio.sleep(0.5)
    r = await api.post(f"/api/experiments/{exp['id']}/stop")
    assert r.status_code == 200
    done = await wait_for(lambda: _status(api, exp["id"]), timeout=30, step=0.3)
    assert done["status"] == "stopped"
    assert 0 < done["runs"][0]["summary"]["total_requests"] < 5000
    assert (await api.post(f"/api/experiments/{exp['id']}/stop")).status_code == 409


async def test_failure_experiment_records_detection(api):
    body = {"name": "fail", "type": "failure", "routing_strategy": "nearest",
            "workload": {"total_requests": 400, "concurrency": 4, "rate_rps": 100},
            "failure": {"edge_id": "india-edge", "at_s": 0.8, "restore_at_s": 2.5}}
    exp = (await api.post("/api/experiments", json={**body, "start": True})).json()
    done = await wait_for(lambda: _status(api, exp["id"]), timeout=60, step=0.3)
    f = done["summary"]["variants"][0]["failure"]
    assert f["injected"] and f["detection_ms"] is not None and f["detection_ms"] > 0
    assert f["first_success_elsewhere_ms"] is not None
    run = done["runs"][0]["summary"]
    assert run["per_edge"]["singapore-edge"]["requests"] > 0  # traffic failed over
    assert [e["kind"] for e in run["events"]] == ["edge_failed", "edge_restored"]
    assert api.services.cluster.edges["india-edge"].stats["failed"] is False  # restored afterwards


async def test_back_to_back_degradations_restore_original_profile(api):
    """Regression: the second experiment must not adopt the first one's degraded profile as its baseline."""
    original = (await api.get("/api/servers/india-edge")).json()["profile"]
    api.services.cluster._tasks[1].cancel()  # stop background stats polling: the cache only changes on explicit refresh
    body = {"name": "deg", "type": "degradation", "routing_strategy": "nearest",
            "workload": {"total_requests": 30, "concurrency": 4, "rate_rps": 30},
            "degradation": {"edge_id": "india-edge", "at_s": 0.3, "profile": {"base_latency_ms": 40, "packet_loss": 0}}}
    for _ in range(2):
        exp = (await api.post("/api/experiments", json={**body, "start": True})).json()
        done = await wait_for(lambda: _status(api, exp["id"]), timeout=60, step=0.2)
        assert done["status"] == "completed"
        first_bucket = done["runs"][0]["summary"]["timeline"][0]
        assert first_bucket["avg_ms"] < 150  # not degraded from the start
    await api.services.cluster.refresh_stats()
    assert (await api.get("/api/servers/india-edge")).json()["profile"] == original


async def test_metrics_timeseries_and_topology(api):
    for _ in range(3):
        await api.post("/api/requests/probe", json={"object_id": "index.html"})
    ts = (await api.get("/api/metrics/timeseries?window_s=60")).json()
    assert sum(p["requests"] for p in ts["points"]) == 3
    topo = (await api.get("/api/topology")).json()
    kinds = sorted(n["kind"] for n in topo["nodes"])
    assert kinds.count("edge") == 3 and "resolver" in kinds and topo["kind"] == "conceptual-local"


async def test_reset_clears_data(api):
    await api.post("/api/requests/probe", json={"object_id": "index.html"})
    await api.post("/api/experiments", json={"name": "tmp"})
    assert (await api.post("/api/reset")).status_code == 200
    assert (await api.get("/api/experiments")).json()["experiments"] == []
    assert (await api.get("/api/metrics/summary")).json()["total_requests"] == 0


def test_websocket_streams_snapshots_and_events(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/metrics") as ws:
            assert ws.receive_json()["type"] == "hello"
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            assert {s["edge_id"] for s in snap["data"]["servers"]} == {"india-edge", "singapore-edge", "usa-edge"}
            client.post("/api/servers/usa-edge/simulate-failure")
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "event" and msg["event"]["kind"] == "failure_injected":
                    break
            else:
                raise AssertionError("failure event not pushed")
            ws.send_text("ping")
            for _ in range(10):
                if ws.receive_json()["type"] == "pong":
                    break
            else:
                raise AssertionError("no pong")
        client.post("/api/servers/usa-edge/restore")
