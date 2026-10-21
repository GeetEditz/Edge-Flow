# Mininet Mode

> **Status: not executed in the development environment.** The project was built on Windows 11,
> where Mininet cannot run (it needs Linux network namespaces, `tc`/netem and Open vSwitch).
> `topology.py` is syntax-checked and uses the same CLI entry points that the Local Mode tests
> exercise, but the end-to-end Mininet run below has **not** been verified. Treat the first run
> as a validation step.

In Mininet Mode the origin, the three edges and the control plane each run on their own Mininet
host, and **real** shaped links (tc/netem) replace the simulated per-edge profile (edges run with
an all-zero simulated profile).

```
            root-ns (10.0.0.254, browser)
                      |
 client 10.0.0.1 ---- s1 ---- origin 10.0.0.2   (20 ms, 100 Mbit/s)
 (API :8000,           |\
  CNRP udp :8053)      | \---- usa   10.0.0.13 (55 ms one-way, 400 Mbit/s, 0.4 % loss)
                       \------ sing  10.0.0.12 (21 ms one-way, 200 Mbit/s, 0.1 % loss)
                        \----- india 10.0.0.11 ( 9 ms one-way,  50 Mbit/s, 0.2 % loss)
```

## Supported environment

* Ubuntu 22.04 / 24.04 (bare metal or a VM such as VirtualBox / VMware / UTM / Multipass).
  WSL2 is **not** recommended: its default kernel lacks the Open vSwitch module.
* Mininet 2.3+, Open vSwitch, `tcpdump`, Python 3.10+.

## Setup

```bash
sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch tcpdump python3-venv curl
sudo systemctl enable --now openvswitch-switch

cd distributed-cdn-simulator/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/generate_content.py        # sample catalogue
```

## Run

```bash
cd distributed-cdn-simulator
sudo python3 mininet/topology.py            # builds the network, starts all servers, smoke test, Mininet CLI
```

What the script does:

1. Creates the hosts and TCLinks above (`--india-delay 60ms`, `--india-loss 2` change the India link).
2. Starts `app.origin.server` on `origin`, `app.edge.server` on each edge host, and the control plane
   (`uvicorn app.main:app`) on `client` with `CDN_MODE=mininet CDN_LAUNCH=external` and a generated
   topology file at `/tmp/cdn-mininet/topology.json`.
3. Smoke test: pings each edge (RTT ≈ 2 × one-way delay), starts `tcpdump` on every edge interface,
   downloads objects with `scripts/cdn_client.py` from the `client` host, and prints how many packets
   crossed each edge's link — proving that traffic traversed the shaped links.

Dashboard: from the VM, run the frontend with the API pointed at the client host:

```bash
cd frontend
CDN_API_URL=http://10.0.0.1:8000 npm run dev -- --host 0.0.0.0
```

and open `http://127.0.0.1:5173`. The top bar shows **Mininet mode** and the topology page is labelled
**Running Mininet topology**.

## Changing link conditions at runtime

From the Mininet CLI (the interface names are `<host>-eth0`):

```
mininet> india tc qdisc show dev india-eth0
mininet> india tc qdisc change dev india-eth0 parent 5:1 netem delay 80ms loss 2%
mininet> client ping -c 3 10.0.0.11
```

(`parent 5:1` is where TCLink attaches netem when `use_htb=True`; confirm with `tc qdisc show`.)
Lowest-latency and hybrid routing react within a few seconds as the measured probe RTT EWMA rises.

## Failure simulation

Either use the dashboard ("Simulate failure", which closes the edge's data listener), or take the
link down for a network-level failure:

```
mininet> link s1 india down
mininet> link s1 india up
```

## Cleanup

`exit` in the CLI stops all servers and the network. After a crash:

```bash
sudo pkill -f 'app.main:app|app.origin.server|app.edge.server'
sudo mn -c
```

The script confines all `tc`/link manipulation to Mininet's virtual interfaces; it never touches the
host's physical interfaces or firewall.
