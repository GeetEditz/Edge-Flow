"""CDN Name Resolution Protocol (CNRP) v1 — a small DNS-inspired protocol over UDP.

This is NOT standards-compliant DNS (RFC 1035). It borrows DNS ideas (request IDs, response
codes, TTLs, one datagram per query) with a simpler fixed binary layout. All integers are
big-endian (network byte order). See docs/ARCHITECTURE.md for the full specification.

Query (client -> resolver):
    0   4  magic  b"CNRQ"
    4   1  version (1)
    5   1  flags   (bit0: EXPLAIN — include the routing explanation in the reply)
    6   2  request id
    8   1  hostname length N (1..253)
    9   N  hostname (ASCII, case-insensitive)

Response (resolver -> client):
    0   4  magic  b"CNRR"
    4   1  version (1)
    5   1  rcode   (0 OK, 1 FORMERR, 2 SERVFAIL, 3 NXDOMAIN, 4 BADVERS)
    6   2  request id (echoed; 0 if it could not be parsed)
    8   4  TTL seconds (uint32)
    12  4  edge IPv4 address
    16  2  edge TCP port
    18  1  edge id length M
    19  M  edge id (ASCII)
    ..  2  explanation length R
    ..  R  explanation (UTF-8)

Datagrams that do not start with b"CNRQ" are silently dropped (as a DNS server ignores
garbage); anything with the right magic but an invalid body gets FORMERR.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import logging
import re
import struct
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("cdn.resolver")

Q_MAGIC, R_MAGIC, VERSION = b"CNRQ", b"CNRR", 1
FLAG_EXPLAIN = 0x01
RCODE_OK, RCODE_FORMERR, RCODE_SERVFAIL, RCODE_NXDOMAIN, RCODE_BADVERS = range(5)
RCODE_NAMES = {0: "OK", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "BADVERS"}
MAX_DATAGRAM = 1024
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


class ProtocolError(ValueError):
    pass


@dataclass
class Query:
    request_id: int
    hostname: str
    flags: int = 0


@dataclass
class Answer:
    request_id: int
    rcode: int
    ttl: int = 0
    address: str = "0.0.0.0"
    port: int = 0
    edge_id: str = ""
    explanation: str = ""

    @property
    def ok(self) -> bool:
        return self.rcode == RCODE_OK


def encode_query(q: Query) -> bytes:
    name = q.hostname.encode("ascii")
    if not 1 <= len(name) <= 253:
        raise ProtocolError("hostname length out of range")
    return Q_MAGIC + struct.pack("!BBHB", VERSION, q.flags, q.request_id, len(name)) + name


def decode_query(data: bytes) -> Query:
    if len(data) < 9 or data[:4] != Q_MAGIC:
        raise ProtocolError("short datagram or bad magic")
    version, flags, rid, n = struct.unpack("!BBHB", data[4:9])
    if version != VERSION:
        raise ProtocolError(f"unsupported version {version}")
    if n == 0 or len(data) != 9 + n:
        raise ProtocolError("hostname length does not match datagram size")
    try:
        host = data[9:9 + n].decode("ascii").lower().rstrip(".")
    except UnicodeDecodeError as exc:
        raise ProtocolError("hostname is not ASCII") from exc
    if not _HOST_RE.match(host):
        raise ProtocolError("invalid hostname syntax")
    return Query(rid, host, flags)


def encode_answer(a: Answer) -> bytes:
    eid = a.edge_id.encode("ascii")
    expl = a.explanation.encode("utf-8")[:600]
    return (R_MAGIC + struct.pack("!BBHI", VERSION, a.rcode, a.request_id, a.ttl)
            + ipaddress.IPv4Address(a.address).packed + struct.pack("!HB", a.port, len(eid)) + eid
            + struct.pack("!H", len(expl)) + expl)


def decode_answer(data: bytes) -> Answer:
    if len(data) < 21 or data[:4] != R_MAGIC:
        raise ProtocolError("short datagram or bad magic")
    version, rcode, rid, ttl = struct.unpack("!BBHI", data[4:12])
    if version != VERSION:
        raise ProtocolError(f"unsupported version {version}")
    addr = str(ipaddress.IPv4Address(data[12:16]))
    port, m = struct.unpack("!HB", data[16:19])
    pos = 19 + m
    if len(data) < pos + 2:
        raise ProtocolError("truncated answer")
    eid = data[19:pos].decode("ascii")
    (r,) = struct.unpack("!H", data[pos:pos + 2])
    expl = data[pos + 2:pos + 2 + r].decode("utf-8", errors="replace")
    return Answer(rid, rcode, ttl, addr, port, eid, expl)


# A resolve function maps (hostname, explain) -> Answer without request_id filled in.
ResolveFn = Callable[[str, bool], Answer]


class ResolverServer(asyncio.DatagramProtocol):
    """UDP listener. The routing decision itself is delegated to resolve_fn."""

    def __init__(self, resolve_fn: ResolveFn, host: str = "127.0.0.1", port: int = 8053):
        self.resolve_fn, self.host, self.port = resolve_fn, host, port
        self.transport: asyncio.DatagramTransport | None = None
        self.stats = {"queries": 0, "answered": 0, "formerr": 0, "nxdomain": 0, "servfail": 0, "dropped": 0}

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(self.host, self.port))
        self.port = self.transport.get_extra_info("sockname")[1]
        log.info("resolver listening on udp %s:%s", self.host, self.port)

    def stop(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.stats["queries"] += 1
        if len(data) > MAX_DATAGRAM or data[:4] != Q_MAGIC:
            self.stats["dropped"] += 1
            return
        try:
            q = decode_query(data)
        except ProtocolError as exc:
            rid = struct.unpack("!H", data[6:8])[0] if len(data) >= 8 else 0
            bad_version = len(data) >= 5 and data[4] != VERSION
            self.stats["formerr"] += 1
            self._reply(Answer(rid, RCODE_BADVERS if bad_version else RCODE_FORMERR, explanation=str(exc)), addr)
            return
        try:
            ans = self.resolve_fn(q.hostname, bool(q.flags & FLAG_EXPLAIN))
        except Exception:  # noqa: BLE001
            log.exception("resolve_fn failed")
            ans = Answer(0, RCODE_SERVFAIL, explanation="internal resolver error")
        ans.request_id = q.request_id
        if not q.flags & FLAG_EXPLAIN and ans.ok:
            ans.explanation = ""
        key = {RCODE_OK: "answered", RCODE_NXDOMAIN: "nxdomain"}.get(ans.rcode, "servfail")
        self.stats[key] += 1
        self._reply(ans, addr)

    def _reply(self, ans: Answer, addr) -> None:  # noqa: ANN001
        if self.transport:
            self.transport.sendto(encode_answer(ans), addr)


class ResolverClient(asyncio.DatagramProtocol):
    """Stub resolver: one UDP socket, many concurrent queries matched by request id."""

    def __init__(self, host: str, port: int, timeout: float = 1.0, retries: int = 1):
        self.host, self.port, self.timeout, self.retries = host, port, timeout, retries
        self.transport: asyncio.DatagramTransport | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.cycle(range(1, 65536))

    async def open(self) -> "ResolverClient":
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self, remote_addr=(self.host, self.port))
        return self

    def close(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None
        for f in self._pending.values():
            if not f.done():
                f.cancel()

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        try:
            ans = decode_answer(data)
        except (ProtocolError, ValueError, UnicodeDecodeError):
            return
        fut = self._pending.get(ans.request_id)
        if fut and not fut.done():
            fut.set_result(ans)

    def error_received(self, exc: Exception) -> None:
        # e.g. ICMP port unreachable on Windows; pending queries simply time out.
        log.debug("resolver client socket error: %s", exc)

    async def resolve(self, hostname: str, explain: bool = False) -> tuple[Answer, float]:
        """Return (answer, round-trip ms). Raises TimeoutError after all retries."""
        if not self.transport:
            await self.open()
        for _ in range(self.retries + 1):
            rid = next(self._ids)
            while rid in self._pending:
                rid = next(self._ids)
            fut = asyncio.get_running_loop().create_future()
            self._pending[rid] = fut
            t0 = time.perf_counter()
            try:
                self.transport.sendto(encode_query(Query(rid, hostname, FLAG_EXPLAIN if explain else 0)))
                ans = await asyncio.wait_for(fut, self.timeout)
                return ans, (time.perf_counter() - t0) * 1000
            except TimeoutError:
                continue
            finally:
                self._pending.pop(rid, None)
        raise TimeoutError(f"no CNRP answer for {hostname} from {self.host}:{self.port}")
