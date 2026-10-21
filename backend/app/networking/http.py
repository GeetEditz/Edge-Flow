"""Strict, minimal HTTP/1.1 server and client built directly on asyncio TCP streams.

Why custom: the data plane must be inspectable at the packet level (see docs/WIRESHARK.md)
and every byte on the wire is produced here. The parser is deliberately conservative:

* request head must fit in MAX_HEAD_BYTES and arrive within HEAD_TIMEOUT_S
* only Content-Length framed bodies are accepted (Transfer-Encoding -> 501)
* obsolete line folding, malformed header names and conflicting Content-Length -> 400
* persistent connections are supported (HTTP/1.1 default) with an idle timeout
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from email.utils import formatdate
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl

log = logging.getLogger("cdn.http")

MAX_HEAD_BYTES = 16 * 1024
MAX_BODY_BYTES = 256 * 1024
HEAD_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 15.0
BODY_TIMEOUT_S = 15.0

_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_METHODS = {"GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"}

REASONS = {
    200: "OK", 204: "No Content", 400: "Bad Request", 404: "Not Found",
    405: "Method Not Allowed", 408: "Request Timeout", 409: "Conflict",
    413: "Payload Too Large", 431: "Request Header Fields Too Large",
    500: "Internal Server Error", 501: "Not Implemented", 502: "Bad Gateway",
    503: "Service Unavailable", 504: "Gateway Timeout", 505: "HTTP Version Not Supported",
}


class HTTPError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(message or REASONS.get(status, "Error"))
        self.status = status
        self.message = message or REASONS.get(status, "Error")


@dataclass
class Request:
    method: str
    target: str
    path: str
    query: dict[str, str]
    version: str
    headers: dict[str, str]
    body: bytes = b""
    peer: str = ""

    def json(self) -> Any:
        if not self.body:
            return {}
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPError(400, "request body is not valid JSON") from exc


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "application/octet-stream"

    @classmethod
    def json(cls, data: Any, status: int = 200, headers: dict[str, str] | None = None) -> "Response":
        return cls(status, json.dumps(data, separators=(",", ":")).encode(), headers or {}, "application/json")

    @classmethod
    def error(cls, status: int, message: str = "") -> "Response":
        return cls.json({"error": REASONS.get(status, "Error"), "detail": message or REASONS.get(status, "")}, status)


Handler = Callable[[Request], Awaitable[Response]]


def parse_head(raw: bytes) -> tuple[str, str, str, dict[str, str]]:
    """Parse a request head (without the trailing blank line). Raises HTTPError on any violation."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise HTTPError(400, "non-ASCII bytes in request head") from exc
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise HTTPError(400, "malformed request line")
    method, target, version = parts
    if method not in _METHODS:
        raise HTTPError(405 if _TOKEN.match(method) else 400, f"unsupported method {method!r}")
    if version not in ("HTTP/1.1", "HTTP/1.0"):
        raise HTTPError(505 if version.startswith("HTTP/") else 400, "unsupported HTTP version")
    if not target.startswith("/"):
        raise HTTPError(400, "request target must be origin-form")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if line[0] in " \t":
            raise HTTPError(400, "obsolete line folding is not allowed")
        name, sep, value = line.partition(":")
        if not sep or not _TOKEN.match(name):
            raise HTTPError(400, "malformed header field")
        key, value = name.lower(), value.strip()
        if key == "content-length" and key in headers and headers[key] != value:
            raise HTTPError(400, "conflicting Content-Length headers")
        headers[key] = f"{headers[key]}, {value}" if key in headers and key != "content-length" else value
    if version == "HTTP/1.1" and "host" not in headers:
        raise HTTPError(400, "missing Host header")
    return method, target, version, headers


def split_target(target: str) -> tuple[str, dict[str, str]]:
    path, _, qs = target.partition("?")
    return path, dict(parse_qsl(qs, keep_blank_values=True))


class HTTPServer:
    """Asyncio TCP listener speaking HTTP/1.1 to a single async handler."""

    def __init__(self, handler: Handler, host: str, port: int, name: str = "http",
                 max_body: int = MAX_BODY_BYTES):
        self.handler = handler
        self.host = host
        self._port = port
        self.name = name
        self.max_body = max_body
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._handlers: set[asyncio.Task] = set()

    @property
    def port(self) -> int:
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self) -> None:
        if self._server:
            return
        self._server = await asyncio.start_server(self._on_client, self.host, self._port, limit=MAX_HEAD_BYTES)
        self._port = self.port  # pin an ephemeral port so restarts reuse it
        log.info("%s listening on %s:%s", self.name, self.host, self.port)

    async def stop(self) -> None:
        """Close the listening socket and abort in-flight connections (simulates a crash)."""
        server, self._server = self._server, None
        if not server:
            return
        server.close()
        for w in list(self._writers):
            w.transport.abort()
        self._writers.clear()
        for t in list(self._handlers):  # a crashed server does not finish in-flight work
            t.cancel()
        if self._handlers:
            await asyncio.wait(list(self._handlers), timeout=2.0)
        try:
            await asyncio.wait_for(server.wait_closed(), 2.0)
        except (TimeoutError, Exception):  # noqa: BLE001 - best effort on shutdown
            pass

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        task = asyncio.current_task()
        if task:
            self._handlers.add(task)
            task.add_done_callback(self._handlers.discard)
        peer = writer.get_extra_info("peername")
        peer_s = f"{peer[0]}:{peer[1]}" if peer else ""
        first = True
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),
                                                 HEAD_TIMEOUT_S if first else IDLE_TIMEOUT_S)
                except asyncio.LimitOverrunError:
                    await self._send(writer, Response.error(431, "request head too large"), False, "GET")
                    return
                except (asyncio.IncompleteReadError, TimeoutError, ConnectionError):
                    return
                first = False
                try:
                    method, target, version, headers = parse_head(raw[:-4])
                    if "transfer-encoding" in headers:
                        raise HTTPError(501, "Transfer-Encoding request bodies are not supported")
                    body = b""
                    cl = headers.get("content-length")
                    if cl is not None:
                        if not cl.isdigit():
                            raise HTTPError(400, "invalid Content-Length")
                        n = int(cl)
                        if n > self.max_body:
                            raise HTTPError(413, f"body exceeds {self.max_body} bytes")
                        if n:
                            body = await asyncio.wait_for(reader.readexactly(n), BODY_TIMEOUT_S)
                except HTTPError as exc:
                    await self._send(writer, Response.error(exc.status, exc.message), False, "GET")
                    return
                except (asyncio.IncompleteReadError, TimeoutError, ConnectionError):
                    return
                conn = headers.get("connection", "").lower()
                keep_alive = (version == "HTTP/1.1" and "close" not in conn) or (
                    version == "HTTP/1.0" and "keep-alive" in conn)
                path, query = split_target(target)
                req = Request(method, target, path, query, version, headers, body, peer_s)
                try:
                    resp = await self.handler(req)
                except HTTPError as exc:
                    resp = Response.error(exc.status, exc.message)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - never leak tracebacks to clients
                    log.exception("%s handler failed for %s %s", self.name, method, target)
                    resp = Response.error(500)
                await self._send(writer, resp, keep_alive, method)
                if not keep_alive:
                    return
        except ConnectionError:
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, resp: Response, keep_alive: bool, method: str) -> None:
        head = [f"HTTP/1.1 {resp.status} {REASONS.get(resp.status, 'Unknown')}",
                f"Date: {formatdate(usegmt=True)}", "Server: cdn-sim/1.0",
                f"Content-Type: {resp.content_type}", f"Content-Length: {len(resp.body)}",
                f"Connection: {'keep-alive' if keep_alive else 'close'}"]
        head += [f"{k}: {v}" for k, v in resp.headers.items()]
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        if method != "HEAD" and resp.body:
            writer.write(resp.body)
        await writer.drain()


# --------------------------------------------------------------------------- client

class HTTPClientError(Exception):
    """Transport-level failure. kind is one of: connect, timeout, protocol, reset."""

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind


@dataclass
class ClientResponse:
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes
    elapsed_ms: float

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


async def http_request(host: str, port: int, method: str = "GET", path: str = "/", *,
                       json_body: Any = None, headers: dict[str, str] | None = None,
                       timeout: float = 10.0, max_body: int = 64 * 1024 * 1024) -> ClientResponse:
    """Issue one HTTP/1.1 request on a fresh TCP connection (Connection: close)."""
    body = b"" if json_body is None else json.dumps(json_body).encode()
    t0 = time.perf_counter()

    async def _do() -> ClientResponse:
        reader, writer = await asyncio.open_connection(host, port, limit=MAX_HEAD_BYTES)
        try:
            lines = [f"{method} {path} HTTP/1.1", f"Host: {host}:{port}", "Connection: close",
                     "User-Agent: cdn-sim-client/1.0", "Accept: */*"]
            if body or method in ("POST", "PUT"):
                lines += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
            lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
            await writer.drain()
            head = (await reader.readuntil(b"\r\n\r\n"))[:-4].decode("latin-1")
            status_line, *hdr_lines = head.split("\r\n")
            proto, _, rest = status_line.partition(" ")
            code_s, _, reason = rest.partition(" ")
            if not proto.startswith("HTTP/1.") or not code_s.isdigit():
                raise HTTPClientError("protocol", f"bad status line {status_line!r}")
            hdrs: dict[str, str] = {}
            for line in hdr_lines:
                k, sep, v = line.partition(":")
                if not sep:
                    raise HTTPClientError("protocol", "malformed response header")
                hdrs[k.strip().lower()] = v.strip()
            status = int(code_s)
            if method == "HEAD" or status in (204, 304):
                data = b""
            elif "content-length" in hdrs:
                n = int(hdrs["content-length"])
                if n > max_body:
                    raise HTTPClientError("protocol", "response body too large")
                data = await reader.readexactly(n)
            elif "transfer-encoding" in hdrs:
                raise HTTPClientError("protocol", "chunked responses are not supported")
            else:
                data = await reader.read(max_body + 1)  # read-until-close framing
                if len(data) > max_body:
                    raise HTTPClientError("protocol", "response body too large")
            return ClientResponse(status, reason, hdrs, data, (time.perf_counter() - t0) * 1000)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    try:
        return await asyncio.wait_for(_do(), timeout)
    except HTTPClientError:
        raise
    except TimeoutError as exc:
        raise HTTPClientError("timeout", f"no response from {host}:{port} within {timeout}s") from exc
    except ConnectionRefusedError as exc:
        raise HTTPClientError("connect", f"connection refused by {host}:{port}") from exc
    except asyncio.IncompleteReadError as exc:
        raise HTTPClientError("reset", "connection closed mid-response") from exc
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
        raise HTTPClientError("reset", str(exc) or exc.__class__.__name__) from exc
    except OSError as exc:
        raise HTTPClientError("connect", str(exc) or exc.__class__.__name__) from exc
