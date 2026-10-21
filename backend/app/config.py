"""Runtime configuration and the default (simulated) topology.

Environment variables:
  CDN_MODE          local (default) | mininet  — label shown in the UI; mininet implies external edges
  CDN_LAUNCH        subprocess (default) | inprocess | external — how origin/edges are started
  CDN_TOPOLOGY      path to a JSON topology file overriding DEFAULT_TOPOLOGY
  CDN_DB_PATH       SQLite file (default: <repo>/data/cdn.sqlite3)
  CDN_CONTENT_DIR   origin content directory (default: backend/content)
  CDN_API_HOST / CDN_API_PORT, CDN_RESOLVER_HOST / CDN_RESOLVER_PORT
  CDN_CORS_ORIGINS  comma-separated allowed origins for the dashboard
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent

CDN_HOSTNAME = "cdn.sim.local"

DEFAULT_TOPOLOGY = {
    "client": {"name": "Client", "location": "Bengaluru, India (simulated)"},
    "origin": {"host": "127.0.0.1", "port": 9000, "delay_ms": 25.0, "location": "Frankfurt, Germany (simulated)"},
    "edges": [
        {"edge_id": "india-edge", "name": "India Edge", "location": "Mumbai, India", "host": "127.0.0.1",
         "data_port": 9001, "admin_port": 9101, "distance_km": 850, "priority": 1, "concurrency_limit": 8,
         "profile": {"base_latency_ms": 18, "jitter_ms": 3, "bandwidth_mbps": 50, "packet_loss": 0.002}},
        {"edge_id": "singapore-edge", "name": "Singapore Edge", "location": "Singapore", "host": "127.0.0.1",
         "data_port": 9002, "admin_port": 9102, "distance_km": 3200, "priority": 2, "concurrency_limit": 16,
         "profile": {"base_latency_ms": 42, "jitter_ms": 5, "bandwidth_mbps": 200, "packet_loss": 0.001}},
        {"edge_id": "usa-edge", "name": "USA Edge", "location": "Virginia, USA", "host": "127.0.0.1",
         "data_port": 9003, "admin_port": 9103, "distance_km": 13900, "priority": 3, "concurrency_limit": 32,
         "profile": {"base_latency_ms": 110, "jitter_ms": 8, "bandwidth_mbps": 400, "packet_loss": 0.004}},
    ],
    "cache": {"policy": "lru", "capacity_bytes": 4 * 1024 * 1024, "ttl_seconds": 30.0},
    "routing": {"strategy": "hybrid", "weights": {"latency_weight": 0.5, "load_weight": 0.4, "packet_loss_weight": 0.1}},
}


@dataclass
class Settings:
    mode: str = "local"
    launch: str = "subprocess"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    resolver_host: str = "127.0.0.1"
    resolver_port: int = 8053
    resolver_ttl_s: int = 5
    db_path: Path = REPO_DIR / "data" / "cdn.sqlite3"
    content_dir: Path = BACKEND_DIR / "content"
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    probe_interval_s: float = 0.5
    probe_timeout_s: float = 0.8
    stats_interval_s: float = 1.0
    fail_threshold: int = 2
    recover_threshold: int = 2
    topology: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_TOPOLOGY)))

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        env = os.environ
        s.mode = env.get("CDN_MODE", s.mode)
        s.launch = env.get("CDN_LAUNCH", "external" if s.mode == "mininet" else s.launch)
        s.api_host = env.get("CDN_API_HOST", s.api_host)
        s.api_port = int(env.get("CDN_API_PORT", s.api_port))
        s.resolver_host = env.get("CDN_RESOLVER_HOST", s.resolver_host)
        s.resolver_port = int(env.get("CDN_RESOLVER_PORT", s.resolver_port))
        if "CDN_DB_PATH" in env:
            s.db_path = Path(env["CDN_DB_PATH"])
        if "CDN_CONTENT_DIR" in env:
            s.content_dir = Path(env["CDN_CONTENT_DIR"])
        if "CDN_CORS_ORIGINS" in env:
            s.cors_origins = [o.strip() for o in env["CDN_CORS_ORIGINS"].split(",") if o.strip()]
        if "CDN_TOPOLOGY" in env:
            s.topology = json.loads(Path(env["CDN_TOPOLOGY"]).read_text())
        if s.mode not in ("local", "mininet"):
            raise ValueError("CDN_MODE must be local or mininet")
        if s.launch not in ("subprocess", "inprocess", "external"):
            raise ValueError("CDN_LAUNCH must be subprocess, inprocess or external")
        return s
