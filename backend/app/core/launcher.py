"""Starts the origin and edge servers for Local Mode.

  subprocess  each server is its own OS process (python -m app.origin.server / app.edge.server)
              with its own PID, sockets and cache. Children exit if the control plane dies.
  inprocess   servers run as independent asyncio listeners inside the control-plane process
              (used by tests; CPU/memory figures are then for the shared process)
  external    servers are started elsewhere (e.g. on Mininet hosts); nothing is launched
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from app.config import BACKEND_DIR
from app.edge.profile import NetworkProfile
from app.edge.server import EdgeServer
from app.networking.http import HTTPClientError, http_request
from app.origin.server import OriginServer

log = logging.getLogger("cdn.launcher")


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


async def wait_ready(host: str, port: int, path: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            r = await http_request(host, port, "GET", path, timeout=1.0)
            if r.status == 200:
                return
        except HTTPClientError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"server {host}:{port} did not become ready within {timeout}s")
        await asyncio.sleep(0.2)


class Launcher:
    def __init__(self, mode: str, topology: dict, content_dir: Path, log_dir: Path, cache_cfg: dict):
        self.mode, self.topology, self.content_dir, self.log_dir = mode, topology, content_dir, log_dir
        self.cache_cfg = cache_cfg
        self.procs: list[subprocess.Popen] = []
        self.servers: list[OriginServer | EdgeServer] = []

    def _edge_config(self, e: dict, i: int) -> dict:
        return {"profile": e.get("profile", {}), "concurrency_limit": e.get("concurrency_limit", 16),
                "cache_policy": self.cache_cfg["policy"], "cache_capacity_bytes": self.cache_cfg["capacity_bytes"],
                "cache_ttl_seconds": self.cache_cfg.get("ttl_seconds"), "seed": 1000 + i}

    async def start(self) -> None:
        o, edges = self.topology["origin"], self.topology["edges"]
        if self.mode == "external":
            log.info("external launch mode: expecting origin and edges to be running already")
            return
        ports = [(o["host"], o["port"])] + [(e["host"], p) for e in edges for p in (e["data_port"], e["admin_port"])]
        busy = [f"{h}:{p}" for h, p in ports if not port_free(h, p)]
        if busy:
            raise RuntimeError(f"ports already in use: {', '.join(busy)} — is another instance running?")
        if self.mode == "inprocess":
            origin = OriginServer(self.content_dir, o["host"], o["port"], o.get("delay_ms", 25.0))
            await origin.start()
            self.servers.append(origin)
            for i, e in enumerate(edges):
                c = self._edge_config(e, i)
                srv = EdgeServer(e["edge_id"], host=e["host"], data_port=e["data_port"], admin_port=e["admin_port"],
                                 origin_host=o["host"], origin_port=o["port"], profile=NetworkProfile(**c["profile"]),
                                 cache_policy=c["cache_policy"], cache_capacity_bytes=c["cache_capacity_bytes"],
                                 cache_ttl_seconds=c["cache_ttl_seconds"], concurrency_limit=c["concurrency_limit"],
                                 seed=c["seed"], shared_process=True)
                await srv.start()
                self.servers.append(srv)
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        py, pid = sys.executable, str(os.getpid())
        self._spawn("origin", [py, "-m", "app.origin.server", "--host", o["host"], "--port", str(o["port"]),
                               "--content-dir", str(self.content_dir), "--delay-ms", str(o.get("delay_ms", 25.0)),
                               "--parent-pid", pid])
        for i, e in enumerate(edges):
            self._spawn(e["edge_id"], [py, "-m", "app.edge.server", "--edge-id", e["edge_id"], "--host", e["host"],
                                       "--data-port", str(e["data_port"]), "--admin-port", str(e["admin_port"]),
                                       "--origin-host", o["host"], "--origin-port", str(o["port"]),
                                       "--config", json.dumps(self._edge_config(e, i)), "--parent-pid", pid])
        await asyncio.gather(wait_ready(o["host"], o["port"], "/_health"),
                             *(wait_ready(e["host"], e["admin_port"], "/stats") for e in edges))
        log.info("launched origin + %d edge processes", len(edges))

    def _spawn(self, name: str, cmd: list[str]) -> None:
        logf = open(self.log_dir / f"{name}.log", "ab")  # noqa: SIM115 - owned by the child process
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.procs.append(subprocess.Popen(cmd, cwd=BACKEND_DIR, stdout=logf, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, creationflags=flags))
        logf.close()

    async def stop(self) -> None:
        for s in reversed(self.servers):
            await s.stop()
        self.servers.clear()
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                await asyncio.to_thread(p.wait, 5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs.clear()
