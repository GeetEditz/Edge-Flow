"""Capture real screenshots of the running dashboard for the report and presentation.

Requires the backend (http://127.0.0.1:8000) and frontend (http://127.0.0.1:5173) to be running and
Google Chrome to be installed. Uses the Chrome DevTools Protocol directly (no extra browser driver).

    .venv-docs/Scripts/python scripts/capture_screenshots.py
Outputs PNG files into submission/figures/.
"""

import asyncio
import base64
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "submission" / "figures"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
UI, API = "http://127.0.0.1:5173", "http://127.0.0.1:8000/api"
PORT = 9334


def api(path: str):
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.loads(r.read())


class Tab:
    def __init__(self, ws):
        self.ws, self.n, self.pending = ws, 0, {}

    async def pump(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if m.get("id") in self.pending:
                self.pending.pop(m["id"]).set_result(m)

    async def cmd(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        res = await asyncio.wait_for(fut, 60)
        if "error" in res:
            raise RuntimeError(res["error"])
        return res.get("result", {})

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    async def goto(self, path, wait=3.5):
        await self.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1.5, mobile=False)
        await self.cmd("Page.navigate", url=UI + path)
        await asyncio.sleep(wait)

    async def shot(self, name, clip=None):
        params = {"format": "png", "captureBeyondViewport": True}
        if clip:
            # grow the viewport so the region is laid out on screen (charts render only when visible)
            await self.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=int(clip["y"] + clip["height"] + 40),
                           deviceScaleFactor=1.5, mobile=False)
            await asyncio.sleep(1.5)
            params["clip"] = {**clip, "scale": 1}
        r = await self.cmd("Page.captureScreenshot", **params)
        (OUT / f"{name}.png").write_bytes(base64.b64decode(r["data"]))
        print("saved", name)

    async def region(self, first_selector_js, last_selector_js, pad=12):
        """Bounding box spanning two elements (document coordinates)."""
        box = await self.js(f"""(() => {{
            const a = {first_selector_js}, b = {last_selector_js};
            if (!a || !b) return null;
            const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
            const x = Math.min(ra.left, rb.left), y = Math.min(ra.top, rb.top) + window.scrollY;
            return {{x: x, y: y, width: Math.max(ra.right, rb.right) - x, height: Math.max(ra.bottom, rb.bottom) + window.scrollY - y}};
        }})()""")
        if not box:
            raise RuntimeError(f"elements not found: {first_selector_js} / {last_selector_js}")
        return {"x": max(0, box["x"] - pad), "y": max(0, box["y"] - pad), "width": box["width"] + 2 * pad,
                "height": box["height"] + 2 * pad}


def panel(title: str) -> str:
    return f"[...document.querySelectorAll('section.panel')].find(s => s.querySelector('h2')?.textContent.startsWith({json.dumps(title)}))"


def warm_live_metrics(seconds: int = 25) -> None:
    """Run a short real traffic run so the Overview page has recent per-second data to display."""
    body = json.dumps({"name": "Screenshot warm-up traffic", "type": "single", "start": True,
                       "workload": {"total_requests": seconds * 30, "concurrency": 8, "rate_rps": 30}}).encode()
    req = urllib.request.Request(API + "/experiments", data=body, method="POST", headers={"Content-Type": "application/json"})
    exp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    import time
    while api(f"/experiments/{exp['id']}")["status"] in ("created", "running", "stopping"):
        time.sleep(1)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    warm_live_metrics()
    exps = {e["name"]: e["id"] for e in api("/experiments")["experiments"]}
    fail_id = next(v for k, v in exps.items() if k.startswith("C. "))
    prof = tempfile.mkdtemp(prefix="cdn-shot-")
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                             "--disable-gpu", "--hide-scrollbars", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                tgt = next(t for t in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json").read())
                           if t["type"] == "page")
                break
            except Exception:  # noqa: BLE001 - Chrome still starting
                await asyncio.sleep(0.25)
        async with websockets.connect(tgt["webSocketDebuggerUrl"], max_size=100_000_000) as ws:
            t = Tab(ws)
            pump = asyncio.create_task(t.pump())
            await t.cmd("Page.enable")
            await t.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1.5, mobile=False)

            await t.goto("/")
            await t.shot("shot_overview", {"x": 0, "y": 0, "width": 1440, "height": 900})

            await t.goto("/servers")
            await t.shot("shot_servers", await t.region("document.querySelector('.page-head')",
                                                        "[...document.querySelectorAll('section.edge-card')].pop()"))

            await t.goto("/routing", 4)
            await t.shot("shot_routing_lab", await t.region(panel("Routing strategy"), panel("Recent resolutions")))
            await t.shot("shot_routing_comparison", await t.region(panel("Latest routing comparison"), panel("Request distribution")))

            await t.goto("/cache", 4)
            await t.shot("shot_cache_comparison", await t.region(panel("Latest cache comparison"), panel("Origin fetches by policy")))

            await t.goto(f"/experiments/{fail_id}", 4)
            await t.shot("shot_failure_experiment", await t.region(
                "[...document.querySelectorAll('.stat-label')].find(e => e.textContent === 'Detection time').closest('.grid')",
                panel("Requests per edge over time")))

            await t.goto("/topology", 4)
            await t.shot("shot_topology", await t.region(panel("Topology"), panel("Topology")))
            pump.cancel()
    finally:
        proc.terminate()


if __name__ == "__main__":
    asyncio.run(main())
