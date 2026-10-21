"""Real TCP/UDP tests against in-process servers on ephemeral loopback ports."""

import asyncio
import struct

import pytest

from app.edge.profile import NetworkProfile
from app.edge.server import EdgeServer
from app.networking.http import HTTPClientError, HTTPServer, Response, http_request, parse_head, HTTPError
from app.networking.resolver import (RCODE_BADVERS, RCODE_FORMERR, RCODE_NXDOMAIN, RCODE_OK, Answer, Query,
                                     ResolverClient, ResolverServer, decode_answer, decode_query, encode_answer,
                                     encode_query)
from app.origin.server import OriginServer


@pytest.fixture
async def stack(content_dir):
    origin = OriginServer(content_dir, port=0, delay_ms=0)
    await origin.start()
    edge = EdgeServer("e1", origin_port=origin.port, profile=NetworkProfile(1, 0, 1000, 0),
                      cache_capacity_bytes=4 * 1024 * 1024, shared_process=True)
    await edge.start()
    yield origin, edge
    await edge.stop()
    await origin.stop()


async def raw(port: int, data: bytes, timeout: float = 3.0) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(data)
    await w.drain()
    out = await asyncio.wait_for(r.read(65536), timeout)
    w.close()
    return out


# ------------------------------------------------------------------ HTTP

async def test_object_retrieval_miss_then_hit_and_origin_fetched_once(stack, content_dir):
    origin, edge = stack
    expected = (content_dir / "images/photo-03.png").read_bytes()
    r1 = await http_request("127.0.0.1", edge.data.port, "GET", "/content/images/photo-03.png")
    r2 = await http_request("127.0.0.1", edge.data.port, "GET", "/content/images/photo-03.png")
    assert r1.status == r2.status == 200
    assert r1.body == r2.body == expected
    assert int(r1.headers["content-length"]) == len(expected)
    assert r1.headers["content-type"] == "image/png"
    assert (r1.headers["x-cache"], r2.headers["x-cache"]) == ("MISS", "HIT")
    assert origin.content_requests == 1 and origin.fetches_by_edge["e1"] == 1


async def test_missing_object_returns_404(stack):
    _, edge = stack
    r = await http_request("127.0.0.1", edge.data.port, "GET", "/content/nope/missing.bin")
    assert r.status == 404 and r.json()["error"] == "Not Found"


@pytest.mark.parametrize("path", ["/content/../app/main.py", "/content/%2e%2e/secret", "/content/.hidden",
                                  "/content/a//b", "/content/a\\b"])
async def test_invalid_object_ids_rejected(stack, path):
    origin, edge = stack
    out = await raw(edge.data.port, f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    assert out.startswith(b"HTTP/1.1 400")
    out = await raw(origin.port, f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    assert out.startswith(b"HTTP/1.1 400")


@pytest.mark.parametrize("payload,status", [
    (b"GARBAGE\r\n\r\n", b"400"),
    (b"GET /content/index.html HTTP/2.0\r\nHost: x\r\n\r\n", b"505"),
    (b"GET /content/index.html HTTP/1.1\r\n\r\n", b"400"),  # missing Host
    (b"GET /content/index.html HTTP/1.1\r\nHost: x\r\n folded\r\n\r\n", b"400"),
    (b"GET /x HTTP/1.1\r\nHost: x\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n", b"400"),
    (b"POST /content/x HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n", b"501"),
    (b"GET /content/index.html HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n", b"400"),
    (b"BREW /content/index.html HTTP/1.1\r\nHost: x\r\n\r\n", b"405"),
])
async def test_malformed_requests(stack, payload, status):
    _, edge = stack
    out = await raw(edge.data.port, payload)
    assert out.split(b" ")[1] == status


async def test_oversized_head_rejected_with_431(stack):
    _, edge = stack
    out = await raw(edge.data.port, b"GET / HTTP/1.1\r\nHost: x\r\nX-Big: " + b"a" * 20000 + b"\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 431")


async def test_partial_reads_and_keep_alive_pipelining(stack):
    """Request head split across TCP segments, then two requests on one persistent connection."""
    _, edge = stack
    r, w = await asyncio.open_connection("127.0.0.1", edge.data.port)
    for piece in (b"GET /content/docs/rea", b"dme.txt HTTP/1.1\r\nHo", b"st: x\r\n", b"\r\n"):
        w.write(piece)
        await w.drain()
        await asyncio.sleep(0.02)
    head = await r.readuntil(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200") and b"Connection: keep-alive" in head
    n = int(next(l for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")).split(b":")[1])
    await r.readexactly(n)
    w.write(b"GET /content/docs/readme.txt HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    await w.drain()
    rest = await r.read()
    assert rest.startswith(b"HTTP/1.1 200") and b"X-Cache: HIT" in rest
    w.close()


async def test_concurrent_requests_coalesce_at_edge(stack):
    origin, edge = stack
    origin.delay_ms = 50
    rs = await asyncio.gather(*(http_request("127.0.0.1", edge.data.port, "GET", "/content/media/segment-01.bin")
                                for _ in range(10)))
    assert all(r.status == 200 and len(r.body) == 256 * 1024 for r in rs)
    assert origin.content_requests == 1
    assert sorted(r.headers["x-cache"] for r in rs).count("MISS") == 1


async def test_client_timeout_and_connection_refused():
    async def slow(_req):
        await asyncio.sleep(5)
        return Response.json({})

    srv = HTTPServer(slow, "127.0.0.1", 0)
    await srv.start()
    with pytest.raises(HTTPClientError) as ei:
        await http_request("127.0.0.1", srv.port, "GET", "/", timeout=0.3)
    assert ei.value.kind == "timeout"
    port = srv.port
    await srv.stop()
    with pytest.raises(HTTPClientError) as ei:
        await http_request("127.0.0.1", port, "GET", "/", timeout=5)
    assert ei.value.kind in ("connect", "timeout")


async def test_edge_crash_and_restore(stack):
    _, edge = stack
    port = edge.data.port
    r = await http_request("127.0.0.1", edge.admin.port, "POST", "/fail")
    assert r.status == 200 and edge.failed
    with pytest.raises(HTTPClientError):
        await http_request("127.0.0.1", port, "GET", "/_health", timeout=3)
    await http_request("127.0.0.1", edge.admin.port, "POST", "/restore")
    r = await http_request("127.0.0.1", port, "GET", "/_health")
    assert r.status == 200


async def test_origin_unavailable_gives_502(content_dir):
    edge = EdgeServer("lonely", origin_port=1, profile=NetworkProfile(0, 0, 1000, 0), shared_process=True)
    await edge.start()
    try:
        r = await http_request("127.0.0.1", edge.data.port, "GET", "/content/index.html", timeout=15)
        assert r.status == 502 and edge.errors_5xx == 1
    finally:
        await edge.stop()


def test_parse_head_lowercases_headers():
    m, t, v, h = parse_head(b"GET /a?b=1 HTTP/1.1\r\nHost: x\r\nX-Foo: Bar")
    assert (m, t, v, h["x-foo"]) == ("GET", "/a?b=1", "HTTP/1.1", "Bar")
    with pytest.raises(HTTPError):
        parse_head(b"GET a HTTP/1.1\r\nHost: x")


# ------------------------------------------------------------------ UDP resolver

def test_protocol_roundtrip():
    q = decode_query(encode_query(Query(513, "CDN.sim.local", 1)))
    assert (q.request_id, q.hostname, q.flags) == (513, "cdn.sim.local", 1)
    a = decode_answer(encode_answer(Answer(9, RCODE_OK, 5, "10.0.0.3", 9003, "usa-edge", "because")))
    assert (a.request_id, a.ttl, a.address, a.port, a.edge_id, a.explanation) == (9, 5, "10.0.0.3", 9003, "usa-edge", "because")


@pytest.fixture
async def resolver():
    def resolve(host, explain):
        if host == "cdn.sim.local":
            return Answer(0, RCODE_OK, 5, "127.0.0.1", 9001, "india-edge", "test reason")
        return Answer(0, RCODE_NXDOMAIN, explanation="unknown")

    srv = ResolverServer(resolve, port=0)
    await srv.start()
    cli = await ResolverClient("127.0.0.1", srv.port, timeout=0.3, retries=0).open()
    yield srv, cli
    cli.close()
    srv.stop()


async def test_udp_resolution_success(resolver):
    srv, cli = resolver
    ans, rtt = await cli.resolve("cdn.sim.local", explain=True)
    assert ans.ok and ans.port == 9001 and ans.edge_id == "india-edge" and ans.ttl == 5
    assert ans.explanation == "test reason" and rtt >= 0
    ans2, _ = await cli.resolve("cdn.sim.local")
    assert ans2.explanation == ""  # explanation only when the EXPLAIN flag is set
    assert srv.stats["answered"] == 2


async def test_udp_unknown_hostname_nxdomain(resolver):
    _, cli = resolver
    ans, _ = await cli.resolve("nothing.example")
    assert ans.rcode == RCODE_NXDOMAIN


async def udp_exchange(port: int, data: bytes, timeout: float = 0.5) -> bytes | None:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    class P(asyncio.DatagramProtocol):
        def datagram_received(self, d, a):
            if not fut.done():
                fut.set_result(d)

    tr, _ = await loop.create_datagram_endpoint(P, remote_addr=("127.0.0.1", port))
    tr.sendto(data)
    try:
        return await asyncio.wait_for(fut, timeout)
    except TimeoutError:
        return None
    finally:
        tr.close()


async def test_udp_malformed_requests(resolver):
    srv, _ = resolver
    # right magic, truncated name -> FORMERR echoing the request id
    bad = b"CNRQ" + struct.pack("!BBHB", 1, 0, 777, 20) + b"short"
    a = decode_answer(await udp_exchange(srv.port, bad))
    assert a.rcode == RCODE_FORMERR and a.request_id == 777
    # invalid hostname characters -> FORMERR
    bad = b"CNRQ" + struct.pack("!BBHB", 1, 0, 5, 4) + b"a b!"
    assert decode_answer(await udp_exchange(srv.port, bad)).rcode == RCODE_FORMERR
    # wrong version -> BADVERS
    bad = b"CNRQ" + struct.pack("!BBHB", 9, 0, 6, 3) + b"abc"
    assert decode_answer(await udp_exchange(srv.port, bad)).rcode == RCODE_BADVERS
    # garbage without magic -> silently dropped
    assert await udp_exchange(srv.port, b"hello world") is None
    assert srv.stats["dropped"] == 1 and srv.stats["formerr"] == 3


async def test_udp_client_times_out_without_server():
    cli = await ResolverClient("127.0.0.1", 9, timeout=0.2, retries=1).open()
    with pytest.raises(TimeoutError):
        await cli.resolve("cdn.sim.local")
    cli.close()
