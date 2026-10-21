"""Metric definitions and aggregation over client-side request records.

A request record is produced by the client (traffic generator or probe endpoint) for every
HTTP request it issues and contains MEASURED values only:
  latency_ms   wall-clock time from TCP connect to last body byte, measured by the client.
               In Local Mode it includes the edge's simulated link delay (also reported
               separately as sim_delay_ms) — it is a real measurement of a simulated path.
  resolve_ms   CNRP (UDP) resolution round-trip time, measured by the client (null if cached)
  cache_status X-Cache header returned by the edge

Definitions:
  successful        HTTP 200 responses
  failed            everything else (connection refused/reset, timeout, 4xx, 5xx, no healthy edge)
  throughput (rps)  successful requests / wall-clock span of the sample
  latency stats     over successful requests only; P50 needs >= 1 sample, P95 >= 20, P99 >= 100,
                    otherwise null ("insufficient samples")
  eligible lookups  HIT + MISS + COALESCED responses
  hit_ratio         HIT / eligible lookups (null when there are no eligible lookups)
  origin_fetches    MISS + BYPASS responses (each caused exactly one origin fetch)
  baseline_origin   requests answered by an edge (HIT+MISS+COALESCED+BYPASS): the number of origin
                    fetches the SAME workload would cause with caching disabled
  origin_reduction  1 - origin_fetches / baseline_origin
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict, deque
from typing import Iterable

PCT_MIN_SAMPLES = {50: 1, 95: 20, 99: 100}


def percentile(sorted_vals: list[float], p: float) -> float | None:
    """Linear-interpolated percentile of an ascending list; None if not enough samples."""
    if len(sorted_vals) < PCT_MIN_SAMPLES.get(int(p), 1):
        return None
    k = (len(sorted_vals) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _r(v: float | None, d: int = 2) -> float | None:
    return None if v is None else round(v, d)


def latency_stats(values: Iterable[float]) -> dict:
    v = sorted(values)
    return {"samples": len(v), "avg_ms": _r(sum(v) / len(v)) if v else None,
            "p50_ms": _r(percentile(v, 50)), "p95_ms": _r(percentile(v, 95)), "p99_ms": _r(percentile(v, 99)),
            "min_ms": _r(v[0]) if v else None, "max_ms": _r(v[-1]) if v else None}


def summarize(records: list[dict], duration_s: float | None = None) -> dict:
    total = len(records)
    ok = [r for r in records if r.get("ok")]
    cs = Counter(r.get("cache_status") for r in records if r.get("cache_status"))
    hits, misses, coal, byp = cs["HIT"], cs["MISS"], cs["COALESCED"], cs["BYPASS"]
    eligible = hits + misses + coal
    origin_fetches = misses + byp
    baseline = eligible + byp
    origin_bytes = sum(r.get("bytes") or 0 for r in records if r.get("cache_status") in ("MISS", "BYPASS"))
    if duration_s is None and records:
        start = min(r["ts"] - (r.get("latency_ms") or 0) / 1000 for r in records)
        duration_s = max(r["ts"] for r in records) - start
    per_edge: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        per_edge[r.get("edge_id") or "unrouted"].append(r)
    errors = Counter((r.get("error") or f"HTTP {r.get('status_code')}").split(":")[0]
                     for r in records if not r.get("ok"))
    edges = {}
    for eid, rs in sorted(per_edge.items()):
        e_cs = Counter(r.get("cache_status") for r in rs)
        e_elig = e_cs["HIT"] + e_cs["MISS"] + e_cs["COALESCED"]
        lat = latency_stats(r["latency_ms"] for r in rs if r.get("ok"))
        edges[eid] = {"requests": len(rs), "failed": sum(1 for r in rs if not r.get("ok")),
                      "share": len(rs) / total if total else None, "avg_ms": lat["avg_ms"], "p95_ms": lat["p95_ms"],
                      "hits": e_cs["HIT"], "hit_ratio": e_cs["HIT"] / e_elig if e_elig else None}
    return {
        "total_requests": total, "successful": len(ok), "failed": total - len(ok),
        "failure_rate": (total - len(ok)) / total if total else None,
        "duration_s": _r(duration_s, 3) if records else None,
        "throughput_rps": _r(len(ok) / duration_s) if records and duration_s and duration_s > 0 else None,
        "latency": latency_stats(r["latency_ms"] for r in ok),
        "resolve": latency_stats(r["resolve_ms"] for r in records if r.get("resolve_ms") is not None),
        "cache": {"hits": hits, "misses": misses, "coalesced": coal, "bypass": byp, "eligible_lookups": eligible,
                  "hit_ratio": _r(hits / eligible, 4) if eligible else None},
        "origin": {"fetches": origin_fetches, "bytes": origin_bytes, "baseline_fetches": baseline,
                   "reduction": _r(1 - origin_fetches / baseline, 4) if baseline else None},
        "bytes_served": sum(r.get("bytes") or 0 for r in ok),
        "per_edge": edges,
        "errors": dict(errors.most_common(10)),
    }


class LiveMetrics:
    """Bounded in-memory window of recent request records feeding the live dashboard."""

    def __init__(self, maxlen: int = 100_000):
        self.records: deque[dict] = deque(maxlen=maxlen)
        self.since = time.time()

    def reset(self) -> None:
        self.records.clear()
        self.since = time.time()

    def add(self, rec: dict) -> None:
        self.records.append(rec)

    def summary(self, window_s: float | None = None) -> dict:
        recs = list(self.records)
        if window_s:
            cutoff = time.time() - window_s
            recs = [r for r in recs if r["ts"] >= cutoff]
        s = summarize(recs)
        s["since"] = self.since
        return s

    def recent_errors(self, limit: int = 20) -> list[dict]:
        out = []
        for r in reversed(self.records):
            if not r.get("ok"):
                out.append({k: r.get(k) for k in ("ts", "object_id", "edge_id", "status_code", "error", "experiment_id")})
                if len(out) >= limit:
                    break
        return out

    def timeseries(self, window_s: int = 300, bucket_s: int = 1) -> list[dict]:
        now = time.time()
        start = math.floor((now - window_s) / bucket_s) * bucket_s
        buckets: dict[int, list[dict]] = defaultdict(list)
        for r in self.records:
            if r["ts"] >= start:
                buckets[int((r["ts"] - start) // bucket_s)].append(r)
        out = []
        for i in sorted(buckets):
            rs = buckets[i]
            ok = sorted(r["latency_ms"] for r in rs if r.get("ok"))
            cs = Counter(r.get("cache_status") for r in rs)
            edges = Counter(r.get("edge_id") or "unrouted" for r in rs)
            out.append({"ts": start + i * bucket_s, "requests": len(rs), "errors": len(rs) - len(ok),
                        "rps": len(ok) / bucket_s,
                        "avg_ms": _r(sum(ok) / len(ok)) if ok else None,
                        "p95_ms": _r(percentile(ok, 95)) if len(ok) >= 20 else None,
                        "hits": cs["HIT"], "misses": cs["MISS"] + cs["COALESCED"], "bypass": cs["BYPASS"],
                        "origin_fetches": cs["MISS"] + cs["BYPASS"], "per_edge": dict(edges)})
        return out
