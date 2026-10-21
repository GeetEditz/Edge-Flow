import { useState } from "react";
import { Cpu, Globe, Laptop, Radio, Server } from "lucide-react";
import { api } from "../api/client";
import type { Topology } from "../api/types";
import { Badge, ErrorAlert, KindTag, Note, PageHeader, Panel, Skeleton, StatusBadge } from "../components/ui";
import { edgeColor, fmtMs, fmtPct, shortEdge } from "../lib/format";
import { useApi } from "../lib/hooks";

const W = 1040;
const H = 460;
const NODE_W = 168;
const NODE_H = 56;

function layout(t: Topology): Record<string, { x: number; y: number }> {
  const edges = t.nodes.filter((n) => n.kind === "edge");
  const pos: Record<string, { x: number; y: number }> = {
    client: { x: 100, y: H / 2 }, resolver: { x: 330, y: 56 }, control: { x: 330, y: H - 46 }, origin: { x: W - 100, y: H / 2 },
  };
  const top = 110;
  const gap = edges.length > 1 ? (H - 2 * top) / (edges.length - 1) : 0;
  edges.forEach((e, i) => { pos[e.id] = { x: 700, y: edges.length > 1 ? top + i * gap : H / 2 }; });
  return pos;
}

const ICONS: Record<string, typeof Server> = { client: Laptop, resolver: Radio, control: Cpu, origin: Globe, edge: Server };

export function TopologyPage() {
  const topo = useApi(api.topology, [], 2000);
  const [sel, setSel] = useState<string | null>(null);
  const t = topo.data;

  return (
    <>
      <PageHeader title="Network Topology" description="Hosts, protocols and link conditions of the CDN. Link labels show the simulated profile and the measured probe RTT." />
      {topo.error && <div style={{ marginBottom: 16 }}><ErrorAlert message={topo.error} onRetry={topo.reload} /></div>}
      {t && (
        <div className={`alert ${t.kind === "running-mininet" ? "info" : "warning"}`} style={{ marginBottom: 16 }}>
          <span style={{ flex: 1 }}>
            <b>{t.kind === "running-mininet" ? "Running Mininet topology." : "Conceptual topology (Local Mode)."}</b> {t.note}
          </span>
        </div>
      )}
      <div className="stack">
        <Panel title="Topology" subtitle="Click a node for details" actions={
          <div className="legend">
            <span><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#2563eb" strokeWidth="2" /></svg>HTTP/TCP content</span>
            <span><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#0284c7" strokeWidth="2" strokeDasharray="5 3" /></svg>UDP CNRP</span>
            <span><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#cbd5e1" strokeWidth="1.5" strokeDasharray="2 3" /></svg>Control</span>
          </div>}>
          {!t ? <Skeleton height={420} /> : <TopologySvg t={t} sel={sel} onSelect={setSel} />}
        </Panel>
        <div className="grid cols-2" style={{ alignItems: "start" }}>
          <NodeDetail t={t} id={sel} />
          <Panel title="Endpoints" flush>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Node</th><th>Address</th><th>Status</th></tr></thead>
                <tbody>
                  {t?.nodes.map((n) => (
                    <tr key={n.id} className={`clickable${sel === n.id ? " selected" : ""}`} onClick={() => setSel(n.id)}>
                      <td style={{ fontWeight: 500 }}>{n.label}</td>
                      <td className="mono faint" style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={n.detail}>{n.detail?.split(" · ")[0]}</td>
                      <td><StatusBadge status={n.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>
      <div className="mt-16">
        <Panel title="Client → edge links" subtitle="Simulated link conditions versus measured round-trip time" flush>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Link</th><th className="r">Base latency <KindTag kind="simulated" /></th><th className="r">Jitter</th><th className="r">Bandwidth</th><th className="r">Packet loss</th><th className="r">Probe RTT <KindTag kind="measured" /></th><th className="r">Traffic share (60 s)</th><th>Status</th></tr></thead>
              <tbody>
                {t?.links.filter((l) => l.source === "client" && l.profile).map((l) => (
                  <tr key={l.target}>
                    <td><span className="row"><i className="swatch" style={{ background: edgeColor(l.target) }} />Client → {shortEdge(l.target)}</span></td>
                    <td className="r">{l.profile!.base_latency_ms} ms</td>
                    <td className="r">± {l.profile!.jitter_ms} ms</td>
                    <td className="r">{l.profile!.bandwidth_mbps} Mbps</td>
                    <td className="r">{fmtPct(l.profile!.packet_loss, 2)}</td>
                    <td className="r">{fmtMs(l.measured_rtt_ms)}</td>
                    <td className="r">{fmtPct(l.share, 0)}</td>
                    <td><StatusBadge status={l.status ?? "unknown"} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <div className="mt-16"><Note>To run on real links, start the Mininet topology (see mininet/README.md); the dashboard then labels the view as a running Mininet topology and edges report zero simulated delay.</Note></div>
      </div>
    </>
  );
}

function TopologySvg({ t, sel, onSelect }: { t: Topology; sel: string | null; onSelect: (id: string) => void }) {
  const pos = layout(t);
  const statusColor = (s?: string) => (s === "unhealthy" ? "#dc2626" : s === "healthy" ? "#16a34a" : "#94a3b8");
  const anchor = (id: string, toward: string) => {
    const a = pos[id], b = pos[toward];
    const dx = b.x - a.x;
    if (Math.abs(dx) > 60) return { x: a.x + Math.sign(dx) * NODE_W / 2, y: a.y };
    return { x: a.x, y: a.y + Math.sign(b.y - a.y) * NODE_H / 2 };
  };
  return (
    <svg className="topo-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="CDN topology diagram">
      {t.links.map((l, i) => {
        if (!pos[l.source] || !pos[l.target]) return null;
        const a = anchor(l.source, l.target), b = anchor(l.target, l.source);
        const control = l.source === "control";
        const udp = l.protocol === "UDP";
        const content = l.source === "client" && l.target !== "resolver";
        const down = l.status === "unhealthy";
        const stroke = control ? "#cbd5e1" : udp ? "#0284c7" : down ? "#dc2626" : content ? "#2563eb" : "#94a3b8";
        const width = content ? 1.5 + 3 * (l.share ?? 0) : control ? 1.2 : 1.6;
        const midX = (a.x + b.x) / 2, midY = (a.y + b.y) / 2;
        const path = control || udp ? `M${a.x},${a.y} L${b.x},${b.y}` : `M${a.x},${a.y} C${midX},${a.y} ${midX},${b.y} ${b.x},${b.y}`;
        return (
          <g key={i}>
            <path d={path} fill="none" stroke={stroke} strokeWidth={width} strokeDasharray={control ? "2 4" : udp ? "6 4" : down ? "6 4" : undefined} opacity={control ? 0.9 : 1} />
            {content && l.profile && (
              <g transform={`translate(${b.x - 14},${b.y > a.y + 20 ? b.y + 18 : b.y - 24})`}>
                <text fontSize="11.5" fill="#475569" textAnchor="end">{l.profile.base_latency_ms} ms · {l.profile.bandwidth_mbps} Mbps · {fmtPct(l.profile.packet_loss, 1)} loss</text>
                <text fontSize="11" fill="#94a3b8" textAnchor="end" y="14">RTT {fmtMs(l.measured_rtt_ms, 0)} measured</text>
              </g>
            )}
            {udp && <text x={midX - 40} y={midY} fontSize="11" fill="#0284c7" textAnchor="middle">UDP {l.label.split(" ")[0]}</text>}
          </g>
        );
      })}
      {t.nodes.map((n) => {
        const p = pos[n.id];
        if (!p) return null;
        const Icon = ICONS[n.kind] ?? Server;
        return (
          <g key={n.id} className={`topo-node${sel === n.id ? " sel" : ""}`} transform={`translate(${p.x - NODE_W / 2},${p.y - NODE_H / 2})`}
            style={{ cursor: "pointer" }} onClick={() => onSelect(n.id)} tabIndex={0} role="button" aria-label={`${n.label}, ${n.status}`}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(n.id)}>
            <rect width={NODE_W} height={NODE_H} rx={6} />
            {n.kind === "edge" && <rect width={3} height={NODE_H - 16} x={0} y={8} rx={1.5} fill={edgeColor(n.id)} stroke="none" />}
            <foreignObject x={12} y={18} width={20} height={20}><Icon size={18} color="#64748b" strokeWidth={1.8} /></foreignObject>
            <text x={40} y={24} fontSize="13" fontWeight="500" fill="#111827">{n.label}</text>
            <text x={40} y={41} fontSize="11" fill="#94a3b8">{truncate((n.detail ?? "").split(" · ")[0].replace(/^(http|udp) /, ""), 20)}</text>
            <circle cx={NODE_W - 14} cy={14} r={4} fill={statusColor(n.status)} />
          </g>
        );
      })}
    </svg>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function NodeDetail({ t, id }: { t: Topology | null; id: string | null }) {
  const n = t?.nodes.find((x) => x.id === id);
  if (!t || !n) {
    return <Panel title="Node details" subtitle="Select a node in the diagram"><p className="muted" style={{ fontSize: 13 }}>Each node is a real process listening on loopback in Local Mode.</p></Panel>;
  }
  const links = t.links.filter((l) => l.source === n.id || l.target === n.id);
  return (
    <Panel title={n.label} subtitle={n.detail} actions={<StatusBadge status={n.status} />}>
      <div className="stack" style={{ gap: 8 }}>
        {links.map((l, i) => (
          <div key={i} className="row spread" style={{ fontSize: 13 }}>
            <span>{l.source === n.id ? "→" : "←"} {l.source === n.id ? t.nodes.find((x) => x.id === l.target)?.label : t.nodes.find((x) => x.id === l.source)?.label}</span>
            <span className="row"><Badge tone={l.protocol === "UDP" ? "info" : "neutral"}>{l.protocol}</Badge><span className="muted">{l.label}</span></span>
          </div>
        ))}
        {n.delay_ms != null && <div className="muted" style={{ fontSize: 13 }}>Origin response delay: {n.delay_ms} ms</div>}
      </div>
    </Panel>
  );
}
