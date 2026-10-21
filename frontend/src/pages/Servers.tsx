import { Fragment, useEffect, useState } from "react";
import { Power, PowerOff, RefreshCw, Save, SlidersHorizontal } from "lucide-react";
import { api } from "../api/client";
import type { EdgeServer } from "../api/types";
import { Button, ErrorAlert, Field, KindTag, Meter, Note, PageHeader, Panel, Skeleton, StatusBadge, useConfirm, useToast } from "../components/ui";
import { edgeColor, fmtBytes, fmtMs, fmtNum, fmtPct, fmtTime } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

export function Servers() {
  const servers = useApi(api.servers, [], 2000);
  const [editing, setEditing] = useState<string | null>(null);

  return (
    <>
      <PageHeader title="Edge Servers"
        description="Each edge is an independent server process with its own TCP listeners, cache and network profile. Locations and link profiles are simulated."
        actions={<Button icon={<RefreshCw size={14} />} onClick={servers.reload}>Refresh</Button>} />
      {servers.error && <div style={{ marginBottom: 16 }}><ErrorAlert message={servers.error} onRetry={servers.reload} /></div>}

      <div className="grid cols-3">
        {servers.data ? servers.data.servers.map((s) => (
          <EdgeCard key={s.edge_id} s={s} onChanged={servers.reload} onEdit={() => setEditing(editing === s.edge_id ? null : s.edge_id)} editing={editing === s.edge_id} />
        )) : [0, 1, 2].map((i) => <div className="panel edge-card" key={i}><Skeleton height={22} width="50%" /><Skeleton height={120} /></div>)}
      </div>

      {editing && servers.data && (
        <ProfileEditor key={editing} s={servers.data.servers.find((x) => x.edge_id === editing)!} onClose={() => setEditing(null)} onSaved={servers.reload} />
      )}

      <div className="grid cols-2 mt-16">
        <Panel title="Comparison" subtitle="Side-by-side view of the fleet" flush>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Edge</th><th className="r">Probe RTT</th><th className="r">Requests</th><th className="r">Hit ratio</th><th className="r">Served</th><th className="r">5xx</th></tr>
              </thead>
              <tbody>
                {servers.data?.servers.map((s) => (
                  <tr key={s.edge_id}>
                    <td><span className="row"><i className="swatch" style={{ background: edgeColor(s.edge_id) }} />{s.name}</span></td>
                    <td className="r">{fmtMs(s.latency.probe_rtt_ewma_ms)}</td>
                    <td className="r">{fmtNum(s.requests)}</td>
                    <td className="r">{fmtPct(s.cache.hit_ratio)}</td>
                    <td className="r">{fmtBytes(s.bytes_served)}</td>
                    <td className="r">{fmtNum(s.errors_5xx)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <OriginPanel origin={servers.data?.origin} onChanged={servers.reload} />
      </div>
    </>
  );
}

function EdgeCard({ s, onChanged, onEdit, editing }: { s: EdgeServer; onChanged: () => void; onEdit: () => void; editing: boolean }) {
  const toast = useToast();
  const { pending, run } = useAction();
  const [dialog, confirm] = useConfirm();
  const cacheUse = s.cache.capacity_bytes ? (s.cache.used_bytes ?? 0) / s.cache.capacity_bytes : 0;
  const last = s.transitions[s.transitions.length - 1];

  async function fail() {
    const ok = await confirm({
      title: `Simulate failure of ${s.name}?`, danger: true, confirmLabel: "Simulate failure",
      body: "The edge's data listener is closed and in-flight connections are aborted, like a crashed process. The health monitor will mark it unhealthy and routing will avoid it.",
    });
    if (!ok) return;
    await run("fail", async () => {
      try { await api.failServer(s.edge_id); toast("success", `${s.name}: failure simulated`); onChanged(); }
      catch (e) { toast("error", (e as Error).message); }
    });
  }
  async function restore() {
    await run("restore", async () => {
      try { await api.restoreServer(s.edge_id); toast("success", `${s.name}: restored, waiting for health checks`); onChanged(); }
      catch (e) { toast("error", (e as Error).message); }
    });
  }

  return (
    <section className="panel edge-card" aria-label={s.name}>
      {dialog}
      <div className="edge-card-head">
        <div>
          <h3><i className="swatch" style={{ background: edgeColor(s.edge_id) }} />{s.name}</h3>
          <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>{s.location} <KindTag kind="simulated" /></div>
          <div className="faint mono" style={{ marginTop: 4 }}>{s.host}:{s.data_port} · admin {s.admin_port}</div>
        </div>
        <StatusBadge status={s.status} />
      </div>

      <dl className="kv">
        <dt>Probe RTT <KindTag kind="measured" /></dt><dd>{fmtMs(s.latency.probe_rtt_ewma_ms)}</dd>
        <dt>Link profile <KindTag kind="simulated" /></dt>
        <dd>{s.profile.base_latency_ms} ms ± {s.profile.jitter_ms} · {s.profile.bandwidth_mbps} Mbps</dd>
        <dt>Packet loss</dt>
        <dd title="Configured loss rate vs. loss estimated from segments reported by the edge">
          {fmtPct(s.packet_loss.configured, 2)} cfg · {fmtPct(s.packet_loss.estimated, 2)} est
        </dd>
        <dt>Distance <KindTag kind="configured" /></dt><dd>{fmtNum(s.distance_km)} km</dd>
        <dt>CPU / memory</dt>
        <dd title={s.shared_process ? "In-process mode: figures are for the shared control-plane process" : `PID ${s.pid}`}>
          {s.cpu_percent == null ? "—" : `${s.cpu_percent.toFixed(1)}%`} · {fmtBytes(s.memory_rss_bytes)}{s.shared_process ? " (shared)" : ""}
        </dd>
      </dl>

      <div>
        <div className="row spread" style={{ fontSize: 12.5, marginBottom: 6 }}>
          <span className="muted">Load · {fmtNum(s.load.active_requests)} active, {fmtNum(s.load.queued_requests)} queued</span>
          <span className="num">{fmtPct(s.load.value, 0)} of {s.load.concurrency_limit}</span>
        </div>
        <Meter value={s.load.value} label="load" />
      </div>
      <div>
        <div className="row spread" style={{ fontSize: 12.5, marginBottom: 6 }}>
          <span className="muted">Cache · {(s.cache.policy ?? "—").toUpperCase()} · {fmtNum(s.cache.objects)} objects</span>
          <span className="num">{fmtBytes(s.cache.used_bytes)} / {fmtBytes(s.cache.capacity_bytes)}</span>
        </div>
        <Meter value={cacheUse} warnAt={2} label="cache occupancy" />
      </div>

      <div className="edge-metrics">
        <div className="edge-metric"><div className="l">Requests</div><div className="v">{fmtNum(s.requests)}</div></div>
        <div className="edge-metric"><div className="l">Hit ratio</div><div className="v">{fmtPct(s.cache.hit_ratio)}</div></div>
        <div className="edge-metric"><div className="l">Evictions</div><div className="v">{fmtNum(s.cache.evictions)}</div></div>
      </div>

      {last && (
        <div className="faint" style={{ fontSize: 12 }}>
          Last transition {fmtTime(last.at)}: {last.from} → {last.to}
          {last.detection_ms ? `, detected in ${Math.round(last.detection_ms)} ms` : ""}
          {last.recovery_ms ? `, recovered in ${Math.round(last.recovery_ms)} ms` : ""}
        </div>
      )}

      <div className="row row-wrap">
        {s.failed_simulated ? (
          <Button variant="primary" size="sm" icon={<Power size={14} />} loading={pending === "restore"} onClick={restore}>Restore</Button>
        ) : (
          <Button variant="danger" size="sm" icon={<PowerOff size={14} />} loading={pending === "fail"} onClick={fail}>Simulate failure</Button>
        )}
        <Button size="sm" variant={editing ? "primary" : "secondary"} icon={<SlidersHorizontal size={14} />} onClick={onEdit} aria-expanded={editing}>Network profile</Button>
      </div>
    </section>
  );
}

function ProfileEditor({ s, onClose, onSaved }: { s: EdgeServer; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const { pending, run } = useAction();
  const [p, setP] = useState({ ...s.profile, concurrency_limit: s.load.concurrency_limit });
  const set = (k: keyof typeof p) => (e: React.ChangeEvent<HTMLInputElement>) => setP({ ...p, [k]: Number(e.target.value) });

  async function save() {
    await run("save", async () => {
      try { await api.setProfile(s.edge_id, p); toast("success", `${s.name}: profile updated`); onSaved(); onClose(); }
      catch (e) { toast("error", (e as Error).message); }
    });
  }

  return (
    <div className="mt-16">
      <Panel title={`Network profile · ${s.name}`} subtitle="Simulated client↔edge link conditions applied inside the edge before bytes are written to the socket."
        actions={<><Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button><Button size="sm" variant="primary" icon={<Save size={14} />} loading={pending === "save"} onClick={save}>Apply</Button></>}>
        <div className="grid cols-4" style={{ gap: 20 }}>
          <Field label="Base latency" hint={`${p.base_latency_ms} ms`}><input type="range" min={0} max={400} step={1} value={p.base_latency_ms} onChange={set("base_latency_ms")} aria-label="Base latency" /></Field>
          <Field label="Jitter" hint={`± ${p.jitter_ms} ms`}><input type="range" min={0} max={50} step={1} value={p.jitter_ms} onChange={set("jitter_ms")} aria-label="Jitter" /></Field>
          <Field label="Bandwidth" hint={`${p.bandwidth_mbps} Mbps`}><input type="range" min={1} max={1000} step={1} value={p.bandwidth_mbps} onChange={set("bandwidth_mbps")} aria-label="Bandwidth" /></Field>
          <Field label="Packet loss" hint={fmtPct(p.packet_loss, 1)}><input type="range" min={0} max={0.1} step={0.001} value={p.packet_loss} onChange={set("packet_loss")} aria-label="Packet loss" /></Field>
          <Field label="Concurrency limit" hint={`${p.concurrency_limit} slots`}><input type="range" min={1} max={128} step={1} value={p.concurrency_limit} onChange={set("concurrency_limit")} aria-label="Concurrency limit" /></Field>
        </div>
        <div className="mt-16"><Note>Lost segments add max(2 × base latency, 10 ms) each, approximating fast-retransmit recovery. Requests beyond the concurrency limit queue at the edge.</Note></div>
      </Panel>
    </div>
  );
}

function OriginPanel({ origin, onChanged }: { origin?: { status: string; stats: Record<string, unknown> | null; host: string; port: number; location: string }; onChanged: () => void }) {
  const toast = useToast();
  const { pending, run } = useAction();
  const st = (origin?.stats ?? {}) as Record<string, number | Record<string, number>>;
  const [delay, setDelay] = useState<number | null>(null);
  useEffect(() => { if (delay == null && typeof st.delay_ms === "number") setDelay(st.delay_ms); }, [st.delay_ms, delay]);
  const byEdge = (st.fetches_by_edge ?? {}) as Record<string, number>;

  return (
    <Panel title="Origin server" subtitle={origin ? `${origin.host}:${origin.port} · ${origin.location}` : "Authoritative content source"}
      actions={origin && <StatusBadge status={origin.status} />}>
      <dl className="kv">
        <dt>Content requests</dt><dd>{fmtNum(st.content_requests as number)}</dd>
        <dt>Bytes served</dt><dd>{fmtBytes(st.bytes_served as number)}</dd>
        <dt>Not found</dt><dd>{fmtNum(st.not_found as number)}</dd>
        {Object.entries(byEdge).map(([k, v]) => (<Fragment key={k}><dt>Fetched by {k}</dt><dd>{fmtNum(v)}</dd></Fragment>))}
      </dl>
      <div className="row mt-16" style={{ alignItems: "flex-end", gap: 12 }}>
        <Field label="Origin response delay" hint={delay == null ? "—" : `${delay} ms`} className="" >
          <input type="range" min={0} max={500} step={5} value={delay ?? 0} onChange={(e) => setDelay(Number(e.target.value))} aria-label="Origin delay" style={{ minWidth: 200 }} />
        </Field>
        <Button size="sm" loading={pending === "delay"} disabled={delay == null} onClick={() => run("delay", async () => {
          try { await api.setOriginDelay(delay!); toast("success", `Origin delay set to ${delay} ms`); onChanged(); }
          catch (e) { toast("error", (e as Error).message); }
        })}>Apply</Button>
      </div>
    </Panel>
  );
}
