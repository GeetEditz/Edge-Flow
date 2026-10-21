import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, Database, FlaskConical, Gauge, Menu, Network, Route, Server, Zap } from "lucide-react";
import { api } from "../api/client";
import { useLive } from "../api/live";
import { useApi } from "../lib/hooks";
import { fmtPct } from "../lib/format";
import { Badge } from "./ui";

const NAV = [
  { to: "/", label: "Overview", icon: Gauge },
  { to: "/servers", label: "Edge Servers", icon: Server },
  { to: "/routing", label: "Routing Lab", icon: Route },
  { to: "/cache", label: "Cache Lab", icon: Database },
  { to: "/traffic", label: "Traffic Generator", icon: Zap },
  { to: "/experiments", label: "Experiments", icon: FlaskConical },
  { to: "/topology", label: "Network Topology", icon: Network },
];

function ConnectionStatus() {
  const { state, retryInMs } = useLive();
  if (state === "open") return <Badge tone="success" dot title="Receiving live metrics over WebSocket">Live</Badge>;
  if (state === "connecting") return <Badge tone="neutral" dot>Connecting</Badge>;
  return (
    <Badge tone="warning" dot title="Dashboard falls back to periodic polling while disconnected">
      Reconnecting{retryInMs ? ` in ${Math.ceil(retryInMs / 1000)}s` : ""}
    </Badge>
  );
}

export function Layout() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const { snapshot } = useLive();
  const cfg = useApi(api.config);
  const mode = cfg.data?.mode;
  useEffect(() => setOpen(false), [pathname]);
  const current = NAV.find((n) => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to)));
  const exp = snapshot?.experiment;
  const running = exp && (exp.status === "running" || exp.status === "stopping");
  const healthy = snapshot?.servers.filter((s) => s.status === "healthy").length;

  return (
    <div className="shell">
      <aside className={`sidebar${open ? " open" : ""}`} aria-label="Primary">
        <div className="brand">
          <div className="brand-mark"><Activity size={16} /></div>
          <div>
            <div className="brand-name">CDN Simulator</div>
            <div className="brand-sub">Latency-aware edge network</div>
          </div>
        </div>
        <nav className="nav">
          <div className="nav-label">Monitor</div>
          {NAV.slice(0, 2).map((n) => <NavItem key={n.to} {...n} />)}
          <div className="nav-label">Labs</div>
          {NAV.slice(2, 5).map((n) => <NavItem key={n.to} {...n} />)}
          <div className="nav-label">Analysis</div>
          {NAV.slice(5).map((n) => <NavItem key={n.to} {...n} />)}
        </nav>
        <div className="sidebar-foot">
          <div className="row spread"><span>Edges healthy</span><span className="num">{healthy ?? "—"} / {snapshot?.servers.length ?? "—"}</span></div>
          <div className="row spread"><span>Routing</span><span>{snapshot?.routing.strategy.replace("_", " ") ?? "—"}</span></div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="btn ghost icon menu-btn" aria-label="Open navigation" onClick={() => setOpen((o) => !o)}><Menu size={18} /></button>
          <div className="topbar-title"><span className="crumb-extra">CDN Simulator /</span><strong>{current?.label ?? "Page"}</strong></div>
          <div className="topbar-right">
            {running && (
              <NavLink to="/experiments" style={{ display: "contents" }}>
                <Badge tone="accent" dot title={exp.name}>Experiment {fmtPct(exp.fraction, 0)}</Badge>
              </NavLink>
            )}
            {mode && (
              <Badge tone="neutral" title={mode === "mininet" ? "Servers on Mininet hosts; links shaped by tc/netem"
                : "Real TCP/UDP on loopback with simulated link profiles"}>{mode === "mininet" ? "Mininet mode" : "Local mode"}</Badge>
            )}
            <ConnectionStatus />
          </div>
        </header>
        <main className="content" id="main"><Outlet /></main>
      </div>
      {open && <div className="overlay" style={{ zIndex: 40, background: "rgba(17,24,39,.2)" }} onClick={() => setOpen(false)} />}
    </div>
  );
}

function NavItem({ to, label, icon: Icon }: { to: string; label: string; icon: typeof Gauge }) {
  return (
    <NavLink to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
      <Icon size={16} strokeWidth={1.8} />{label}
    </NavLink>
  );
}
