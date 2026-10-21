# Experiments

All numbers on this page were **measured** on 2026-10-20 in Local Mode on the development machine
(Windows 11, Python 3.13, all processes on one host over loopback). Link conditions are the
**simulated** default profiles (India 18 ms / 50 Mbit/s / 0.2 % loss / 8 slots; Singapore 42 ms /
200 Mbit/s / 0.1 % / 16 slots; USA 110 ms / 400 Mbit/s / 0.4 % / 32 slots; origin delay 25 ms).
They show how the strategies behave *in this simulator*; they are not claims about real CDNs, and
absolute values depend on the machine. Re-run them yourself — every experiment is reproducible from
its stored configuration and seed.

Common conditions: Zipf popularity (s = 1.1) over the 32-object catalogue (≈ 9 MB), seed 7,
`per_request` resolution unless stated, caches emptied before every run, identical request sequence
for every variant.

## How to run

From the dashboard: **Routing Lab → Compare routing strategies**, **Cache Lab → Compare cache
policies**, or **Experiments → New experiment** (all types). From the API, e.g.:

```bash
curl -X POST http://127.0.0.1:8000/api/experiments -H "Content-Type: application/json" -d "{\"name\":\"A\",\"type\":\"routing_comparison\",\"repetitions\":3,\"workload\":{\"total_requests\":400,\"concurrency\":24,\"seed\":7},\"start\":true}"
```

Results: `GET /api/experiments/{id}`, CSV: `GET /api/experiments/{id}/export.csv?kind=runs|requests`.

## A. Routing comparison

400 requests per strategy, concurrency 24, LRU 4 MB per edge, **mean of 3 repetitions**.

| Strategy | Avg | P95 | P99 | Throughput | Hit ratio | Request share (India / Singapore / USA) |
|---|---|---|---|---|---|---|
| Random | 279.8 ms | 1118.2 ms | 1905.9 ms | 69.6 req/s | 56.4 % | 37 / 31 / 32 % |
| Nearest replica | 436.0 ms | 748.7 ms | 860.5 ms | 52.6 req/s | 57.0 % | 100 / 0 / 0 % |
| Lowest latency | 436.2 ms | 741.3 ms | 863.1 ms | 52.0 req/s | 59.3 % | 100 / 0 / 0 % |
| Least loaded | 258.5 ms | 1047.7 ms | 1764.9 ms | 72.3 req/s | 58.3 % | 12 / 59 / 29 % |
| **Hybrid (0.5/0.4/0.1)** | **146.9 ms** | **404.3 ms** | **511.7 ms** | **142.7 req/s** | 57.3 % | 30 / 70 / 0 % |

Observations:

* Nearest and lowest-latency send everything to India, the closest edge — but it has only 8 service
  slots and 50 Mbit/s, so at concurrency 24 requests queue there. Low network RTT did not mean low
  response time under load.
* Least-loaded spreads traffic but sends ~29 % to the 110 ms USA edge, which inflates the tail
  (P95 > 1 s). Random behaves similarly.
* Hybrid combined RTT and load: it kept traffic on the two nearby edges and roughly doubled
  throughput while having the lowest average and tail latency **in this workload**. This is not a
  general optimality result: with low concurrency (no queueing) nearest/lowest-latency are expected
  to match it, and different weights shift the balance.
* Hit ratios are similar (56-59 %) because every run starts cold; strategies that spread traffic
  warm several caches instead of one.

## B. Cache-policy comparison

400 requests per policy, concurrency 8, nearest routing (so one edge sees the whole stream),
**2 MB per edge** (smaller than the 9 MB catalogue), TTL 10 s, **mean of 3 repetitions**.

| Policy | Hit ratio | Origin fetches | Bytes from origin | Evictions | Avg | P95 |
|---|---|---|---|---|---|---|
| Disabled | — (no lookups) | 400 | 263.0 MB | 0 | 191.6 ms | 464.6 ms |
| LRU | 31.1 % | 246.3 | 116.1 MB | 242.7 | 180.3 ms | 465.7 ms |
| **LFU** | **32.8 %** | **241.0** | **114.0 MB** | 237.0 | **179.4 ms** | **454.1 ms** |
| TTL (10 s) | 30.8 % | 245.7 | 120.4 MB | 242.3 | 181.7 ms | 457.8 ms |

Observations:

* Any cache cut origin fetches by ~38-40 % and origin bytes by ~54-57 % versus no caching for this
  workload.
* LFU was marginally best, consistent with a skewed (Zipf) popularity where frequency predicts reuse;
  the gap between policies is small (a few fetches out of 400) because the 2 MB cache can hold only a
  handful of the large objects. The latency gain is modest because the India link's transmission
  time, not the origin, dominates large-object latency.
* With 4 MB (default) the hit ratio in experiment A was ~57 %, showing the capacity sensitivity.

## C. Server failure

600 requests at 60 req/s, concurrency 8, nearest routing (India is the primary). India's data
listener is closed at t = 3 s and reopened at t = 7 s.

| Variant | Detection | Detected by | Failed requests | Last failure after injection | Recovery (restore → healthy) | Failure rate |
|---|---|---|---|---|---|---|
| Per-request resolution | 2.1 ms | passive (3 client transport failures) | 8 | 5.2 ms | 882 ms | 1.33 % |
| DNS-style (TTL 5 s) | 5.3 ms | passive | 9 | 2043 ms | 1166 ms | 1.50 % |

Observations:

* The failures are the requests in flight when the edge "crashed" (connections reset). Their
  failures triggered passive detection within milliseconds, before the next active probe.
* With per-request resolution, the very next resolution excluded India; the first successful request
  on Singapore came ~80 ms after injection.
* With DNS-style caching, a client that held a cached India answer kept trying it; on Windows a
  refused loopback connection takes ~2 s, hence the last failure at ~2 s. This is the expected
  DNS-TTL behaviour (routing happens at resolution time).
* After restore, two consecutive successful probes (0.5 s interval) marked India healthy again.

## D. Network degradation

600 requests at 60 req/s, concurrency 8. At t = 3 s India's simulated link changes to 180 ms base
latency and 2 % loss.

| Strategy | Avg | P95 | P99 | Throughput | Failure rate | India share | Run length |
|---|---|---|---|---|---|---|---|
| Lowest latency | 187.0 ms | 419.6 ms | 583.1 ms | 42.0 req/s | 0 % | 26.7 % | 14 s |
| Nearest replica | 2199 ms | 9078 ms | 9797 ms | 2.7 req/s | 6.2 % (client timeouts) | 100 % | 206 s |

Lowest-latency moved traffic to Singapore within about 2 s (per-second timeline: India 54 requests
at t = 2 s, 14 at t = 3 s, 2 at t = 4 s, at most 1 afterwards) as the probe-RTT EWMA rose. Nearest
kept using the degraded link: with 2 % loss on a 180 ms path the loss model adds many lost-segment
penalties to multi-megabyte objects, responses took seconds, some exceeded the 10 s client timeout,
and the run took ~3.4 minutes instead of ~14 s. (The simulated loss model is deliberately pessimistic
for large objects; real TCP behaves badly under such conditions too, but not identically.)

> **Correction note.** A first run of D2 on the same day was invalid: the experiment runner read each
> edge's baseline profile from a stats cache that had not yet been refreshed after experiment D's
> restore, so D2 inherited D's degraded profile from t = 0 and did not restore it afterwards. The
> runner now refreshes edge stats before capturing the baseline and after restoring it (regression
> test `test_back_to_back_degradations_restore_original_profile`), and D and D2 were re-run.
> Experiments A-C2 were run before any profile change and were unaffected.

## Limitations of these measurements

* Single machine, loopback, simulated link conditions; the client, control plane and all servers
  compete for the same CPU.
* One workload family (Zipf over a 32-object catalogue); 3 repetitions for A and B, single runs for
  C and D.
* Mininet Mode was not executed in this environment (see `mininet/README.md`), so no results on
  real shaped links are reported.
