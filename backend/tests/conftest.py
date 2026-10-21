import copy
import socket
from pathlib import Path

import pytest

from app.config import DEFAULT_TOPOLOGY, Settings
from app.origin.content_gen import generate


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def content_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("content")
    generate(d)
    return d


def make_topology(fast: bool = True) -> dict:
    topo = copy.deepcopy(DEFAULT_TOPOLOGY)
    topo["origin"]["port"] = free_port()
    topo["origin"]["delay_ms"] = 5.0
    for e in topo["edges"]:
        e["data_port"], e["admin_port"] = free_port(), free_port()
        if fast:  # keep tests quick while preserving the latency ordering india < singapore < usa
            e["profile"] = {**e["profile"], "base_latency_ms": e["profile"]["base_latency_ms"] / 6,
                            "jitter_ms": 0, "packet_loss": 0}
    return topo


@pytest.fixture
def settings(tmp_path, content_dir) -> Settings:
    s = Settings()
    s.launch = "inprocess"
    s.resolver_port = 0
    s.db_path = tmp_path / "test.sqlite3"
    s.content_dir = content_dir
    s.probe_interval_s = 0.2
    s.probe_timeout_s = 0.5
    s.stats_interval_s = 0.2
    s.topology = make_topology()
    return s
