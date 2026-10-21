# Control-plane API

Base URL: `http://127.0.0.1:8000`. Interactive OpenAPI docs: `http://127.0.0.1:8000/docs`.
All bodies are JSON. Errors always have the shape:

```json
{ "error": "validation_error | bad_request | not_found | conflict | edge_error", "detail": "human-readable message" }
```

Status codes: `400` bad input, `404` unknown server/experiment, `409` conflicting state (e.g. an
experiment is running), `422` schema validation, `502` an edge/origin admin call failed.

## System

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Control-plane status, mode, per-edge health, resolver address |
| GET | `/api/config` | Mode, hostname, resolver TTL, health thresholds, routing + cache config, strategy list |
| GET | `/api/events?limit=100` | Recent events (health transitions, config changes, experiments) |
| POST | `/api/reset` | Delete all experiments, requests, snapshots, events; reset edge/origin counters (409 while running) |

## Servers

| Method | Path | Description |
|---|---|---|
| GET | `/api/servers` | All edges (health, measured RTT, load, profile, cache stats, CPU/memory) + origin |
| GET | `/api/servers/{server_id}` | One edge |
| POST | `/api/servers/{server_id}/simulate-failure` | Close the edge's data listener (crash) |
| POST | `/api/servers/{server_id}/restore` | Re-open the data listener |
| POST | `/api/servers/{server_id}/profile` | `{base_latency_ms?, jitter_ms?, bandwidth_mbps?, packet_loss?, concurrency_limit?}` |
| GET | `/api/origin` | Origin status and counters |
| POST | `/api/origin/config` | `{delay_ms}` (0-5000) |
| GET | `/api/content` | Origin catalogue |

## Metrics

| Method | Path | Description |
|---|---|---|
| GET | `/api/metrics/summary` | Live window summary; `?window_s=` limits it; `?experiment_id=` summarises stored records |
| GET | `/api/metrics/timeseries?window_s=300&bucket_s=1` | Per-bucket requests, errors, avg/P95, hits, misses, origin fetches, per-edge counts; `source=snapshots` returns persisted snapshots |
| GET | `/api/metrics/errors?limit=20` | Recent failed requests |
| POST | `/api/metrics/reset` | Clear the in-memory live window |

Summary fields: `total_requests, successful, failed, failure_rate, duration_s, throughput_rps,
latency{samples, avg_ms, p50_ms, p95_ms, p99_ms, min_ms, max_ms}, resolve{…}, cache{hits, misses,
coalesced, bypass, eligible_lookups, hit_ratio}, origin{fetches, bytes, baseline_fetches, reduction},
bytes_served, per_edge{…}, errors{…}`. Unavailable values are `null`, never invented.

## Routing

| Method | Path | Description |
|---|---|---|
| GET | `/api/routing` | Active strategy, hybrid weights, strategy list, decision counts |
| POST | `/api/routing/strategy` | `{strategy, weights?: {latency_weight, load_weight, packet_loss_weight}}` (409 while an experiment runs) |
| GET | `/api/routing/decisions?limit=50` | Recent resolver decisions with reasons and scores |
| GET | `/api/routing/preview` | Evaluate every strategy against current fleet state (not recorded) |
| POST | `/api/requests/probe` | `{object_id, hostname?}` — resolve over UDP + fetch over HTTP like a client; recorded in live metrics |

## Cache

| Method | Path | Description |
|---|---|---|
| GET | `/api/cache/stats` | Config, per-edge cache stats, fleet totals |
| POST | `/api/cache/policy` | `{policy: lru\|lfu\|ttl\|none, capacity_bytes, ttl_seconds?, edge_ids?}` — caches restart empty |
| GET | `/api/cache/{server_id}` | Entries (size, accesses, age, idle, TTL left), stats, eviction history |
| POST | `/api/cache/{server_id}/clear` | Remove all entries |
| POST | `/api/cache/{server_id}/invalidate` | `{object_id}` |
| POST | `/api/cache/{server_id}/warm` | `{object_ids: [...]}` (empty = first 10 catalogue objects) |

## Experiments

| Method | Path | Description |
|---|---|---|
| GET | `/api/experiments` | All experiments (+ `current` progress) |
| POST | `/api/experiments` | Create (see body below); `"start": true` starts immediately |
| GET | `/api/experiments/{id}` | Experiment with runs and progress |
| POST | `/api/experiments/{id}/start` | Start or re-run (409 if another is running) |
| POST | `/api/experiments/{id}/stop` | Stop after in-flight requests (409 if not running) |
| DELETE | `/api/experiments/{id}` | Delete with its runs and requests |
| GET | `/api/experiments/{id}/export.csv?kind=runs\|requests` | CSV export |
| GET | `/api/experiments/export/all.csv` | One row per variant of every experiment |

Create body:

```json
{
  "name": "Routing comparison",
  "type": "single | routing_comparison | cache_comparison | failure | degradation",
  "workload": {
    "total_requests": 300, "concurrency": 16, "rate_rps": 0,
    "distribution": "zipf | uniform | random", "zipf_s": 1.1, "seed": 42,
    "objects": [], "resolution": "per_request | dns", "timeout_s": 10
  },
  "routing_strategy": "hybrid", "weights": null,
  "cache_policy": "lru", "cache_capacity_bytes": 4194304, "cache_ttl_seconds": 30,
  "strategies": ["random", "nearest", "lowest_latency", "least_loaded", "hybrid"],
  "policies": ["none", "lru", "lfu", "ttl"],
  "repetitions": 1,
  "failure": {"edge_id": "india-edge", "at_s": 3, "restore_at_s": 8},
  "degradation": {"edge_id": "india-edge", "at_s": 3, "profile": {"base_latency_ms": 180, "packet_loss": 0.02}},
  "start": false
}
```

## Topology

`GET /api/topology` — nodes (client, resolver, control plane, origin, edges) and links with protocol,
simulated profile, measured RTT, status and 60-second traffic share. `kind` is `conceptual-local`
(Local Mode) or `running-mininet`.

## WebSocket `/ws/metrics`

Server → client JSON text frames:

```json
{"type": "hello",    "ts": 1792511303.1, "interval_s": 1.0}
{"type": "snapshot", "ts": 1792511304.1, "data": {
    "summary": {…live-window summary…}, "recent": {…last 10 s summary…},
    "servers": [{"edge_id", "status", "load", "active_requests", "latency_ms", "packet_loss_est",
                 "cache_used_bytes", "cache_hit_ratio", "requests"}],
    "origin": {"status", "content_requests", "bytes_served"},
    "experiment": {…progress or null…}, "routing": {"strategy", "weights"}},
  "point": {…latest complete 1-second timeseries bucket or null…}}
{"type": "event",    "ts": 1792511305.2, "event": {"ts", "kind", "edge_id", "detail"}}
{"type": "pong",     "ts": …}
```

Snapshots are pushed once per second, events as they occur. Each connection has a bounded queue
(100 events; excess events are dropped rather than buffered without limit). Send the text `ping` to
receive a `pong`. The dashboard reconnects with exponential backoff (0.5 s → 10 s) and falls back to
REST polling while disconnected.

## Edge admin API (internal, ports 9101-9103)

Used by the control plane only: `GET /stats`, `GET /cache`, `POST /cache/clear`,
`POST /cache/invalidate`, `POST /cache/config`, `POST /cache/warm`, `POST /profile`, `POST /fail`,
`POST /restore`, `POST /reset-counters`.
