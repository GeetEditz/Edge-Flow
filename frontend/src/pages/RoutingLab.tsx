import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Check, FlaskConical, Play, Send } from "lucide-react";
import { api } from "../api/client";
import type { RequestRecord, Weights } from "../api/types";
import { Bars } from "../components/charts";
import { ComparisonCharts, ComparisonTable } from "../components/Comparison";
import { Badge, Button, EmptyState, ErrorAlert, Field, KindTag, Note, OptionList, PageHeader, Panel, Skeleton, StatusBadge, useToast } from "../components/ui";
import { edgeColor, fmtDateTime, fmtMs, fmtNum, fmtPct, fmtTime, shortEdge, STRATEGY_LABELS } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

const DESCRIPTIONS: Record<string, string> = {
  random: "Uniformly random healthy edge (seeded).",
  nearest: "Smallest configured distance from the client region.",
  lowest_latency: "Lowest measured probe RTT (EWMA).",
  least_loaded: "Lowest in-flight requests relative to concurrency limit.",
  hybrid: "Weighted sum of normalised latency, load and packet loss.",
  failover: "Highest-priority healthy edge; backups only on failure.",
};

export function RoutingLab() {
  const routing = useApi(api.routing);
  const preview = useApi(api.preview, [], 2000);
  const decisions = useApi(() => api.decisions(12), [], 2000);
  const exps = useApi(api.experiments, [], 3000);
  const toast = useToast();
  const { pending, run } = useAction();
  const [strategy, setStrategy] = useState("hybrid");
  const [w, setW] = useState<Weights>({ latency_weight: 0.5, load_weight: 0.4, packet_loss_weight: 0.1 });

  useEffect(() => {
    if (routing.data) {
      setStrategy(routing.data.strategy);
      setW(routing.data.weights);
    }
  }, [routing.data]);

  const dirty = routing.data && (strategy !== routing.data.strategy ||
    (strategy === "hybrid" && (Object.keys(w) as (keyof Weights)[]).some((k) => Math.abs(w[k] - routing.data!.weights[k]) > 1e-6)));
  const wSum = w.latency_weight + w.load_weight + w.packet_loss_weight;

  async function apply() {
    await run("apply", async () => {
      try {
        await api.setRouting(strategy, strategy === "hybrid" ? w : undefined);
        await routing.reload();
        toast("success", `Routing strategy set to ${STRATEGY_LABELS[strategy]}`);
      } catch (e) { toast("error", (e as Error).message); }
    });
  }

  const latestComparison = useMemo(() => exps.data?.experiments.find((e) => e.type === "routing_comparison" && e.summary?.variants.length), [exps.data]);
  const counts = Object.entries(routing.data?.decision_counts ?? {}).filter(([k]) => k !== "none").sort(([a], [b]) => a.localeCompare(b));

  return (
    <>
      <PageHeader title="Routing Lab"
        description="Choose how the CNRP resolver maps cdn.sim.local to an edge, inspect each strategy's live decision, and compare strategies under identical workloads." />

      <div className="grid split-rev">
        <div className="stack">
          <Panel title="Routing strategy" subtitle="Applied to every new resolution"
            footer={<div className="row spread"><span>Active: <b style={{ color: "var(--text)" }}>{routing.data ? STRATEGY_LABELS[routing.data.strategy] : "—"}</b></span>
              <Button variant="primary" size="sm" icon={<Check size={14} />} disabled={!dirty || (strategy === "hybrid" && wSum <= 0)} loading={pending === "apply"} onClick={apply}>Apply</Button></div>}>
            {routing.error && <ErrorAlert message={routing.error} onRetry={routing.reload} />}
            {routing.data ? (
              <OptionList label="Routing strategy" value={strategy} onChange={setStrategy}
                options={routing.data.strategies.map((s) => ({ value: s.name, title: STRATEGY_LABELS[s.name] ?? s.label, description: DESCRIPTIONS[s.name],
                  extra: routing.data!.strategy === s.name ? <Badge tone="accent">Active</Badge> : undefined }))} />
            ) : <Skeleton height={300} />}
          </Panel>

          {strategy === "hybrid" && (
            <Panel title="Hybrid weights" subtitle="score = w_lat·lat + w_load·load + w_loss·loss (each min-max normalised); lowest score wins">
              <div className="stack">
                {([["latency_weight", "Latency"], ["load_weight", "Load"], ["packet_loss_weight", "Packet loss"]] as const).map(([k, label]) => (
                  <Field key={k} label={label} hint={`${w[k].toFixed(2)}${wSum > 0 ? ` · ${fmtPct(w[k] / wSum, 0)} effective` : ""}`}>
                    <input type="range" min={0} max={1} step={0.05} value={w[k]} aria-label={`${label} weight`}
                      onChange={(e) => setW({ ...w, [k]: Number(e.target.value) })} />
                  </Field>
                ))}
                {wSum <= 0 && <ErrorAlert message="At least one weight must be positive." />}
                <Note>Weights are normalised to sum to 1. Missing metrics count as the worst value; identical values do not discriminate.</Note>
              </div>
            </Panel>
          )}
          <LinkConditions />
        </div>

        <div className="stack">
          <Panel title="Live decision preview" subtitle="What each strategy would choose right now, from the same fleet state" flush>
            {preview.data ? (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Strategy</th><th>Selected edge</th><th>Reason</th></tr></thead>
                  <tbody>
                    {preview.data.decisions.map((d) => (
                      <tr key={d.strategy} className={d.strategy === routing.data?.strategy ? "selected" : ""}>
                        <td style={{ fontWeight: 500, whiteSpace: "nowrap" }}>{STRATEGY_LABELS[d.strategy]}</td>
                        <td>{d.edge_id ? <span className="row"><i className="swatch" style={{ background: edgeColor(d.edge_id) }} />{shortEdge(d.edge_id)}</span> : <Badge tone="error">None</Badge>}</td>
                        <td className="muted" style={{ fontSize: 12.5 }}>{d.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <div style={{ padding: "0 20px 16px" }}><Skeleton height={200} /></div>}
          </Panel>

          <Panel title="Routing inputs" subtitle="Per-edge metrics the strategies consume" flush>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Edge</th><th>Health</th><th className="r">Probe RTT</th><th className="r">Load</th><th className="r">Loss</th><th className="r">Distance</th><th className="r">Hybrid score</th></tr></thead>
                <tbody>
                  {(preview.data?.inputs ?? []).map((raw) => {
                    const i = raw as { edge_id: string; healthy: boolean; latency_ms: number | null; load: number | null; packet_loss: number | null; distance_km: number | null };
                    const score = preview.data?.decisions.find((d) => d.strategy === "hybrid")?.scores?.[i.edge_id];
                    return (
                      <tr key={i.edge_id}>
                        <td><span className="row"><i className="swatch" style={{ background: edgeColor(i.edge_id) }} />{shortEdge(i.edge_id)}</span></td>
                        <td><StatusBadge status={i.healthy ? "healthy" : "unhealthy"} /></td>
                        <td className="r">{fmtMs(i.latency_ms)}</td>
                        <td className="r">{fmtPct(i.load, 0)}</td>
                        <td className="r">{fmtPct(i.packet_loss, 2)}</td>
                        <td className="r">{fmtNum(i.distance_km)} km</td>
                        <td className="r">{score == null ? "—" : score.toFixed(3)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="row row-wrap" style={{ padding: "8px 20px 12px", gap: 12, fontSize: 12, color: "var(--text-2)" }}>
              <span>RTT, load <KindTag kind="measured" /></span><span>Loss <KindTag kind="estimated" /></span><span>Distance <KindTag kind="configured" /></span>
            </div>
          </Panel>

          <div className="grid cols-2">
            <TestRequest />
            <Panel title="Resolver decisions" subtitle="Recorded selections since start (all strategies)">
              {counts.length ? (
                <Bars data={counts.map(([id, n]) => ({ name: shortEdge(id), n, id }))} xKey="name" height={170}
                  series={[{ key: "n", label: "Resolutions", color: "#2a78d6", format: (x) => fmtNum(x) }]} />
              ) : <EmptyState title="No resolutions yet" />}
            </Panel>
          </div>

          <Panel title="Recent resolutions" subtitle="Latest CNRP answers and the reason recorded for each" flush>
            {decisions.data?.decisions.length ? (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Time</th><th>Strategy</th><th>Edge</th><th>Reason</th></tr></thead>
                  <tbody>
                    {decisions.data.decisions.slice(0, 8).map((d, i) => (
                      <tr key={i}>
                        <td className="faint num">{fmtTime(d.at)}</td>
                        <td style={{ whiteSpace: "nowrap" }}>{STRATEGY_LABELS[d.strategy]}</td>
                        <td>{d.edge_id ? shortEdge(d.edge_id) : "—"}</td>
                        <td className="muted" style={{ fontSize: 12.5 }}>{d.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyState title="No resolutions yet">Send a test request or run traffic.</EmptyState>}
          </Panel>
        </div>
      </div>

      <div className="mt-24">
        <QuickComparison onCreated={exps.reload} running={!!exps.data?.current && ["running", "stopping"].includes(exps.data.current.status)} />
      </div>
      {latestComparison && (
        <div className="stack mt-16">
          <Panel title={`Latest routing comparison · ${latestComparison.name}`}
            subtitle={`${fmtDateTime(latestComparison.created_at)} · ${latestComparison.config.workload.total_requests} requests per strategy, ${latestComparison.config.workload.distribution} popularity, cache ${latestComparison.config.cache_policy.toUpperCase()}`}
            actions={<Link className="btn sm" to={`/experiments/${latestComparison.id}`}>Open experiment</Link>} flush>
            <ComparisonTable exp={latestComparison} />
          </Panel>
          <ComparisonCharts exp={latestComparison} />
        </div>
      )}
    </>
  );
}

function TestRequest() {
  const content = useApi(api.content);
  const toast = useToast();
  const { pending, run } = useAction();
  const [obj, setObj] = useState("index.html");
  const [res, setRes] = useState<RequestRecord | null>(null);
  return (
    <Panel title="Send a test request" subtitle="UDP resolve, then HTTP GET, exactly like a client">
      <div className="stack" style={{ gap: 12 }}>
        <div className="row">
          <select className="select" value={obj} onChange={(e) => setObj(e.target.value)} aria-label="Object">
            {(content.data?.objects ?? [{ object_id: "index.html" }]).map((o) => <option key={o.object_id}>{o.object_id}</option>)}
          </select>
          <Button variant="primary" icon={<Send size={14} />} loading={pending === "probe"} onClick={() => run("probe", async () => {
            try { setRes(await api.probe(obj)); } catch (e) { toast("error", (e as Error).message); }
          })}>Send</Button>
        </div>
        {res && (
          <dl className="kv" style={{ fontSize: 12.5 }}>
            <dt>Result</dt><dd>{res.ok ? <Badge tone="success">HTTP {res.status_code}</Badge> : <Badge tone="error">{res.error}</Badge>}</dd>
            <dt>Edge</dt><dd>{res.edge_id ? shortEdge(res.edge_id) : "—"}</dd>
            <dt>Cache</dt><dd>{res.cache_status ?? "—"}</dd>
            <dt>Resolve (UDP)</dt><dd>{fmtMs(res.resolve_ms, 2)}</dd>
            <dt>Fetch (TCP)</dt><dd>{fmtMs(res.latency_ms)}</dd>
            {res.explanation && <><dt>Why</dt><dd style={{ textAlign: "left" }} className="muted">{res.explanation}</dd></>}
          </dl>
        )}
      </div>
    </Panel>
  );
}

function LinkConditions() {
  const servers = useApi(api.servers);
  const toast = useToast();
  const { pending, run } = useAction();
  const [draft, setDraft] = useState<Record<string, { base_latency_ms: number; packet_loss: number }>>({});
  useEffect(() => {
    if (servers.data) setDraft(Object.fromEntries(servers.data.servers.map((s) => [s.edge_id, { base_latency_ms: s.profile.base_latency_ms, packet_loss: s.profile.packet_loss }])));
  }, [servers.data]);
  return (
    <Panel title="Network profiles" subtitle="Simulated client↔edge latency and loss (full editor on Edge Servers)">
      <table className="table" style={{ margin: "-4px 0 0" }}>
        <thead><tr><th style={{ paddingLeft: 0 }}>Edge</th><th>Latency (ms)</th><th>Loss (%)</th><th style={{ paddingRight: 0 }} /></tr></thead>
        <tbody>
          {servers.data?.servers.map((s) => {
            const d = draft[s.edge_id];
            if (!d) return null;
            const changed = d.base_latency_ms !== s.profile.base_latency_ms || d.packet_loss !== s.profile.packet_loss;
            return (
              <tr key={s.edge_id}>
                <td style={{ paddingLeft: 0 }}><span className="row"><i className="swatch" style={{ background: edgeColor(s.edge_id) }} />{shortEdge(s.edge_id)}</span></td>
                <td><input className="input num" type="number" min={0} max={5000} value={d.base_latency_ms} aria-label={`${s.edge_id} latency`} style={{ width: 80 }}
                  onChange={(e) => setDraft({ ...draft, [s.edge_id]: { ...d, base_latency_ms: Number(e.target.value) } })} /></td>
                <td><input className="input num" type="number" min={0} max={50} step={0.1} value={+(d.packet_loss * 100).toFixed(2)} aria-label={`${s.edge_id} loss`} style={{ width: 72 }}
                  onChange={(e) => setDraft({ ...draft, [s.edge_id]: { ...d, packet_loss: Number(e.target.value) / 100 } })} /></td>
                <td className="r" style={{ paddingRight: 0 }}>
                  <Button size="sm" disabled={!changed} loading={pending === s.edge_id} onClick={() => run(s.edge_id, async () => {
                    try { await api.setProfile(s.edge_id, d); toast("success", `${shortEdge(s.edge_id)} profile updated`); await servers.reload(); }
                    catch (e) { toast("error", (e as Error).message); }
                  })}>Set</Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

const ALL = ["random", "nearest", "lowest_latency", "least_loaded", "hybrid"];

function QuickComparison({ onCreated, running }: { onCreated: () => void; running: boolean }) {
  const toast = useToast();
  const { pending, run } = useAction();
  const [n, setN] = useState(300);
  const [conc, setConc] = useState(24);
  const [selected, setSelected] = useState<string[]>(ALL);
  return (
    <Panel title="Compare routing strategies" subtitle="Runs the same seeded Zipf workload through each strategy; caches are emptied before every run."
      actions={<Link className="btn sm ghost" to="/experiments"><FlaskConical size={14} />All experiments</Link>}>
      <div className="row row-wrap" style={{ gap: 16, alignItems: "flex-end" }}>
        <Field label="Requests per strategy"><input className="input num" type="number" min={20} max={5000} value={n} onChange={(e) => setN(Number(e.target.value))} style={{ width: 140 }} /></Field>
        <Field label="Concurrency"><input className="input num" type="number" min={1} max={256} value={conc} onChange={(e) => setConc(Number(e.target.value))} style={{ width: 110 }} /></Field>
        <div className="field">
          <span className="field-label">Strategies</span>
          <div className="row row-wrap" style={{ gap: 14, minHeight: 34 }}>
            {[...ALL, "failover"].map((s) => (
              <label key={s} className="check">
                <input type="checkbox" checked={selected.includes(s)} onChange={(e) => setSelected(e.target.checked ? [...selected, s] : selected.filter((x) => x !== s))} />
                {STRATEGY_LABELS[s]}
              </label>
            ))}
          </div>
        </div>
        <Button variant="primary" icon={<Play size={14} />} disabled={running || !selected.length || n < 1} loading={pending === "run"}
          title={running ? "Another experiment is running" : undefined}
          onClick={() => run("run", async () => {
            try {
              await api.createExperiment({ name: `Routing comparison (${selected.length} strategies)`, type: "routing_comparison",
                strategies: selected, workload: { total_requests: n, concurrency: conc, distribution: "zipf" }, start: true });
              toast("success", "Routing comparison started — results appear below when complete");
              onCreated();
            } catch (e) { toast("error", (e as Error).message); }
          })}>Run comparison</Button>
      </div>
    </Panel>
  );
}
