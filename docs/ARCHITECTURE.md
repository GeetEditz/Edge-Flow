# Architecture

The simulator is split into a **data plane** (the processes that move content bytes) and a
**control plane** (the service that configures, observes and experiments on the data plane).
Every arrow below is a real socket; nothing is an in-memory function call pretending to be a network.

```
                         ┌──────────────────────── control plane (FastAPI, :8000) ────────────────────────┐
  React dashboard ──HTTP/WS──▶  REST API · WebSocket · experiment runner · traffic generator · SQLite       │
                         │     routing engine ◀── cluster manager (health probes, stats polling)          │
                         │          ▲                     │ TCP: GET /_health (data port), /stats (admin)  │
                         │   CNRP resolver (UDP :8053)    │                                                │
                         └──────────▲─────────────────────┼────────────────────────────────────────────────┘
                                    │ UDP query/answer    ▼
   client (traffic generator / cdn_client.py) ──HTTP/1.1 over TCP──▶ edge (india :9001 | singapore :9002 | usa :9003)
                                                                         │ cache miss: HTTP/1.1 over TCP
                                                                         ▼
                                                                   origin (:9000) ── content/ directory
```

| Component | Code | Process (Local Mode) | Ports |
|---|---|---|---|
| Origin server | `backend/app/origin/server.py` | own OS process | TCP 9000 |
| Edge servers ×3 | `backend/app/edge/server.py` | one OS process each | TCP 9001-9003 (data), 9101-9103 (admin) |
| CNRP resolver | `backend/app/networking/resolver.py` | inside control plane | UDP 8053 |
| Control plane | `backend/app/main.py`, `app/core/*`, `app/api/*` | uvicorn | TCP 8000 (REST + WebSocket) |
| Dashboard | `frontend/` | Vite dev server | TCP 5173 (proxies `/api`, `/ws` to 8000) |

All servers bind to `127.0.0.1` by default.

## Data plane

### HTTP/1.1 implementation (`app/networking/http.py`)

A deliberately small, strict HTTP/1.1 server and client on `asyncio` streams, so every byte on
the wire is produced by this project and is easy to inspect in Wireshark.

* Request head must end with CRLFCRLF within 16 KiB (`431` otherwise) and 10 s (`408`-style close).
* Request line: exactly three tokens, method in {GET, HEAD, POST, PUT, DELETE, OPTIONS}
  (unknown token → `405`, garbage → `400`), version `HTTP/1.0|1.1` (other `HTTP/x` → `505`),
  origin-form target only.
* Headers: token names, no obsolete line folding, `Host` required for 1.1, conflicting
  `Content-Length` → `400`, any `Transfer-Encoding` → `501`. Bodies only via `Content-Length`,
  read with `readexactly` (handles partial TCP reads), bounded per listener.
* Persistent connections (1.1 default, `Connection: close` honoured) with a 15 s idle timeout.
* Every response carries `Content-Length`; handler exceptions become `500` without tracebacks.
* `stop()` closes the listening socket, aborts open connections and cancels in-flight handlers:
  this is how an edge **crash** is simulated.

The client opens a fresh TCP connection per request (`Connection: close`), so each fetch shows a
complete handshake / request / response / teardown in a capture. Failures are classified as
`connect`, `timeout`, `reset` or `protocol`.

### Origin

Serves files from `backend/content/` (`GET /content/<object_id>`), a catalogue (`GET /catalog`),
counters (`GET /stats`) and a configurable response delay (`POST /config`). Object ids are validated
by `app/core/objects.py`: 1-5 path segments of `[A-Za-z0-9._-]`, no segment may start with `.`
(so `..`, `.` and hidden files are impossible) and `%` is not allowed (so encoded traversal is
rejected before any decoding). The resolved path is additionally checked with
`Path.resolve().is_relative_to(root)`.

The sample catalogue (32 objects, ≈ 9 MB) is generated deterministically by
`app/origin/content_gen.py`: valid PNG images (6 KB - 360 KB), real HTML/CSS/JS/JSON/SVG text, and
six opaque media segments of 256 KB - 2 MB.

### Edges

Each edge is an independent process with its own cache, counters and concurrency limit, and two
listeners:

* **data port** — `GET /content/<id>` and `GET /_health`. Responses carry
  `X-Cache: HIT|MISS|COALESCED|BYPASS`, `X-Edge-Id`, `X-Edge-Service-Ms`, `X-Sim-Delay-Ms`,
  `X-Sim-Lost-Segments`.
* **admin port** — `/stats`, `/cache`, `/cache/clear|invalidate|config|warm`, `/profile`, `/fail`,
  `/restore`, `/reset-counters`. The control plane manages edges only through this port, which
  keeps working while the data port is "crashed" so that the edge can be restored.

Request path inside an edge: validate id → wait for one of `concurrency_limit` slots (queueing is
real and visible as `queued_requests`) → cache lookup / coalesced origin fetch → simulated
transmission + loss penalty (slot still held) → release slot → simulated propagation delay →
write response.

### Simulated network profile (Local Mode)

`app/edge/profile.py`. Delays are applied by really sleeping before bytes are written, so the
client's measured latency includes them, but they are **simulated**, and labelled as such
everywhere in the API and UI.

```
propagation  = base_latency_ms ± uniform(jitter_ms)          (one RTT)
transmission = bytes × 8 / bandwidth
segments     = ceil(bytes / 1460) + 1
lost         ~ Binomial(segments, packet_loss)
penalty      = lost × max(2 × base_latency_ms, 10 ms)          (fast-retransmit-style recovery, serialised)
```

Default profiles: India 18 ± 3 ms / 50 Mbit/s / 0.2 % loss / 8 slots; Singapore 42 ± 5 ms /
200 Mbit/s / 0.1 % / 16 slots; USA 110 ± 8 ms / 400 Mbit/s / 0.4 % / 32 slots. The loss model is
pessimistic for large objects on lossy, long links (losses are recovered one after another).

In Mininet Mode the profile is all zeros and tc/netem shapes real links instead.

## Caching (`app/caching/`)

* `policies.py` — byte-capacity `LRUCache` (ordered dict, O(1)), `LFUCache` (frequency buckets,
  O(1), ties broken by least recent use) and `TTLCache` (fixed lifetime from insertion; under
  pressure evicts the soonest-to-expire entry). Shared invariants, enforced by an assertion and by
  property-style tests: `used_bytes <= capacity` at all times, objects larger than the capacity are
  rejected (served but not cached), expired entries are never returned and are purged before every
  insertion, every removal is reported with a reason (`capacity | expired | invalidated`).
* `edge_cache.py` — per-edge front end: policy selection (`lru | lfu | ttl | none`), counters,
  eviction log (last 200), warming and **request coalescing**: concurrent misses for the same object
  share one origin fetch through an in-flight `Future`; followers are counted as `COALESCED`.
* Concurrency safety: the cache structures contain no `await`, so on a single asyncio event loop
  each operation is atomic with respect to other coroutines. No threads touch them.
* A policy or capacity change restarts the cache empty (documented, and shown in the UI).

**Limitations:** in-memory only (restarting an edge empties it); TTL is a fixed lifetime rather
than HTTP `Cache-Control`-driven freshness; no revalidation (`ETag` is passed through but
conditional requests are not implemented).

## Routing (`app/routing/`)

Strategies receive only healthy candidates; health filtering happens in `RoutingEngine.select`.
If no edge is healthy the resolver answers `SERVFAIL`. Ties are broken by `(priority, edge_id)`.

| Strategy | Chooses | Input kind |
|---|---|---|
| `random` | uniform choice (seeded per experiment) | — |
| `nearest` | smallest configured `distance_km` | configured |
| `lowest_latency` | lowest probe-RTT EWMA; falls back to nearest if no samples | measured |
| `least_loaded` | lowest `max(edge active+queued, client in-flight) / concurrency_limit` | measured |
| `hybrid` | lowest weighted score (below) | measured + estimated |
| `failover` | highest-priority healthy edge (primary/backup) | configured + health |

Hybrid score:

```
score(e) = w_lat · norm(latency_ms) + w_load · norm(load) + w_loss · norm(packet_loss)
```

Weights must be ≥ 0 with a positive sum and are normalised to sum to 1 (defaults 0.5 / 0.4 / 0.1).
`norm` is min-max over the healthy candidates; a missing value counts as the worst (1.0); if all
known values are equal (including all zero) they normalise to 0 so that metric does not
discriminate. The packet-loss input is an **estimate**: an EWMA of the per-second ratio of simulated
lost/sent segments reported by each edge.

Every decision records the strategy, chosen edge, per-edge scores, excluded edges and a
human-readable reason (returned in CNRP answers when the EXPLAIN flag is set, and shown in the UI).

## Health checking (`app/core/cluster.py`)

* **Active:** `GET /_health` on each edge's data port every 0.5 s (timeout 0.8 s). 2 consecutive
  failures → unhealthy; 2 consecutive successes → healthy.
* **Passive:** 3 consecutive client transport failures (refused / reset / timeout) on an edge mark it
  unhealthy immediately.
* Detection time = unhealthy transition − failure injection; recovery time = healthy transition −
  restore request. Both are emitted as events and persisted.

Note: on Windows, connecting to a closed loopback port takes ≈ 2 s (the stack retries the SYN after
an RST), so probes against a crashed edge end by timeout, and DNS-style clients holding a cached
answer experience ~2 s connection failures. On Linux the refusal is immediate.

## CNRP — CDN Name Resolution Protocol v1 (UDP)

A DNS-*inspired* protocol. It is **not** RFC 1035 DNS. One query datagram, one answer datagram,
big-endian integers.

**Query** (client → resolver)

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic `CNRQ` |
| 4 | 1 | version = 1 |
| 5 | 1 | flags (bit 0 = EXPLAIN) |
| 6 | 2 | request id |
| 8 | 1 | hostname length N (1-253) |
| 9 | N | hostname, ASCII, case-insensitive |

**Answer** (resolver → client)

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic `CNRR` |
| 4 | 1 | version = 1 |
| 5 | 1 | rcode: 0 OK, 1 FORMERR, 2 SERVFAIL, 3 NXDOMAIN, 4 BADVERS |
| 6 | 2 | request id (echoed) |
| 8 | 4 | TTL seconds |
| 12 | 4 | edge IPv4 address |
| 16 | 2 | edge TCP port |
| 18 | 1 | edge id length M |
| 19 | M | edge id |
| 19+M | 2 | explanation length R |
| 21+M | R | explanation, UTF-8 (≤ 600 bytes) |

Names: `cdn.sim.local` is routed by the active strategy; `<edge>.cdn.sim.local`
(e.g. `india.cdn.sim.local`, `usa-edge.cdn.sim.local`) pins one edge (SERVFAIL if it is unhealthy);
anything else is NXDOMAIN. Datagrams without the `CNRQ` magic are silently dropped; datagrams with
the magic but a bad body get FORMERR (BADVERS for an unknown version). TTL defaults to 5 s.

**DNS-based routing vs. per-request routing.** In `dns` resolution mode the client caches the answer
for its TTL, exactly like a DNS stub resolver: routing happens at resolution time and every request
within the TTL goes to the same edge, even if conditions change (experiment C2 shows the effect
during a failure). `per_request` mode is an explicitly separate, non-DNS mode that resolves before
every request so the strategy is consulted each time; it is the default for routing experiments.

## Control plane

* `app/core/services.py` — composition root: launcher, cluster manager, routing engine, resolver,
  live metrics window, experiment runner, event bus, SQLite.
* `app/core/launcher.py` — `subprocess` (default; one OS process per server, children exit if the
  control plane dies), `inprocess` (tests) or `external` (Mininet). Refuses to start if a port is busy.
* `app/experiments/runner.py` — one experiment at a time; for every variant × repetition it restores
  edges, resets profiles to the experiment baseline, applies the variant's routing strategy and cache
  policy (which empties every cache), replays the same seeded request sequence, injects scheduled
  events, then persists records and the summary. Pre-experiment settings are restored afterwards.
* `app/traffic/generator.py` — seeded request sequences (uniform / random / Zipf), bounded
  concurrency, optional rate limit, cooperative stop. Runs on the event loop without blocking it.

## Metrics (`app/core/metrics.py`)

All request metrics are computed by the backend from client-side records of real requests.

| Metric | Definition |
|---|---|
| successful / failed | HTTP 200 / everything else (refused, reset, timeout, 4xx, 5xx, resolve failure) |
| latency | client wall-clock from TCP connect to last body byte (**measured**; includes simulated link delay in Local Mode) |
| avg, P50, P95, P99 | over successful requests; linear interpolation; P95 needs ≥ 20 samples, P99 ≥ 100, otherwise `null` |
| throughput | successful requests ÷ wall-clock span of the sample |
| eligible lookups | HIT + MISS + COALESCED (BYPASS excluded — no lookup happens with caching disabled) |
| hit ratio | HIT ÷ eligible lookups (`null` if none) |
| origin fetches | MISS + BYPASS (each caused exactly one origin request); cross-checked against the origin's own counter (`observed_at_origin`) |
| baseline origin fetches | requests answered by an edge: what the same workload would cost with no caching |
| origin requests avoided | 1 − origin fetches ÷ baseline |
| detection / recovery time | see Health checking |

Latency kinds are always labelled: **measured** (client timings, probe RTT), **simulated** (profile
values), **estimated** (packet-loss EWMA), **configured** (distance, priority).

## Persistence

SQLite (`data/cdn.sqlite3`, WAL mode, parameterised statements, writes off the event loop via
`asyncio.to_thread`). Experiments left `running` by a killed process are marked `interrupted` on start.

| Table | Contents | Indexes |
|---|---|---|
| `settings(key, value, updated_at)` | persisted routing strategy/weights and cache config | PK |
| `experiments(id, name, type, config, status, created_at, started_at, finished_at, error, summary)` | configuration (JSON) and aggregated summary (JSON) | `created_at` |
| `runs(id, experiment_id, variant, repetition, routing_strategy, cache_policy, started_at, finished_at, summary, events)` | one row per variant × repetition | `experiment_id` |
| `requests(id, experiment_id, run_id, ts, object_id, edge_id, status_code, ok, error, latency_ms, resolve_ms, bytes, cache_status, service_ms, sim_delay_ms)` | every client request | `run_id`, `(experiment_id, ts)` |
| `metric_snapshots(id, ts, payload)` | 5-second snapshots of the live window and fleet state (pruned after 7 days) | `ts` |
| `events(id, ts, kind, edge_id, detail)` | health transitions, failures, configuration changes | `ts` |

`POST /api/reset` deletes experiments, runs, requests, snapshots and events (settings are kept). No
secrets are stored.

## Security & resource bounds

Localhost binding by default; strict input validation (Pydantic on the API, object-id grammar,
CNRP parser); path-traversal protection; bounded request heads and bodies; timeouts on every socket
operation; bounded concurrency per edge and per traffic run; bounded in-memory windows, event logs
and WebSocket queues; no shell execution reachable from the API (subprocesses are spawned only by
the launcher with fixed argument lists); CORS restricted to the dashboard origin.
