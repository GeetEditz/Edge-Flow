#!/usr/bin/env python3
"""Mininet topology for the Distributed CDN Simulator (Mininet Mode).

    sudo python3 mininet/topology.py                 # start, run a smoke test, open the Mininet CLI
    sudo python3 mininet/topology.py --no-cli        # start, smoke test, tear down
    sudo python3 mininet/topology.py --india-delay 60ms --india-loss 2

Hosts (one switch s1, 10.0.0.0/24):
    client  10.0.0.1   control plane (FastAPI :8000, CNRP resolver udp :8053) + traffic generator
    origin  10.0.0.2   origin server :9000
    india   10.0.0.11  edge data :9001 / admin :9101
    sing    10.0.0.12  edge data :9002 / admin :9102
    usa     10.0.0.13  edge data :9003 / admin :9103
    root    10.0.0.254 (root namespace) — lets a browser on the VM reach http://10.0.0.1:8000

Every edge<->switch link is a TCLink shaped with tc/netem (delay, bandwidth, loss). The
edges run with an all-zero simulated profile so only the real Mininet links add latency.
Netem delay applies on egress of each interface, so a one-way link delay D gives ~2D RTT.

Requirements: Linux with Mininet 2.3+ and Open vSwitch, run as root, Python 3.10+ with the
backend requirements installed for the interpreter given by --python.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import Node, OVSBridge

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
RUN = Path("/tmp/cdn-mininet")

EDGES = [  # name, edge_id, ip, data, admin, distance, priority, one-way delay, bw (Mbit/s), loss %, concurrency
    ("india", "india-edge", "10.0.0.11", 9001, 9101, 850, 1, "9ms", 50, 0.2, 8),
    ("sing", "singapore-edge", "10.0.0.12", 9002, 9102, 3200, 2, "21ms", 200, 0.1, 16),
    ("usa", "usa-edge", "10.0.0.13", 9003, 9103, 13900, 3, "55ms", 400, 0.4, 32),
]
ZERO_PROFILE = {"base_latency_ms": 0, "jitter_ms": 0, "bandwidth_mbps": 100000, "packet_loss": 0}


def topology_json(args) -> dict:
    return {
        "client": {"name": "Client (Mininet h: client)", "location": "10.0.0.1"},
        "origin": {"host": "10.0.0.2", "port": 9000, "delay_ms": 0, "location": "Mininet host origin"},
        "edges": [{"edge_id": eid, "name": eid.replace("-", " ").title(), "location": f"Mininet host {name}",
                   "host": ip, "data_port": dp, "admin_port": ap, "distance_km": dist, "priority": prio,
                   "concurrency_limit": conc, "profile": ZERO_PROFILE}
                  for name, eid, ip, dp, ap, dist, prio, *_rest, conc in EDGES],
        "cache": {"policy": "lru", "capacity_bytes": 4 * 1024 * 1024, "ttl_seconds": 30.0},
        "routing": {"strategy": "hybrid", "weights": {"latency_weight": 0.5, "load_weight": 0.4, "packet_loss_weight": 0.1}},
    }


def build(args) -> Mininet:
    net = Mininet(switch=OVSBridge, link=TCLink, controller=None, autoSetMacs=True)
    s1 = net.addSwitch("s1")
    client = net.addHost("client", ip="10.0.0.1/24")
    origin = net.addHost("origin", ip="10.0.0.2/24")
    net.addLink(client, s1, bw=1000)
    net.addLink(origin, s1, bw=args.origin_bw, delay=args.origin_delay)
    for name, _eid, ip, *_r in EDGES:
        h = net.addHost(name, ip=f"{ip}/24")
        delay, bw, loss = _r[4], _r[5], _r[6]
        if name == "india":
            delay, loss = args.india_delay or delay, args.india_loss if args.india_loss is not None else loss
        net.addLink(h, s1, delay=delay, bw=bw, loss=loss, use_htb=True)
    root = Node("root", inNamespace=False)  # root-namespace access for the dashboard
    net.addLink(root, s1)
    root.setIP("10.0.0.254/24", intf=root.defaultIntf())
    return net


def start_services(net: Mininet, args) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / "topology.json").write_text(json.dumps(topology_json(args), indent=2))
    py, env = args.python, f"cd {BACKEND} &&"
    net["origin"].cmd(f"{env} {py} -m app.origin.server --host 10.0.0.2 --port 9000 --delay-ms 0 "
                      f"> {RUN}/origin.log 2>&1 &")
    for name, eid, ip, dp, ap, *_r in EDGES:
        cfg = json.dumps({"profile": ZERO_PROFILE, "concurrency_limit": _r[-1]})
        net[name].cmd(f"{env} {py} -m app.edge.server --edge-id {eid} --host {ip} --data-port {dp} --admin-port {ap} "
                      f"--origin-host 10.0.0.2 --origin-port 9000 --config '{cfg}' > {RUN}/{name}.log 2>&1 &")
    time.sleep(2)
    net["client"].cmd(
        f"{env} CDN_MODE=mininet CDN_LAUNCH=external CDN_TOPOLOGY={RUN}/topology.json "
        f"CDN_DB_PATH={RUN}/cdn.sqlite3 CDN_API_HOST=10.0.0.1 CDN_RESOLVER_HOST=10.0.0.1 "
        f"CDN_CORS_ORIGINS=http://10.0.0.254:5173,http://127.0.0.1:5173,http://localhost:5173 "
        f"{py} -m uvicorn app.main:app --host 10.0.0.1 --port 8000 > {RUN}/control.log 2>&1 &")
    for _ in range(40):
        if "ok" in net["client"].cmd("curl -s -m 1 http://10.0.0.1:8000/api/health"):
            info("*** control plane is up at http://10.0.0.1:8000\n")
            return
        time.sleep(0.5)
    info(f"!!! control plane did not start; see {RUN}/control.log\n")


def smoke_test(net: Mininet, args) -> None:
    """Verify traffic really crosses the shaped links: count packets on each edge interface."""
    client = net["client"]
    info("*** ping RTTs from client (expect ~2x the one-way link delay)\n")
    for name, eid, ip, *_r in EDGES:
        out = client.cmd(f"ping -c 3 -q {ip}")
        info(f"    {eid:15s} {out.strip().splitlines()[-1] if out.strip() else 'no reply'}\n")
    caps = {}
    for name, *_r in EDGES:
        intf = net[name].defaultIntf().name
        caps[name] = f"{RUN}/{name}.pcap"
        net[name].cmd(f"timeout 15 tcpdump -i {intf} -w {caps[name]} 'tcp port 9001 or tcp port 9002 or tcp port 9003' "
                      f"> /dev/null 2>&1 &")
    time.sleep(1)
    info("*** fetching through the CDN from the client host\n")
    out = client.cmd(f"cd {BACKEND} && {args.python} scripts/cdn_client.py images/photo-05.png --repeat 3 "
                     f"--resolver 10.0.0.1:8053")
    info(out)
    for name, eid, ip, dp, *_r in EDGES:
        out = client.cmd(f"cd {BACKEND} && {args.python} scripts/cdn_client.py index.html "
                         f"--hostname {name if name != 'sing' else 'singapore'}.cdn.sim.local --resolver 10.0.0.1:8053")
        info(out)
    time.sleep(2)
    for name in caps:
        net[name].cmd("pkill -INT tcpdump")
    time.sleep(1)
    for name, path in caps.items():
        n = net[name].cmd(f"tcpdump -r {path} 2>/dev/null | wc -l").strip()
        info(f"    packets captured on {name}'s link: {n}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="CDN simulator Mininet topology")
    ap.add_argument("--python", default=str(BACKEND / ".venv" / "bin" / "python"))
    ap.add_argument("--origin-delay", default="20ms")
    ap.add_argument("--origin-bw", type=float, default=100)
    ap.add_argument("--india-delay", default=None, help="override India link one-way delay, e.g. 60ms")
    ap.add_argument("--india-loss", type=float, default=None, help="override India link loss in percent")
    ap.add_argument("--no-cli", action="store_true")
    ap.add_argument("--no-smoke", action="store_true")
    args = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("Mininet must run as root: sudo python3 mininet/topology.py")
    if not Path(args.python).exists():
        sys.exit(f"python interpreter {args.python} not found; create backend/.venv or pass --python")
    setLogLevel("info")
    net = build(args)
    net.start()
    try:
        start_services(net, args)
        if not args.no_smoke:
            smoke_test(net, args)
        if not args.no_cli:
            info("*** Mininet CLI. Try: client python3 backend/scripts/cdn_client.py index.html --resolver 10.0.0.1:8053\n")
            CLI(net)
    finally:
        for h in ("client", "origin", *(e[0] for e in EDGES)):
            net[h].cmd("pkill -f 'app.main:app|app.origin.server|app.edge.server'")
        net.stop()


if __name__ == "__main__":
    main()
