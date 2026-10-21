"""Generate diagrams and result charts for the report and presentation.

Charts are drawn only from submission/evidence/experiment_results.json (results exported from the
running system's SQLite database via the API); no values are typed in by hand.

    .venv-docs/Scripts/python scripts/make_figures.py
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "submission" / "figures"
DATA = ROOT / "submission" / "evidence" / "experiment_results.json"

NAVY = "#20375C"
INK = "#1F2937"
MUTED = "#64748B"
LINE = "#94A3B8"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
EDGE_COLORS = {"india-edge": SERIES[0], "singapore-edge": SERIES[1], "usa-edge": SERIES[2]}
STRATEGY_LABELS = {"random": "Random", "nearest": "Nearest replica", "lowest_latency": "Lowest latency",
                   "least_loaded": "Least loaded", "hybrid": "Hybrid", "failover": "Failover"}
POLICY_LABELS = {"none": "Disabled", "lru": "LRU", "lfu": "LFU", "ttl": "TTL (10 s)"}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": "#CBD5E1", "axes.labelcolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#EEF1F5", "grid.linewidth": 0.8, "axes.axisbelow": True,
    "legend.frameon": False, "savefig.dpi": 200, "savefig.bbox": "tight", "savefig.facecolor": "white",
})


def box(ax, x, y, w, h, title, sub="", fill="#FFFFFF", edge=NAVY, dashed=False, title_size=10, sub_size=8):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", linewidth=1.3,
                                edgecolor=edge, facecolor=fill, linestyle="--" if dashed else "-"))
    ax.text(x + w / 2, y + h * (0.67 if sub else 0.5), title, ha="center", va="center", fontsize=title_size,
            color=INK, fontweight="bold")
    if sub:
        ax.text(x + w / 2, y + h * (0.27 if "\n" in sub else 0.3), sub, ha="center", va="center", fontsize=sub_size,
                color=MUTED)


def arrow(ax, p, q, label="", color=NAVY, style="-|>", ls="-", lw=1.4, rad=0.0, label_pos=0.5, dy=0.12, fs=8):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=12, color=color, lw=lw, linestyle=ls,
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=2, shrinkB=2))
    if label:
        x = p[0] + (q[0] - p[0]) * label_pos
        y = p[1] + (q[1] - p[1]) * label_pos + dy
        ax.text(x, y, label, ha="center", va="center", fontsize=fs, color=color,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))


def architecture():
    fig, ax = plt.subplots(figsize=(11, 7.0))
    ax.set_xlim(0, 11)
    ax.set_ylim(-0.3, 7.0)
    ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.15, 4.35), 10.7, 2.5, boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc="#F3F6FB", ec="#D5DEEB", lw=1))
    ax.text(0.35, 6.6, "CONTROL PLANE  (FastAPI process, 127.0.0.1:8000)", fontsize=9, color=NAVY, fontweight="bold")
    ax.add_patch(FancyBboxPatch((0.15, 0.15), 10.7, 3.85, boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc="#FBFBFB", ec="#E2E8F0", lw=1))
    ax.text(0.35, 3.75, "DATA PLANE  (independent server processes, real TCP/UDP sockets)", fontsize=9, color=NAVY,
            fontweight="bold")
    ax.text(5.5, -0.15, "Local Mode shown (all processes on 127.0.0.1). Mininet Mode runs the same processes on Mininet "
            "hosts with tc/netem-shaped links (script implemented, not executed).", fontsize=7.5, color=MUTED,
            ha="center", style="italic")

    box(ax, 0.4, 5.45, 1.9, 0.85, "React dashboard", "Vite :5173 · 7 pages")
    box(ax, 2.8, 5.45, 2.4, 0.85, "API + WebSocket", "/api/*  ·  /ws/metrics")
    box(ax, 5.6, 5.45, 2.3, 0.85, "Experiment runner", "traffic generator (client)")
    box(ax, 8.4, 5.45, 2.2, 0.85, "SQLite", "experiments · runs · requests")
    box(ax, 2.8, 4.55, 2.4, 0.7, "Routing engine", "6 strategies + health filter", sub_size=7.5)
    box(ax, 5.6, 4.55, 2.3, 0.7, "Cluster manager", "health probes · stats polling", sub_size=7.5)
    box(ax, 8.4, 4.55, 2.2, 0.7, "Live metrics", "P50/P95/P99 · hit ratio", sub_size=7.5)
    arrow(ax, (2.3, 5.87), (2.8, 5.87))
    arrow(ax, (5.2, 5.87), (5.6, 5.87))
    arrow(ax, (7.9, 5.87), (8.4, 5.87))

    box(ax, 0.4, 1.35, 1.9, 0.95, "Client", "traffic generator\nor cdn_client.py", sub_size=7.5)
    box(ax, 2.8, 2.55, 2.4, 0.8, "CNRP resolver", "UDP :8053", fill="#EEF6FF")
    edges = [("India edge", ":9001 · own cache", 2.6), ("Singapore edge", ":9002 · own cache", 1.5),
             ("USA edge", ":9003 · own cache", 0.4)]
    for name, sub, y in edges:
        box(ax, 5.6, y, 2.3, 0.8, name, sub)
    box(ax, 8.6, 1.5, 2.0, 0.8, "Origin server", "TCP :9000 · content/")

    arrow(ax, (1.35, 2.3), (2.8, 2.95), "", color="#0284C7", style="<|-|>")
    ax.text(1.2, 2.95, "1-2  CNRP query / answer\n(UDP: edge IP, port, TTL)", fontsize=7.5, color="#0284C7", ha="left")
    for _, _, y in edges:
        arrow(ax, (2.3, 1.82), (5.6, y + 0.4), "", color=NAVY, lw=1.2)
    ax.text(3.2, 0.55, "3  HTTP/1.1 GET over TCP\n5  response (X-Cache: HIT / MISS)", fontsize=7.5, color=NAVY, ha="left")
    for _, _, y in edges:
        arrow(ax, (7.9, y + 0.4), (8.6, 1.9), "", color=MUTED, lw=1.1)
    ax.text(9.6, 0.75, "4  cache miss:\norigin fetch (TCP)", fontsize=7.5, color=MUTED, ha="center")
    arrow(ax, (4.0, 4.55), (4.0, 3.35), "", color=NAVY)
    ax.text(4.1, 4.05, "routing decision", fontsize=7.5, color=NAVY, ha="left")
    arrow(ax, (6.75, 4.55), (6.75, 3.4), "", color=MUTED, ls="--")
    ax.text(6.85, 4.05, "probes / admin (TCP)", fontsize=7.5, color=MUTED, ha="left")
    fig.savefig(OUT / "fig_architecture.png")
    plt.close(fig)


def sequence():
    lanes = ["Client", "CNRP resolver\n(+ routing engine)", "Edge server", "Edge cache", "Origin", "Control plane\nmetrics"]
    xs = [0.8 + i * 1.75 for i in range(len(lanes))]
    fig, ax = plt.subplots(figsize=(11, 5.6))
    ax.set_xlim(0, 10.6)
    ax.set_ylim(0, 6.2)
    ax.axis("off")
    for x, name in zip(xs, lanes):
        box(ax, x - 0.7, 5.45, 1.4, 0.6, name.split("\n")[0], name.split("\n")[1] if "\n" in name else "",
            title_size=9, sub_size=7)
        ax.plot([x, x], [0.25, 5.45], color="#CBD5E1", lw=1, ls="--", zorder=0)
    steps = [
        (0, 1, 5.05, "1  CNRQ query (UDP): cdn.sim.local", "#0284C7"),
        (1, 0, 4.65, "2  CNRR answer: edge IP:port, TTL, reason", "#0284C7"),
        (0, 2, 4.2, "3  GET /content/<id> (HTTP/1.1, TCP)", NAVY),
        (2, 3, 3.8, "4  lookup (LRU / LFU / TTL)", NAVY),
        (3, 2, 3.4, "5a HIT: cached bytes", "#16A34A"),
        (2, 4, 2.9, "5b MISS: GET /content/<id>", MUTED),
        (4, 2, 2.5, "6  200 OK + body", MUTED),
        (2, 3, 2.1, "7  insert (evict to fit capacity)", NAVY),
        (2, 0, 1.6, "8  200 OK, X-Cache, Content-Length", NAVY),
        (0, 5, 1.05, "9  record latency, cache status, edge", "#7C3AED"),
    ]
    for a, b, y, label, color in steps:
        arrow(ax, (xs[a], y), (xs[b], y), "", color=color, lw=1.3)
        ax.text((xs[a] + xs[b]) / 2, y + 0.13, label, ha="center", fontsize=8, color=color,
                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none"))
    ax.text(xs[2] + 0.1, 0.45, "Concurrent misses for the same object are coalesced into one origin fetch.",
            fontsize=8, color=MUTED, ha="center", style="italic")
    fig.savefig(OUT / "fig_request_flow.png")
    plt.close(fig)


def load():
    return {e["name"][:2].strip(". "): e for e in json.loads(DATA.read_text(encoding="utf-8"))}


def routing_chart(exps):
    v = exps["A"]["summary"]["variants"]
    labels = [STRATEGY_LABELS[x["routing"]] for x in v]
    avg = [x["metrics"]["avg_ms"] for x in v]
    p95 = [x["metrics"]["p95_ms"] for x in v]
    thr = [x["metrics"]["throughput_rps"] for x in v]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw={"width_ratios": [1.5, 1]})
    y = range(len(labels))
    h = 0.36
    a1.barh([i - h / 2 for i in y], avg, height=h, color=SERIES[0], label="Average")
    a1.barh([i + h / 2 for i in y], p95, height=h, color=SERIES[1], label="P95")
    a1.set_yticks(list(y), labels)
    a1.invert_yaxis()
    a1.set_xlabel("Latency (ms), client-measured")
    a1.grid(axis="y", visible=False)
    a1.legend(loc="lower right", fontsize=9)
    for i, (m, p) in enumerate(zip(avg, p95)):
        a1.text(m + 10, i - h / 2, f"{m:.0f}", va="center", fontsize=8, color=INK)
        a1.text(p + 10, i + h / 2, f"{p:.0f}", va="center", fontsize=8, color=INK)
    a1.set_title("Latency by routing strategy", loc="left", fontsize=11, color=INK, fontweight="bold")
    a2.barh(list(y), thr, height=0.5, color=SERIES[0])
    a2.set_yticks(list(y), [""] * len(labels))
    a2.invert_yaxis()
    a2.grid(axis="y", visible=False)
    a2.set_xlabel("Throughput (successful req/s)")
    for i, t in enumerate(thr):
        a2.text(t + 2, i, f"{t:.1f}", va="center", fontsize=8, color=INK)
    a2.set_title("Throughput", loc="left", fontsize=11, color=INK, fontweight="bold")
    fig.text(0.01, -0.04, "Experiment A: 400 requests per strategy, concurrency 24, Zipf (s = 1.1), seed 7, "
             "mean of 3 repetitions. Local Mode with simulated link profiles.", fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(OUT / "fig_routing_results.png")
    plt.close(fig)


def cache_chart(exps):
    v = exps["B"]["summary"]["variants"]
    labels = [POLICY_LABELS[x["cache_policy"]] for x in v]
    fetches = [x["metrics"]["origin_fetches"] for x in v]
    hit = [(x["metrics"]["hit_ratio"] or 0) * 100 for x in v]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.4))
    a1.bar(labels, fetches, color=SERIES[0], width=0.55)
    a1.set_ylabel("Origin fetches per 400 requests")
    a1.grid(axis="x", visible=False)
    for i, f in enumerate(fetches):
        a1.text(i, f + 6, f"{f:.0f}", ha="center", fontsize=8, color=INK)
    a1.set_title("Origin fetches by cache policy", loc="left", fontsize=11, color=INK, fontweight="bold")
    a2.bar(labels[1:], hit[1:], color=SERIES[2], width=0.5)
    a2.set_ylabel("Cache-hit ratio (%)")
    a2.set_ylim(0, max(hit) * 1.3)
    a2.grid(axis="x", visible=False)
    for i, h in enumerate(hit[1:]):
        a2.text(i, h + 0.6, f"{h:.1f}%", ha="center", fontsize=8, color=INK)
    a2.set_title("Hit ratio (caching enabled)", loc="left", fontsize=11, color=INK, fontweight="bold")
    fig.text(0.01, -0.05, "Experiment B: 400 requests per policy, nearest routing, 2 MB cache per edge, Zipf, seed 7, "
             "mean of 3 repetitions. 'Disabled' performs no lookups, so it has no hit ratio.", fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(OUT / "fig_cache_results.png")
    plt.close(fig)


def latency_timeline_chart(exps, keys, out, title, note):
    fig, axes = plt.subplots(1, len(keys), figsize=(11, 3.3))
    for ax, k in zip(axes, keys):
        e = exps[k]
        run = e["runs"][0]["summary"]
        tl = [b for b in run["timeline"] if b["avg_ms"] is not None]
        ax.plot([b["t"] for b in tl], [b["avg_ms"] / 1000 for b in tl], lw=2, color=SERIES[0])
        ax.set_ylim(bottom=0)
        for ev in run["events"]:
            ax.axvline(ev["t"], color="#DC2626", ls="--", lw=1)
            ax.text(ev["t"] + 0.01 * (tl[-1]["t"] or 1), ax.get_ylim()[1] * 0.9, "degraded", fontsize=8, color=MUTED)
        share = run["per_edge"].get("india-edge", {}).get("share") or 0
        ax.set_title(f"{e['config']['routing_strategy'].replace('_', ' ')}  (India share {share * 100:.0f}%, "
                     f"{run['duration_s']:.0f} s run)", loc="left", fontsize=10, color=INK, fontweight="bold")
        ax.set_xlabel("Time since run start (s)")
        ax.set_ylabel("Average latency per bucket (s)")
    fig.suptitle(title, x=0.01, ha="left", fontsize=11, color=INK, fontweight="bold")
    fig.text(0.01, -0.04, note, fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(OUT / out)
    plt.close(fig)


def timeline_chart(exps, keys, out, title, event_label):
    fig, axes = plt.subplots(1, len(keys), figsize=(11, 3.3), sharey=False)
    for ax, k in zip(axes, keys):
        e = exps[k]
        run = e["runs"][0]["summary"]
        tl = [b for b in run["timeline"] if b["t"] <= 14]
        edges = sorted({eid for b in tl for eid in b["per_edge"]})
        for eid in edges:
            ax.plot([b["t"] for b in tl], [b["per_edge"].get(eid, 0) for b in tl], lw=2, color=EDGE_COLORS.get(eid, LINE),
                    label=eid.replace("-edge", "").title().replace("Usa", "USA"))
        for ev in run["events"]:
            ax.axvline(ev["t"], color="#DC2626" if "restored" not in ev["kind"] else "#16A34A", ls="--", lw=1)
            ax.text(ev["t"] + 0.1, ax.get_ylim()[1] * 0.92 if ax.get_ylim()[1] else 1,
                    ev["kind"].replace("edge_", "").replace("link_", ""), fontsize=8, color=MUTED)
        sub = e["config"]["routing_strategy"].replace("_", " ")
        res = " · DNS-style TTL" if e["config"]["workload"]["resolution"] == "dns" else ""
        ax.set_title(f"{sub}{res}", loc="left", fontsize=10, color=INK, fontweight="bold")
        ax.set_xlabel("Time since run start (s)")
        ax.set_ylabel("Requests completed per bucket")
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=8, loc="center right")
    fig.suptitle(title, x=0.01, ha="left", fontsize=11, color=INK, fontweight="bold")
    fig.text(0.01, -0.04, event_label, fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(OUT / out)
    plt.close(fig)


# ----------------------------------------------------------------------------- slide figures (wide, large text)

def slide_architecture():
    fig, ax = plt.subplots(figsize=(12, 3.9))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 3.9)
    ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.05, 3.08), 11.9, 0.76, boxstyle="round,pad=0.02,rounding_size=0.1", fc="#F3F6FB", ec="#D5DEEB"))
    ax.text(0.2, 3.46, "Control\nplane", fontsize=12, color=NAVY, fontweight="bold", va="center")
    for x, t in [(1.3, "Dashboard"), (3.95, "FastAPI + WebSocket"), (6.6, "Routing + health"), (9.25, "Experiments + SQLite")]:
        box(ax, x, 3.16, 2.45, 0.6, t, title_size=10.5)
    box(ax, 0.1, 0.95, 1.9, 1.1, "Client", "traffic generator", title_size=14, sub_size=10.5)
    box(ax, 2.9, 1.95, 2.3, 0.85, "CNRP resolver", "UDP :8053", fill="#EEF6FF", title_size=13, sub_size=10.5)
    ys = [1.95, 1.0, 0.05]
    for (n, port), y in zip([("India edge", ":9001"), ("Singapore edge", ":9002"), ("USA edge", ":9003")], ys):
        box(ax, 6.2, y, 2.4, 0.8, n, f"{port} · own cache", title_size=13, sub_size=10.5)
    box(ax, 9.7, 0.95, 2.2, 1.0, "Origin", "TCP :9000", title_size=14, sub_size=10.5)
    arrow(ax, (2.0, 1.75), (2.9, 2.3), "", color="#0284C7", style="<|-|>", lw=2)
    ax.text(2.0, 2.55, "1 UDP\nresolve", fontsize=11, color="#0284C7", ha="center")
    for y in ys:
        arrow(ax, (2.0, 1.4), (6.2, y + 0.4), "", color=NAVY, lw=1.8)
    ax.text(4.1, 0.5, "2 HTTP/1.1 GET (TCP)", fontsize=11.5, color=NAVY, ha="center")
    for y in ys:
        arrow(ax, (8.6, y + 0.4), (9.7, 1.45), "", color=MUTED, lw=1.5)
    ax.text(10.8, 2.25, "3 miss: origin fetch", fontsize=11, color=MUTED, ha="center")
    arrow(ax, (7.3, 3.16), (5.2, 2.65), "", color=NAVY, ls="--", lw=1.4)
    ax.text(7.45, 2.86, "routing decision", fontsize=10.5, color=NAVY)
    fig.savefig(OUT / "slide_architecture.png")
    plt.close(fig)


def slide_flow():
    fig, ax = plt.subplots(figsize=(12, 3.2))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 3.2)
    ax.axis("off")
    steps = [("1  Resolve", "UDP CNRQ → CNRR\nedge chosen by strategy"), ("2  Request", "GET /content/<id>\nHTTP/1.1 over TCP"),
             ("3  Cache lookup", "LRU / LFU / TTL\nper edge")]
    for i, (t, sub) in enumerate(steps):
        box(ax, 0.1 + i * 2.95, 1.2, 2.55, 1.2, t, sub, title_size=14, sub_size=11)
        if i:
            arrow(ax, (0.1 + i * 2.95 - 0.4, 1.8), (0.1 + i * 2.95, 1.8), "", lw=2)
    box(ax, 9.0, 2.05, 2.9, 1.0, "HIT", "serve cached bytes", fill="#F0FDF4", edge="#16A34A", title_size=14, sub_size=11)
    box(ax, 9.0, 0.6, 2.9, 1.1, "MISS", "fetch origin (coalesced),\ninsert + evict to fit", fill="#FFF7ED", edge="#D97706",
        title_size=14, sub_size=11)
    arrow(ax, (8.55, 1.95), (9.0, 2.5), "", color="#16A34A", lw=2)
    arrow(ax, (8.55, 1.65), (9.0, 1.15), "", color="#D97706", lw=2)
    ax.text(6.0, 0.25, "4  Response with X-Cache header → client records latency, edge and cache status → live metrics + SQLite",
            fontsize=12, color=NAVY, ha="center")
    fig.savefig(OUT / "slide_flow.png")
    plt.close(fig)


def slide_results(exps):
    a = exps["A"]["summary"]["variants"]
    b = exps["B"]["summary"]["variants"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 3.3), gridspec_kw={"width_ratios": [1.35, 1]})
    names = [STRATEGY_LABELS[v["routing"]] for v in a]
    vals = [v["metrics"]["avg_ms"] for v in a]
    colors = [SERIES[0] if v["routing"] == "hybrid" else "#9DB9E5" for v in a]
    a1.barh(names, vals, color=colors, height=0.6)
    a1.invert_yaxis()
    a1.grid(axis="y", visible=False)
    a1.tick_params(labelsize=12)
    for i, v in enumerate(vals):
        a1.text(v + 6, i, f"{v:.0f} ms", va="center", fontsize=12, color=INK)
    a1.set_xlim(0, max(vals) * 1.22)
    a1.set_title("Average latency by routing strategy (A)", loc="left", fontsize=13.5, color=INK, fontweight="bold")
    pn = [POLICY_LABELS[v["cache_policy"]].replace(" (10 s)", "") for v in b]
    pv = [v["metrics"]["origin_fetches"] for v in b]
    a2.bar(pn, pv, color=[ "#9DB9E5" if v["cache_policy"] == "none" else SERIES[2] for v in b], width=0.6)
    a2.grid(axis="x", visible=False)
    a2.tick_params(labelsize=12)
    for i, v in enumerate(pv):
        a2.text(i, v + 8, f"{v:.0f}", ha="center", fontsize=12, color=INK)
    a2.set_ylim(0, max(pv) * 1.2)
    a2.set_title("Origin fetches per 400 requests (B)", loc="left", fontsize=13.5, color=INK, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "slide_results.png")
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    architecture()
    sequence()
    exps = load()
    routing_chart(exps)
    cache_chart(exps)
    timeline_chart(exps, ["C", "C2"], "fig_failure_timeline.png", "Experiment C: India edge failure at t = 3 s, restore at t = 7 s",
                   "600 requests at 60 req/s, nearest routing. Left: per-request resolution; right: DNS-style resolution (5 s TTL).")
    latency_timeline_chart(exps, ["D", "D2"], "fig_degradation_timeline.png",
                           "Experiment D: India link degraded to 180 ms / 2 % loss at t = 3 s (600 requests at 60 req/s)",
                           "Left: lowest-latency routing shifts traffic to Singapore and latency recovers. Right: nearest routing stays on "
                           "the degraded link for the whole run (note the different time and latency scales).")
    slide_architecture()
    slide_flow()
    slide_results(exps)
    print("figures written to", OUT)
