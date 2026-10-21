"""Drive the running dashboard through the Chrome DevTools Protocol: render every page, click controls,
verify their effect on the backend through the API, and collect console errors.

Requires backend (:8000) and frontend (:5173) running. Creates one 150-request traffic run; the
temporary experiment it creates through the form is deleted again.

    .venv-docs/Scripts/python scripts/ui_check.py
"""
import asyncio, base64, json, subprocess, sys, time, urllib.request
from pathlib import Path
import websockets

import tempfile
SCRATCH = Path(tempfile.mkdtemp(prefix="cdn-ui-check-"))
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
UI = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000/api"


def api(path, method="GET", body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


class Page:
    def __init__(self, ws):
        self.ws, self.n, self.pending, self.errors = ws, 0, {}, []

    async def pump(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self.pending:
                self.pending.pop(m["id"]).set_result(m)
            elif m.get("method") == "Runtime.exceptionThrown":
                self.errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description", "exception"))
            elif m.get("method") == "Runtime.consoleAPICalled" and m["params"]["type"] in ("error", "warning"):
                self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in m["params"]["args"]))
            elif m.get("method") == "Log.entryAdded" and m["params"]["entry"]["level"] == "error":
                self.errors.append(m["params"]["entry"]["text"] + " " + m["params"]["entry"].get("url", ""))

    async def cmd(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        return (await asyncio.wait_for(fut, 30)).get("result", {})

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    async def goto(self, path, wait=2.5):
        await self.cmd("Page.navigate", url=UI + path)
        await asyncio.sleep(wait)

    async def click_text(self, text, selector="button"):
        ok = await self.js(f"""(() => {{ const els=[...document.querySelectorAll({json.dumps(selector)})].filter(e => e.textContent.trim().includes({json.dumps(text)}) && !e.disabled);
            if (!els.length) return false; els[0].click(); return true; }})()""")
        if not ok:
            raise AssertionError(f"no enabled {selector} containing {text!r}")
        await asyncio.sleep(0.4)

    async def shot(self, name, width=1440, height=1800):
        await self.cmd("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=width < 600)
        await asyncio.sleep(1.0)
        r = await self.cmd("Page.captureScreenshot", format="png")
        (SCRATCH / f"{name}.png").write_bytes(base64.b64decode(r["data"]))
        await self.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=1800, deviceScaleFactor=1, mobile=False)


async def wait_api(pred, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        if pred():
            return True
        await asyncio.sleep(0.5)
    raise AssertionError("timeout waiting for backend state")


async def main():
    proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9333", f"--user-data-dir={SCRATCH / 'chrome-prof'}",
                             "--disable-gpu", "--window-size=1440,1800", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json").read())
                tgt = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(0.25)
        async with websockets.connect(tgt["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
            p = Page(ws)
            pump = asyncio.create_task(p.pump())
            for d in ("Runtime", "Log", "Page"):
                await p.cmd(f"{d}.enable")
            await p.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=1800, deviceScaleFactor=1, mobile=False)
            results = []

            def ok(name):
                results.append(("PASS", name)); print("PASS", name, flush=True)

            # every page loads without console errors and shows no placeholder text
            for path in ["/", "/servers", "/routing", "/cache", "/traffic", "/experiments", "/topology", "/nope"]:
                before = len(p.errors)
                await p.goto(path, 3)
                text = await p.js("document.body.innerText")
                assert "lorem" not in text.lower() and "todo" not in text.lower(), path
                assert len(p.errors) == before, f"console errors on {path}: {p.errors[before:]}"
                ok(f"page {path} renders without console errors")

            # Routing Lab: choose Least loaded + Apply -> backend changes
            await p.goto("/routing")
            await p.click_text("Least loaded", "button[role=radio]")
            await p.click_text("Apply")
            await wait_api(lambda: api("/routing")["strategy"] == "least_loaded", 10)
            ok("routing strategy control changes backend")
            await p.click_text("Hybrid", "button[role=radio]")
            await p.click_text("Apply")
            await wait_api(lambda: api("/routing")["strategy"] == "hybrid", 10)
            # test request
            await p.click_text("Send")
            await asyncio.sleep(1.5)
            assert "HTTP 200" in await p.js("document.body.innerText")
            ok("test request shows HTTP 200 and explanation")

            # Servers: simulate failure (with confirm) then restore
            await p.goto("/servers")
            await p.js("""[...document.querySelectorAll('section[aria-label="USA Edge"] button')].find(b=>b.textContent.includes('Simulate failure')).click()""")
            await asyncio.sleep(0.4)
            await p.click_text("Simulate failure", ".dialog button")
            await wait_api(lambda: next(s for s in api("/servers")["servers"] if s["edge_id"] == "usa-edge")["status"] == "unhealthy", 15)
            ok("simulate failure via confirm dialog marks edge unhealthy")
            await asyncio.sleep(2.5)
            await p.js("""[...document.querySelectorAll('section[aria-label="USA Edge"] button')].find(b=>b.textContent.includes('Restore')).click()""")
            await wait_api(lambda: next(s for s in api("/servers")["servers"] if s["edge_id"] == "usa-edge")["status"] == "healthy", 15)
            ok("restore control brings edge back to healthy")

            # Cache Lab: warm then clear India cache (confirm)
            await p.goto("/cache")
            await p.click_text("Warm")
            await wait_api(lambda: len(api("/cache/india-edge")["entries"]) > 0, 15)
            await p.click_text("Clear")
            await p.click_text("Clear cache", ".dialog button")
            await wait_api(lambda: len(api("/cache/india-edge")["entries"]) == 0, 10)
            ok("cache warm and clear controls act on the edge cache")

            # Traffic generator: start a run, watch progress, see results
            n_before = len(api("/experiments")["experiments"])
            await p.goto("/traffic")
            await p.js("""(() => { const i=[...document.querySelectorAll('input[type=number]')][0]; const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; set.call(i,'150'); i.dispatchEvent(new Event('input',{bubbles:true})); })()""")
            await p.click_text("Start traffic")
            await wait_api(lambda: len(api("/experiments")["experiments"]) == n_before + 1, 10)
            exp = api("/experiments")["experiments"][0]
            assert exp["config"]["workload"]["total_requests"] == 150, exp["config"]["workload"]
            await wait_api(lambda: api(f"/experiments/{exp['id']}")["status"] == "completed", 60)
            await asyncio.sleep(3)
            txt = await p.js("document.body.innerText")
            assert "Error summary" in txt, "results not rendered"
            ok("traffic generator starts a real 150-request run and renders results")
            await p.shot("traffic-after")

            # Experiments: create via form (save only), open, CSV link
            await p.goto("/experiments")
            await p.click_text("New experiment")
            await p.click_text("Save only")
            await wait_api(lambda: len(api("/experiments")["experiments"]) == n_before + 2, 10)
            await asyncio.sleep(1.5)
            href = await p.js("[...document.querySelectorAll('a')].find(a=>a.textContent.includes('Runs CSV'))?.href")
            assert href and "export.csv" in href
            csv = urllib.request.urlopen(href.replace("5173", "5173")).read().decode()
            assert csv.startswith("experiment_id,")
            ok("experiment form saves and CSV export downloads")
            # delete it again
            await p.js("document.querySelector('button[aria-label=\"Delete experiment\"]').click()")
            await asyncio.sleep(0.4)
            await p.click_text("Delete", ".dialog button")
            await wait_api(lambda: len(api("/experiments")["experiments"]) == n_before + 1, 10)
            ok("delete with confirmation removes the experiment")

            # screenshots for review (desktop + phone)
            for path, name in [("/topology", "topology2"), ("/experiments", "experiments2"), ("/cache", "cache2"), ("/routing", "routing2")]:
                await p.goto(path, 3)
                await p.shot(name)
            await p.goto("/", 3)
            await p.shot("overview-mobile", 390, 1600)
            await p.goto("/servers", 3)
            await p.shot("servers-mobile", 390, 1600)
            print("console errors:", p.errors)
            pump.cancel()
    finally:
        proc.terminate()


asyncio.run(main())
