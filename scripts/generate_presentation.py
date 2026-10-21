"""Generate the 8-slide presentation from the original PowerPoint template.

    .venv-docs/Scripts/python scripts/generate_presentation.py [--pdf <preview.pdf>]

The template is opened read-only and saved under submission/. Text boxes keep their template position
and run formatting; images are fitted into the template's "[Insert ...]" areas. Numbers are read from
submission/evidence/ so that slides and report agree.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "MiniProject_PPT_Template_TY_CSE_Final.pptx"
OUT = ROOT / "submission" / "Distributed_CDN_Simulator_Final_Presentation.pptx"
FIG = ROOT / "submission" / "figures"
EVID = ROOT / "submission" / "evidence"

TITLE = "Distributed CDN Simulator with Latency-Aware Routing and Adaptive Caching"
TEAM = [("More Geet Dattatraya", "1262250006"), ("Shinde Soham Manoj", "1262250009"),
        ("Jagtap Prasad Sachin", "1262250018"), ("Gaikwad Shreyash Sachin", "1262250005")]
PLACEHOLDER_COLOR = RGBColor(0xC2, 0x41, 0x0C)  # bracketed, still-unconfirmed fields


def shape_by_text(slide, prefix: str):
    for sh in slide.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip().startswith(prefix):
            return sh
    raise KeyError(prefix)


def set_lines(shape, lines: list[str], size: float | None = None, label_bold: bool = True, space_after: float | None = None):
    """Replace the text of a template text box. Each line becomes one paragraph cloned from the first
    template paragraph (keeps alignment, bullets, font and colour). 'Label: text' renders the label bold;
    [bracketed] text is coloured as an unresolved placeholder."""
    tf = shape.text_frame
    txBody = tf._txBody
    paras = txBody.findall(qn("a:p"))
    proto_p = deepcopy(paras[0])
    first_r = proto_p.find(qn("a:r"))
    proto_rpr = deepcopy(first_r.find(qn("a:rPr"))) if first_r is not None and first_r.find(qn("a:rPr")) is not None else None
    for p in paras:
        txBody.remove(p)
    for line in lines:
        p = deepcopy(proto_p)
        for child in list(p):
            if child.tag in (qn("a:r"), qn("a:br"), qn("a:fld")):
                p.remove(child)
        end = p.find(qn("a:endParaRPr"))
        segments = []
        m = re.match(r"^([A-Z][A-Za-z ]{1,30}:)(\s.*)$", line) if label_bold else None
        if m:
            segments.append((m.group(1), True))
            line = m.group(2)
        for tok in re.split(r"(\[[^\]]*–[^\]]*\])", line):  # placeholders look like "[Panel – to be confirmed]"
            if tok:
                segments.append((tok, None))
        for text, bold in segments:
            r = p.makeelement(qn("a:r"), {})
            rpr = deepcopy(proto_rpr) if proto_rpr is not None else r.makeelement(qn("a:rPr"), {})
            if size:
                rpr.set("sz", str(int(size * 100)))
            if bold:
                rpr.set("b", "1")
            r.append(rpr)
            t = r.makeelement(qn("a:t"), {})
            t.text = text
            r.append(t)
            if end is not None:
                end.addprevious(r)
            else:
                p.append(r)
            if re.fullmatch(r"\[[^\]]*–[^\]]*\]", text):
                from pptx.text.text import _Run
                run = _Run(r, None)
                run.font.color.rgb = PLACEHOLDER_COLOR
        if space_after is not None:
            pPr = p.find(qn("a:pPr"))
            if pPr is None:
                pPr = p.makeelement(qn("a:pPr"), {})
                p.insert(0, pPr)
            for old in pPr.findall(qn("a:spcAft")):
                pPr.remove(old)
            spc = pPr.makeelement(qn("a:spcAft"), {})
            pts = spc.makeelement(qn("a:spcPts"), {"val": str(int(space_after * 100))})
            spc.append(pts)
            pPr.append(spc)
        txBody.append(p)
    tf.word_wrap = True


def replace_with_picture(slide, shape, image: Path, top: float | None = None, height: float | None = None):
    """Remove a template '[Insert ...]' box and place the image fitted (aspect preserved) and centred in it.
    top/height (inches) optionally enlarge the area vertically when the template leaves spare room."""
    left, width = shape.left, shape.width
    top = Inches(top) if top is not None else shape.top
    height = Inches(height) if height is not None else shape.height
    shape._element.getparent().remove(shape._element)
    with Image.open(image) as im:
        iw, ih = im.size
    scale = min(width / iw, height / ih)
    w, h = int(iw * scale), int(ih * scale)
    slide.shapes.add_picture(str(image), left + (width - w) // 2, top + (height - h) // 2, w, h)


def set_notes(slide, text: str):
    slide.notes_slide.notes_text_frame.text = text


def evidence():
    exps = {e["name"].split(".")[0].strip(): e for e in json.loads((EVID / "experiment_results.json").read_text(encoding="utf-8"))}
    be = len(re.findall(r"PASSED", (EVID / "backend_pytest.txt").read_text(encoding="utf-8")))
    fe = int(re.search(r"Tests\s+(\d+) passed", (EVID / "frontend_vitest.txt").read_text(encoding="utf-8")).group(1))
    ui = len(re.findall(r"^PASS ", (EVID / "ui_check.txt").read_text(encoding="utf-8"), re.M))
    return exps, be, fe, ui


def build(prs: Presentation) -> None:
    exps, be, fe, ui = evidence()
    va = {v["routing"]: v["metrics"] for v in exps["A"]["summary"]["variants"]}
    vb = {v["cache_policy"]: v["metrics"] for v in exps["B"]["summary"]["variants"]}
    fc = exps["C"]["summary"]["variants"][0]["failure"]
    s1, s2, s3, s4, s5, s6, s7, s8 = prs.slides

    # ---------------------------------------------------------------- 1 title and team
    set_lines(shape_by_text(s1, "[PROJECT TITLE]"), [TITLE], size=24)
    set_lines(shape_by_text(s1, "[Student 1"), [
        f"{TEAM[0][0]} – {TEAM[0][1]}     {TEAM[1][0]} – {TEAM[1][1]}",
        f"{TEAM[2][0]} – {TEAM[2][1]}     {TEAM[3][0]} – {TEAM[3][1]}"], size=14, label_bold=False)
    set_lines(shape_by_text(s1, "Group"), [
        "Group 2     Panel [Panel – to be confirmed]     Batch [Batch – to be confirmed]",
        "Faculty: Dr. Swati",
        "Academic Year: 2026–2027     Presentation date: [Date – to be confirmed]"], size=14, label_bold=False)
    set_notes(s1, "Suggested time: 15 seconds. Introduce the project title and Group 2 (four members, guided by "
                  "Dr. Swati). Do not read every PRN.")

    # ---------------------------------------------------------------- 2 problem and objectives
    set_lines(shape_by_text(s2, "[What networking problem"), [
        "A CDN must pick an edge per request: the nearest edge may be overloaded, degraded or down [4].",
        "Latency grows with distance and queueing; caching avoids origin trips but capacity is limited [5].",
    ], size=16, space_after=4)
    set_lines(shape_by_text(s2, "1. [Measurable"), [
        "1. Serve content over real HTTP/TCP via UDP resolution, with 6 routing strategies",
        "2. Reduce origin fetches with per-edge LRU / LFU / TTL caches",
        "3. Detect edge failures, reroute, and measure detection and recovery time",
    ], size=16, label_bold=False, space_after=4)
    set_notes(s2, "Suggested time: 40 seconds. The problem: choosing an edge that balances latency, load and health, "
                  "while caching popular content. Scope: a working miniature CDN with one origin and three edges whose "
                  "behaviour we can measure. Three measurable objectives as listed.")

    # ---------------------------------------------------------------- 3 design and tools
    replace_with_picture(s3, shape_by_text(s3, "[Insert a labelled topology"), FIG / "slide_architecture.png", top=1.3, height=2.95)
    set_lines(shape_by_text(s3, "Tools:"), [
        "Tools: Python 3.13 (asyncio, FastAPI, SQLite), React 18 + Vite, pytest / Vitest",
        "Protocols: HTTP/1.1 over TCP [1]; DNS-inspired CNRP over UDP [2], [3]; WebSocket",
        "Addressing: 127.0.0.1 – API :8000, resolver udp/8053, origin :9000, edges :9001–9003",
    ], size=15, space_after=6)
    set_notes(s3, "Suggested time: 45 seconds. Data plane: the client resolves cdn.sim.local over UDP, then downloads "
                  "from the selected edge over HTTP/1.1; on a miss the edge fetches from the origin. Each edge is its own "
                  "process with its own cache. Control plane: FastAPI with the routing engine, health checks, experiments "
                  "and SQLite, plus the React dashboard. Locations and link conditions are simulated in Local Mode.")

    # ---------------------------------------------------------------- 4 implementation and working
    replace_with_picture(s4, shape_by_text(s4, "[Insert one key configuration"), FIG / "slide_flow.png")
    set_lines(shape_by_text(s4, "Implementation:"), [
        "Implementation: origin + 3 edge processes, UDP resolver, routing engine, health checks",
        "Working: hybrid score = 0.5·latency + 0.4·load + 0.1·loss; unhealthy edges skipped",
        "Troubleshooting: Windows timer inflated an 18 ms link to ~30 ms; 1 ms timer fix gave 21.6 ms",
    ], size=14.5, space_after=6)
    set_notes(s4, "Suggested time: 55 seconds. Walk through the request: resolve, request, cache lookup, hit or miss. "
                  "Concurrent misses for the same object share one origin fetch. Hybrid routing combines measured probe "
                  "RTT, load and estimated packet loss. Troubleshooting example: asyncio sleeps on Windows were quantised "
                  "to 15.6 ms; requesting a 1 ms timer (and opting child processes out of timer throttling) fixed the "
                  "simulated delays, verified by probe RTTs of 21.6 / 46.4 / 114.1 ms for 18 / 42 / 110 ms profiles.")

    # ---------------------------------------------------------------- 5 testing and results
    replace_with_picture(s5, shape_by_text(s5, "[Insert a readable screenshot"), FIG / "slide_results.png", top=1.35, height=2.6)
    set_lines(shape_by_text(s5, "Test:"), [
        f"Test: {be + fe} automated tests + {ui} browser checks; 4 experiments, Local Mode, seed 7",
        "Expected: hybrid beats single-metric routing under load; caching cuts origin fetches",
        f"Observed: hybrid {va['hybrid']['avg_ms']:.0f} ms vs nearest {va['nearest']['avg_ms']:.0f} ms avg; "
        f"origin fetches {vb['none']['origin_fetches']:.0f} → {vb['lfu']['origin_fetches']:.0f} (LFU); "
        f"failure detected in {fc['detection_ms']:.1f} ms",
        "Objectives achieved: O1–O3 achieved; Mininet run not tested (simulated links)",
    ], size=14, space_after=4)
    set_notes(s5, f"Suggested time: 50 seconds. All {be + fe} automated tests pass ({be} backend, {fe} frontend). "
                  f"Experiment A (400 requests per strategy, concurrency 24, mean of 3): hybrid {va['hybrid']['avg_ms']:.0f} ms "
                  f"average and {va['hybrid']['throughput_rps']:.0f} req/s versus nearest {va['nearest']['avg_ms']:.0f} ms, "
                  "because the nearest edge has only 8 slots and queues. Experiment B (2 MB caches): origin fetches fell "
                  f"from {vb['none']['origin_fetches']:.0f} to about {vb['lfu']['origin_fetches']:.0f}. Experiment C: India "
                  f"failure detected passively in {fc['detection_ms']:.1f} ms, {fc['failed_requests']} in-flight requests failed, "
                  f"recovery {fc['recovery_ms']:.0f} ms. These are simulator measurements on one machine, not real-CDN claims.")

    # ---------------------------------------------------------------- 6 demonstration
    set_lines(shape_by_text(s6, "[State the core feature"), [
        "Cache miss → hit on one edge, then an edge failure with automatic rerouting"], size=18, label_bold=False)
    set_lines(shape_by_text(s6, "1. [Launch"), [
        "1. Start backend + dashboard: Overview shows Live and 3 healthy edges",
        "2. Routing Lab: select Health-aware failover; send index.html twice",
        "3. Edge Servers: simulate India failure; Routing Lab: send again",
    ], size=17, label_bold=False, space_after=6)
    set_lines(shape_by_text(s6, "Success criterion:"), [
        "Success criterion: request 2 shows X-Cache HIT; after the failure India is Unhealthy and request 3 "
        "returns HTTP 200 from Singapore"], size=15)
    set_notes(s6, "Suggested time: 65 seconds. Start the backend and frontend before the timer. Rehearsed on 20 Oct "
                  "2026 (submission/evidence/demo_rehearsal.txt): request 1 MISS on india-edge (50.5 ms), request 2 HIT "
                  "(21.8 ms); after simulating the failure India was marked unhealthy in 2.35 s and request 3 returned "
                  "HTTP 200 from singapore-edge with reason 'excluded unhealthy: india-edge'. Restore the edge afterwards. "
                  "Backup: show the Experiments page for the stored failure experiment (identify it as a recorded result).")

    # ---------------------------------------------------------------- 7 conclusion
    set_lines(shape_by_text(s7, "Outcome:"), [
        "Outcome: working mini-CDN – real TCP/UDP, 6 routing strategies, 3 cache policies",
        "Application: teaching and prototyping CDN routing and DNS-TTL failover",
        "Limitation: one machine; latency and loss are simulated; Mininet not run",
        "Future scope: run on Mininet with tc/netem links and verify in Wireshark",
    ], size=16, space_after=3)
    set_lines(shape_by_text(s7, "Team contributions:"), [
        "Team contributions: [Name – responsibility, to be completed by the team]",
        "[Contributions are not recorded in the repository – to be completed]",
    ], size=14)
    set_lines(shape_by_text(s7, "AI use:"), [
        "AI use: Claude Code (Anthropic) generated code, tests, docs and drafts; team must review and verify"],
        size=13)
    set_notes(s7, "Suggested time: 30 seconds. Summarise the outcome, one application, the main limitation (single "
                  "machine with simulated links) and the next step (Mininet + Wireshark). State each member's actual "
                  "contribution (to be filled in by the team) and the AI-use declaration.")

    # ---------------------------------------------------------------- 8 references
    set_lines(shape_by_text(s8, "[1]"), [
        "[1] R. Fielding et al., “HTTP Semantics,” IETF RFC 9110, Jun. 2022.",
        "[2] P. Mockapetris, “Domain Names – Implementation and Specification,” IETF RFC 1035, Nov. 1987.",
        "[3] J. Postel, “User Datagram Protocol,” IETF RFC 768, Aug. 1980.",
        "[4] E. Nygren, R. K. Sitaraman, J. Sun, “The Akamai network,” ACM SIGOPS OSR, 44(3), 2010, "
        "doi: 10.1145/1842733.1842736.",
        "[5] L. Breslau et al., “Web caching and Zipf-like distributions,” IEEE INFOCOM ’99, pp. 126–134, "
        "doi: 10.1109/INFCOM.1999.749260.",
        "[6] B. Lantz, B. Heller, N. McKeown, “A network in a laptop,” ACM HotNets-IX, 2010, "
        "doi: 10.1145/1868447.1868466.",
    ], size=13, label_bold=False, space_after=5)
    set_lines(shape_by_text(s8, "Use IEEE-style"), [
        "Full IEEE reference list, evidence and source: project report and repository docs/"], size=14, label_bold=False)
    set_notes(s8, "Backup slide for questions. References cited on slides 2, 3 and 5; the report contains the "
                  "complete list.")


def export_pdf(pptx: Path, pdf: Path) -> bool:
    ps = f"""
$ErrorActionPreference = 'Stop'
$pp = New-Object -ComObject PowerPoint.Application
try {{
  $p = $pp.Presentations.Open('{pptx}', $true, $false, $false)
  $p.SaveAs('{pdf}', 32)
  Write-Output ("slides=" + $p.Slides.Count)
  $p.Close()
}} finally {{ $pp.Quit() }}
"""
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=300)
    print(res.stdout.strip(), res.stderr.strip()[:400])
    return res.returncode == 0 and pdf.exists()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="also export a PDF preview to this path (Microsoft PowerPoint required)")
    args = ap.parse_args()
    prs = Presentation(str(TEMPLATE))
    build(prs)
    OUT.parent.mkdir(exist_ok=True)
    prs.save(str(OUT))
    print("wrote", OUT)
    if args.pdf:
        print("PDF preview:", "OK" if export_pdf(OUT, Path(args.pdf)) else "FAILED")


if __name__ == "__main__":
    main()
