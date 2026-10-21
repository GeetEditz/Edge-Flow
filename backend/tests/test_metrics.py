import pytest

from app.core.metrics import percentile, summarize
from app.traffic.generator import Workload, build_sequence


def rec(ts, lat, ok=True, cs="HIT", edge="a", size=100):
    return {"ts": ts, "latency_ms": lat if ok else None, "ok": int(ok), "cache_status": cs if ok else None,
            "edge_id": edge, "bytes": size if ok else 0, "status_code": 200 if ok else None,
            "error": None if ok else "connect: refused", "resolve_ms": 1.0}


def test_percentile_interpolation_and_minimum_samples():
    vals = list(range(1, 101))
    assert percentile(vals, 50) == pytest.approx(50.5)
    assert percentile(vals, 95) == pytest.approx(95.05)
    assert percentile(vals, 99) == pytest.approx(99.01)
    assert percentile(vals[:19], 95) is None and percentile(vals[:99], 99) is None
    assert percentile([], 50) is None


def test_summary_definitions():
    rs = [rec(10.0, 10, cs="MISS"), rec(10.1, 20, cs="HIT"), rec(10.2, 30, cs="COALESCED"),
          rec(10.3, 40, cs="BYPASS", edge="b"), rec(10.4, 0, ok=False, edge="b")]
    s = summarize(rs)
    assert s["total_requests"] == 5 and s["successful"] == 4 and s["failed"] == 1
    assert s["failure_rate"] == pytest.approx(0.2)
    assert s["cache"]["eligible_lookups"] == 3  # BYPASS is not a lookup
    assert s["cache"]["hit_ratio"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["origin"]["fetches"] == 2  # MISS + BYPASS
    assert s["origin"]["baseline_fetches"] == 4
    assert s["origin"]["reduction"] == pytest.approx(0.5)
    assert s["latency"]["avg_ms"] == 25 and s["latency"]["p95_ms"] is None  # < 20 samples
    assert s["per_edge"]["b"]["failed"] == 1 and s["per_edge"]["a"]["requests"] == 3
    assert s["errors"] == {"connect": 1}
    # span: first start (10.0 - 0.01) to last completion 10.4
    assert s["throughput_rps"] == pytest.approx(4 / 0.41, rel=1e-2)


def test_empty_summary_has_no_invented_values():
    s = summarize([])
    assert s["total_requests"] == 0 and s["throughput_rps"] is None and s["latency"]["avg_ms"] is None
    assert s["cache"]["hit_ratio"] is None and s["origin"]["reduction"] is None and s["failure_rate"] is None


def test_workload_sequences_are_seeded_and_shaped():
    cat = [f"o{i}" for i in range(10)]
    u = build_sequence(Workload(total_requests=100, distribution="uniform"), cat)
    assert sorted(set(u)) == sorted(cat) and all(u.count(o) == 10 for o in cat)
    z1 = build_sequence(Workload(total_requests=2000, distribution="zipf", seed=3), cat)
    z2 = build_sequence(Workload(total_requests=2000, distribution="zipf", seed=3), cat)
    assert z1 == z2
    counts = sorted((z1.count(o) for o in cat), reverse=True)
    assert counts[0] > 3 * counts[-1]  # heavy head
    r = build_sequence(Workload(total_requests=50, distribution="random", seed=1, objects=["x", "y"]), cat)
    assert set(r) <= {"x", "y"}
    with pytest.raises(ValueError):
        build_sequence(Workload(distribution="pareto"), cat)
