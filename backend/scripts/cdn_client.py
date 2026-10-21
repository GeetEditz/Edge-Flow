"""Command-line CDN client: resolve via CNRP (UDP), then download over HTTP/1.1 (TCP).

  python scripts/cdn_client.py images/photo-05.png
  python scripts/cdn_client.py index.html --repeat 3 --resolver 10.0.0.1:8053     # Mininet
  python scripts/cdn_client.py index.html --hostname usa.cdn.sim.local -o out.html

Requests made this way are NOT recorded by the control plane's client metrics (they go
straight to the edge), but they are counted by the edge and origin counters.
"""

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.networking.http import HTTPClientError, http_request  # noqa: E402
from app.networking.resolver import RCODE_NAMES, ResolverClient  # noqa: E402


async def main(a: argparse.Namespace) -> int:
    host, _, port = a.resolver.partition(":")
    resolver = await ResolverClient(host, int(port or 8053), timeout=1.0, retries=2).open()
    try:
        for i in range(a.repeat):
            ans, rtt = await resolver.resolve(a.hostname, explain=True)
            if not ans.ok:
                print(f"resolve failed: {RCODE_NAMES.get(ans.rcode)} {ans.explanation}")
                return 2
            print(f"[{i + 1}] {a.hostname} -> {ans.edge_id} {ans.address}:{ans.port} ttl={ans.ttl}s "
                  f"({rtt:.2f} ms UDP)  reason: {ans.explanation}")
            try:
                r = await http_request(ans.address, ans.port, "GET", f"/content/{a.object_id}", timeout=a.timeout)
            except HTTPClientError as exc:
                print(f"    fetch failed: {exc}")
                return 3
            print(f"    HTTP {r.status}  {len(r.body)} bytes  {r.elapsed_ms:.1f} ms  X-Cache={r.headers.get('x-cache')}  "
                  f"sha256={hashlib.sha256(r.body).hexdigest()[:16]}")
            if a.output and r.status == 200:
                Path(a.output).write_bytes(r.body)
    finally:
        resolver.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("object_id")
    ap.add_argument("--resolver", default="127.0.0.1:8053", help="CNRP resolver host:port")
    ap.add_argument("--hostname", default="cdn.sim.local")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("-o", "--output")
    sys.exit(asyncio.run(main(ap.parse_args())))
