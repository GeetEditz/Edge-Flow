# Wireshark verification guide

> These instructions are based on the protocol implementation and the ports configured in this
> project. **No packet capture was performed or inspected during development** (Wireshark is not
> installed on the development machine). Follow the steps to verify the behaviour yourself.

## Ports used

| Traffic | Protocol | Port(s) (Local Mode, 127.0.0.1) |
|---|---|---|
| CNRP resolver queries/answers | UDP | 8053 |
| Client → edge content | HTTP/1.1 over TCP | 9001 (India), 9002 (Singapore), 9003 (USA) |
| Edge → origin cache fills | HTTP/1.1 over TCP | 9000 |
| Control plane → edge admin | HTTP/1.1 over TCP | 9101-9103 |
| Health probes | HTTP/1.1 over TCP | 9001-9003 (`GET /_health`) |
| Dashboard / API | HTTP + WebSocket | 8000 (5173 for the Vite dev server) |

In Mininet Mode the same ports are used on 10.0.0.1 (client/control plane), 10.0.0.2 (origin) and
10.0.0.11-13 (edges).

## Setup

* **Windows:** install Wireshark with **Npcap** and tick *"Support loopback traffic"*. Capture on
  the **Adapter for loopback traffic capture** interface.
* **Linux:** capture on `lo` (Local Mode) or on a Mininet interface such as `india-eth0`
  (`sudo wireshark &` from the VM, or `india tcpdump -i india-eth0 -w india.pcap` in the Mininet CLI).
* Wireshark dissects HTTP only on well-known ports. Add ours: *Edit → Preferences → Protocols →
  HTTP → TCP port(s)* = `80,8000,9000-9003,9101-9103` (or right-click a packet → *Decode As… → HTTP*).

Capture filter (keeps the capture small and skips the noisy dashboard/admin traffic):

```
udp port 8053 or tcp portrange 9000-9003
```

## 1. UDP resolution (CNRP)

Display filter: `udp.port == 8053`

* Each query is one datagram starting with ASCII `CNRQ`; the answer starts with `CNRR`.
  Show only queries: `udp.port == 8053 && data.data[0:4] == 43:4e:52:51`.
* Bytes 6-7 of query and answer carry the same request id; bytes 12-17 of the answer are the edge's
  IPv4 address and TCP port (e.g. `7f 00 00 01 23 29` = 127.0.0.1:9001).
* Generate traffic: `python scripts/cdn_client.py index.html --repeat 3` (from `backend/`).
* Unknown names (`--hostname bogus.example`) produce an answer with rcode byte (offset 5) = `03`
  (NXDOMAIN).

## 2. TCP handshake and HTTP exchange to an edge

Display filter: `tcp.port == 9001`

The client uses one connection per request (`Connection: close`), so each fetch shows:
`SYN → SYN/ACK → ACK`, `GET /content/<id> HTTP/1.1`, the response headers + body
(`Content-Length` framed), then `FIN` from both sides. Useful filters:

```
tcp.flags.syn == 1 && tcp.flags.ack == 0 && tcp.dstport in {9001 9002 9003}   # new connections per edge
http.request.uri contains "/content/"
http.response && tcp.srcport in {9001 9002 9003}
```

The `X-Edge-Id` header identifies the serving edge; *Statistics → Conversations → TCP* shows how
requests were distributed across edges for the selected routing strategy.

## 3. Cache miss vs. cache hit

1. Clear a cache (dashboard *Cache Lab → Clear*, or `POST /api/cache/india-edge/clear`).
2. Start capturing with `udp port 8053 or tcp portrange 9000-9003`.
3. Request the same object twice through a pinned edge:
   `python scripts/cdn_client.py images/photo-05.png --hostname india.cdn.sim.local --repeat 2`

Expected:

* **First request (MISS):** client → 9001 `GET /content/images/photo-05.png`, then — before the edge
  answers — a new TCP connection from the edge to **port 9000** with the same `GET` and an
  `X-Edge-Id: india-edge` request header; the client's response carries `X-Cache: MISS`.
* **Second request (HIT):** client → 9001 `GET`, response `X-Cache: HIT`, and **no packets to port
  9000** between that request and its response. Verify with the filter
  `tcp.port == 9000 && http.request` — it should list exactly one request for the object.

Header filters (with HTTP decoding enabled):
`http.response && http contains "X-Cache: HIT"` and `http.response && http contains "X-Cache: MISS"`.

## 4. Latency changes

* *Statistics → Conversations* or the `tcp.time_delta` / `http.time` fields show request-to-response
  time. In Local Mode the edge sleeps for its simulated propagation delay before writing the response,
  so `http.time` for India (≈ 18 ms base) is visibly smaller than for USA (≈ 110 ms).
* Change a profile (*Edge Servers → Network profile*, or
  `POST /api/servers/india-edge/profile {"base_latency_ms": 150}`) and observe `http.time` for
  `GET /_health` probes on port 9001 rise immediately.
* In Mininet Mode the delay is real: the TCP handshake RTT (`tcp.analysis.ack_rtt` of the SYN/ACK)
  reflects the netem delay of the link, and retransmissions (`tcp.analysis.retransmission`) appear
  when loss is configured. In Local Mode there are no real retransmissions — simulated loss only adds
  delay (`X-Sim-Lost-Segments` header).

## 5. Failure

Simulate a failure of India (dashboard or `POST /api/servers/india-edge/simulate-failure`) with
`tcp.port == 9001` displayed: in-flight connections end with `RST`, new connection attempts receive
`RST, ACK` (connection refused), and subsequent CNRP answers (`udp.port == 8053`) carry
port `23 2a` (9002) instead of `23 29` (9001) once the edge is marked unhealthy.
