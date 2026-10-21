import { useMemo } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, RotateCcw } from "lucide-react";
import { api } from "../api/client";
import { useLive } from "../api/live";
import type { Summary, TimePoint } from "../api/types";
import { Legend, TimeLines } from "../components/charts";
import { Button, EmptyState, ErrorAlert, KindTag, Meter, Note, PageHeader, Panel, Stat, StatusBadge, useConfirm, useToast } from "../components/ui";
import { edgeColor, fmtBytes, fmtMs, fmtNum, fmtPct, fmtTime, shortEdge } from "../lib/format";
import { useApi } from "../lib/hooks";

export function Overview() {
  const live = useLive();
  const connected = live.state === "open";
  // While the WebSocket is down, poll the REST API so the page keeps working.
  const polled = useApi(api.summary, [], connected ? undefined : 3000);
  const servers = useApi(api.servers, [], connected ? undefined : 3000);
  const history = useApi(() => api.timeseries(300), [], connected ? undefined : 3000);
  const errors = useApi(api.errors, [], 4000);
  const events = useApi(() => api.events(12), [], 5000);
  const toast = useToast();
  const [dialog, confirm] = useConfirm();

  const summary: Summary | null = (connected && live.snapshot?.summary) || polled.data;
  const recent = live.snapshot?.recent;
  const edgeRows = connected && live.snapshot ? live.snapshot.servers : servers.data?.servers.map((s) => ({
    edge_id: s.edge_id, status: s.status, load: s.load.value, latency_ms: s.latency.probe_rtt_ewma_ms, active_requests: s.load.active_requests,
  })) ?? [];

  const points: TimePoint[] = useMemo(() => {
    const base = history.data?.points ?? [];
    const lastTs = base.length ? base[base.length - 1].ts : 0;
    return [...base, ...live.points.filter((p) => p.ts > lastTs)].slice(-300);
  }, [history.data, live.points]);
  const chartData = points.map((p) => ({ t: p.ts, avg: p.avg_ms, p95: p.p95_ms, rps: p.rps }));

  const loading = !summary && (polled.loading || live.state === "connecting");
  const perEdge = Object.entries(summary?.per_edge ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const maxReq = Math.max(1, ...perEdge.map(([, e]) => e.requests));

  async function resetLive() {
    if (!(await confirm({ title: "Reset live metrics?", body: "Clears the in-memory live window shown on this page. Saved experiments are not affected.", confirmLabel: "Reset" }))) return;
    try {
      await api.resetLive();
      await Promise.all([polled.reload(), history.reload(), errors.reload()]);
      toast("success", "Live metrics reset");
    } catch (e) {
      toast("error", (e as Error).message);
    }
  }

  return (
    <>
      {dialog}
      <PageHeader title="Overview"
        description="Live health and performance of the CDN, computed by the control plane from every client request issued through the resolver and edges."
        actions={<Button icon={<RotateCcw size={14} />} onClick={resetLive}>Reset live window</Button>} />

      {polled.error && !summary && <div className="mt-16" style={{ marginBottom: 16 }}><ErrorAlert message={polled.error} onRetry={polled.reload} /></div>}

      <div className="grid cols-6">
        <Stat loading={loading} label="Total requests" value={fmtNum(summary?.total_requests)} sub={summary ? `${fmtNum(summary.failed)} failed` : undefined} />
        <Stat loading={loading} label="Average latency" value={fmtMs(summary?.latency.avg_ms)} sub="Client-measured, successful" />
        <Stat loading={loading} label="P95 latency" value={fmtMs(summary?.latency.p95_ms)}
          sub={summary && summary.latency.p95_ms == null ? "Needs ≥ 20 samples" : `P99 ${fmtMs(summary?.latency.p99_ms)}`} />
        <Stat loading={loading} label="Cache hit ratio" value={fmtPct(summary?.cache.hit_ratio)}
          sub={summary ? `${fmtNum(summary.cache.hits)} / ${fmtNum(summary.cache.eligible_lookups)} lookups` : undefined}
          title="Hits ÷ eligible lookups (HIT + MISS + COALESCED). Bypassed requests are excluded." />
        <Stat loading={loading} label="Requests / second" value={fmtNum(recent?.throughput_rps ?? summary?.throughput_rps, 1)}
          sub={recent ? "Last 10 s window" : "Over live window"} />
        <Stat loading={loading} label="Origin requests avoided" value={fmtPct(summary?.origin.reduction)}
          sub={summary ? `${fmtNum(summary.origin.fetches)} of ${fmtNum(summary.origin.baseline_fetches)} no-cache baseline` : undefined}
          title="1 − actual origin fetches ÷ fetches the same workload would need with caching disabled (one per edge-served request)." />
      </div>

      <div className="grid split mt-16">
        <Panel title="Request latency" subtitle="Per-second average and P95 of successful requests (last 5 minutes)"
          actions={<Legend series={[{ label: "Average", color: "#2563eb" }, { label: "P95", color: "#eb6834" }]} />}>
          <TimeLines data={chartData} xKey="t" height={260} yFormat={(v) => `${Math.round(v)} ms`} xFormat={(v) => fmtTime(v)}
            series={[{ key: "avg", label: "Average", color: "#2563eb", format: (v) => fmtMs(v) },
                     { key: "p95", label: "P95", color: "#eb6834", format: (v) => fmtMs(v) }]} />
          <Note>P95 per second is shown only for seconds with at least 20 successful requests.</Note>
        </Panel>

        <Panel title="Edge health" subtitle="Active probes on each edge's data port" flush
          actions={<Link className="btn sm ghost" to="/servers">Details</Link>}>
          {edgeRows.length === 0 ? <div style={{ padding: "0 20px 16px" }}><div className="skeleton" style={{ height: 120 }} /></div> : edgeRows.map((s) => (
            <div className="health-row" key={s.edge_id}>
              <i className="swatch" style={{ background: edgeColor(s.edge_id) }} />
              <div className="name">
                <div>{shortEdge(s.edge_id)}</div>
                <div className="faint num" style={{ fontSize: 12 }}>RTT {fmtMs(s.latency_ms)} · {fmtNum(s.active_requests)} active</div>
              </div>
              <div style={{ width: 80 }} title={`Load ${fmtPct(s.load, 0)}`}><Meter value={s.load} label={`${s.edge_id} load`} /></div>
              <StatusBadge status={s.status} />
            </div>
          ))}
        </Panel>
      </div>

      <div className="grid cols-3 mt-16">
        <Panel title="Traffic distribution" subtitle="Requests served per edge in the live window">
          {perEdge.length === 0 ? <EmptyState title="No requests yet">Run the traffic generator or an experiment.</EmptyState> : (
            <div className="stack" style={{ gap: 14 }}>
              {perEdge.map(([id, e]) => (
                <div key={id}>
                  <div className="row spread" style={{ fontSize: 13, marginBottom: 6 }}>
                    <span className="row"><i className="swatch" style={{ background: edgeColor(id) }} />{shortEdge(id)}</span>
                    <span className="num muted">{fmtNum(e.requests)} · {fmtPct(e.share, 0)}</span>
                  </div>
                  <div className="meter"><span style={{ width: `${(e.requests / maxReq) * 100}%`, background: edgeColor(id) }} /></div>
                  <div className="row spread faint num" style={{ fontSize: 12, marginTop: 4 }}>
                    <span>avg {fmtMs(e.avg_ms)}</span><span>hit {fmtPct(e.hit_ratio, 0)}</span>
                  </div>
                </div>
              ))}
              <div className="kv" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                <dt>Bytes served</dt><dd>{fmtBytes(summary?.bytes_served)}</dd>
                <dt>Bytes fetched from origin</dt><dd>{fmtBytes(summary?.origin.bytes)}</dd>
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Recent errors" subtitle="Failed client requests (newest first)" flush>
          {errors.data?.errors.length ? (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Time</th><th>Edge</th><th>Error</th></tr></thead>
                <tbody>
                  {errors.data.errors.slice(0, 8).map((e, i) => (
                    <tr key={i}>
                      <td className="num faint">{fmtTime(e.ts)}</td>
                      <td>{e.edge_id ? shortEdge(e.edge_id) : "—"}</td>
                      <td title={e.error ?? ""} style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {e.error ?? `HTTP ${e.status_code}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <EmptyState icon={<CheckCircle2 size={22} />} title="No errors">Failed requests will appear here.</EmptyState>}
        </Panel>

        <Panel title="Recent events" subtitle="Health transitions and configuration changes" flush>
          {events.data?.events.length ? (
            <div className="event-list">
              {events.data.events.slice(0, 8).map((e, i) => (
                <div className="event" key={i}>
                  <time>{fmtTime(e.ts)}</time>
                  <span>{describeEvent(e.kind, e.edge_id, e.detail)}</span>
                </div>
              ))}
            </div>
          ) : <EmptyState icon={<AlertTriangle size={22} />} title="No events yet" />}
        </Panel>
      </div>
      <div className="mt-16"><Note>Latency figures are <KindTag kind="measured" /> by the client end-to-end. In Local Mode they include each edge's <KindTag kind="simulated" /> link delay.</Note></div>
    </>
  );
}

export function describeEvent(kind: string, edge: string | null, d: Record<string, unknown>): string {
  const e = edge ? shortEdge(edge) : "";
  switch (kind) {
    case "health_transition":
      return `${e} ${String(d.from)} → ${String(d.to)}${d.detection_ms ? ` (detected in ${Math.round(Number(d.detection_ms))} ms)` : ""}${d.recovery_ms ? ` (recovered in ${Math.round(Number(d.recovery_ms))} ms)` : ""}`;
    case "failure_injected": return `${e} failure simulated`;
    case "restore_requested": return `${e} restore requested`;
    case "routing_changed": return `Routing set to ${String(d.strategy).replace("_", " ")}`;
    case "cache_policy_changed": return `Cache policy set to ${String(d.policy).toUpperCase()}`;
    case "cache_cleared": return `${e} cache cleared`;
    case "profile_changed": return `${e} network profile changed`;
    case "experiment_started": return `Experiment “${String(d.name)}” started`;
    case "experiment_finished": return `Experiment ${String(d.status)}`;
    case "experiment_created": return `Experiment “${String(d.name)}” created`;
    case "control_plane_started": return "Control plane started";
    case "origin_config_changed": return `Origin delay set to ${String(d.delay_ms)} ms`;
    case "data_reset": return "All stored data reset";
    default: return kind.replace(/_/g, " ");
  }
}
