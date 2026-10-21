import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Download, FlaskConical, Play, Plus, Square, Trash2, X } from "lucide-react";
import { api, type NewExperiment } from "../api/client";
import { useLive } from "../api/live";
import type { Experiment } from "../api/types";
import { Bars, Legend, TimeLines } from "../components/charts";
import { ComparisonCharts, ComparisonTable, variantLabel } from "../components/Comparison";
import { Button, EmptyState, ErrorAlert, ExperimentStatus, Field, Note, PageHeader, Panel, Segmented, Skeleton, Stat, useConfirm, useToast } from "../components/ui";
import { edgeColor, fmtBytes, fmtDateTime, fmtMs, fmtNum, fmtPct, POLICY_LABELS, SERIES, shortEdge, STRATEGY_LABELS, TYPE_LABELS } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

type SortKey = "created_at" | "name" | "avg_ms" | "p95_ms" | "throughput_rps" | "hit_ratio";

function headline(e: Experiment) {
  const vs = e.summary?.variants ?? [];
  if (!vs.length) return null;
  if (vs.length === 1) return vs[0].metrics;
  return null;
}

export function Experiments() {
  const { id } = useParams();
  const navigate = useNavigate();
  const live = useLive();
  const runningId = live.snapshot?.experiment && ["running", "stopping"].includes(live.snapshot.experiment.status) ? live.snapshot.experiment.experiment_id : null;
  const list = useApi(api.experiments, [runningId], runningId ? 2000 : 6000);
  const [creating, setCreating] = useState(false);
  const [typeFilter, setTypeFilter] = useState("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "created_at", dir: -1 });
  const [compare, setCompare] = useState<string[]>([]);

  const rows = useMemo(() => {
    const exps = (list.data?.experiments ?? []).filter((e) => typeFilter === "all" || e.type === typeFilter);
    const val = (e: Experiment): number | string => {
      if (sort.key === "created_at") return e.created_at;
      if (sort.key === "name") return e.name.toLowerCase();
      return headline(e)?.[sort.key] ?? (sort.dir === 1 ? Infinity : -Infinity);
    };
    return [...exps].sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * sort.dir);
  }, [list.data, typeFilter, sort]);

  const th = (key: SortKey, label: string, right = false) => (
    <th className={`sortable${right ? " r" : ""}`} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
      onClick={() => setSort({ key, dir: sort.key === key ? (sort.dir === 1 ? -1 : 1) : key === "name" ? 1 : -1 })}>
      {label}{sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
    </th>
  );

  return (
    <>
      <PageHeader title="Experiments" description="Every traffic run and comparison is persisted in SQLite with its configuration, per-request records and summary."
        actions={<>
          <a className="btn" href={api.exportAllUrl()} download><Download size={14} />Export all (CSV)</a>
          <Button variant="primary" icon={<Plus size={14} />} onClick={() => setCreating((c) => !c)}>New experiment</Button>
        </>} />

      {creating && <NewExperimentForm onClose={() => setCreating(false)} onCreated={(eid) => { setCreating(false); list.reload(); navigate(`/experiments/${eid}`); }} busy={!!runningId} />}
      {list.error && <div style={{ marginBottom: 16 }}><ErrorAlert message={list.error} onRetry={list.reload} /></div>}

      <Panel title="Saved experiments" subtitle={list.data ? `${list.data.experiments.length} stored` : "Loading…"} flush
        actions={<select className="select" style={{ height: 28, width: 190 }} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="Filter by type">
          <option value="all">All types</option>{Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>}>
        {!list.data ? <div style={{ padding: "0 20px 16px" }}><Skeleton height={160} /></div> : rows.length === 0 ? (
          <EmptyState icon={<FlaskConical size={22} />} title="No experiments yet" action={<Button variant="primary" size="sm" onClick={() => setCreating(true)}>Create one</Button>}>
            Run traffic, a routing comparison, a cache comparison, a failure or a degradation scenario.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr>
                <th style={{ width: 36 }}><span className="sr-only">Compare</span></th>
                {th("name", "Experiment")}<th>Status</th><th>Routing · Cache</th><th className="r">Requests</th>
                {th("avg_ms", "Avg", true)}{th("p95_ms", "P95", true)}{th("throughput_rps", "Throughput", true)}{th("hit_ratio", "Hit ratio", true)}
                <th className="r">Fail rate</th>{th("created_at", "Created", true)}
              </tr></thead>
              <tbody>
                {rows.map((e) => {
                  const m = headline(e);
                  const variants = e.summary?.variants.length ?? 0;
                  return (
                    <tr key={e.id} className={`clickable${e.id === id ? " selected" : ""}`} onClick={() => navigate(`/experiments/${e.id}`)}>
                      <td onClick={(ev) => ev.stopPropagation()}>
                        <input type="checkbox" aria-label={`Compare ${e.name}`} checked={compare.includes(e.id)} disabled={!variants}
                          onChange={(ev) => setCompare(ev.target.checked ? [...compare, e.id] : compare.filter((x) => x !== e.id))} />
                      </td>
                      <td style={{ minWidth: 180 }}>
                        <div style={{ fontWeight: 500 }}>{e.name}</div>
                        <div className="faint" style={{ fontSize: 12 }}>{TYPE_LABELS[e.type] ?? e.type}</div>
                      </td>
                      <td><ExperimentStatus status={e.status} /></td>
                      <td className="muted" style={{ whiteSpace: "nowrap" }}>
                        {e.type === "routing_comparison" ? `${e.config.strategies.length} strategies · ${POLICY_LABELS[e.config.cache_policy]}`
                          : e.type === "cache_comparison" ? `${STRATEGY_LABELS[e.config.routing_strategy]} · ${e.config.policies.length} policies`
                          : `${STRATEGY_LABELS[e.config.routing_strategy]} · ${POLICY_LABELS[e.config.cache_policy]}`}
                      </td>
                      <td className="r">{fmtNum(e.config.workload.total_requests * Math.max(1, variants) * e.config.repetitions)}</td>
                      {m ? (<>
                        <td className="r">{fmtMs(m.avg_ms)}</td><td className="r">{fmtMs(m.p95_ms)}</td>
                        <td className="r">{fmtNum(m.throughput_rps, 1)}/s</td><td className="r">{fmtPct(m.hit_ratio)}</td>
                        <td className="r">{fmtPct(m.failure_rate, 2)}</td>
                      </>) : <td colSpan={5} className="r faint">{variants > 1 ? `${variants} variants — open to compare` : "—"}</td>}
                      <td className="r faint">{fmtDateTime(e.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {compare.length > 0 && <CrossCompare ids={compare} all={list.data?.experiments ?? []} onClear={() => setCompare([])} />}
      {id && <ExperimentDetail key={id} id={id} running={runningId === id} onChanged={list.reload} onDeleted={() => { list.reload(); navigate("/experiments"); }} />}
    </>
  );
}

function CrossCompare({ ids, all, onClear }: { ids: string[]; all: Experiment[]; onClear: () => void }) {
  const exps = ids.map((i) => all.find((e) => e.id === i)).filter((e): e is Experiment => !!e);
  const data = exps.flatMap((e) => (e.summary?.variants ?? []).map((v) => ({
    name: `${e.name.slice(0, 22)}${exps.length > 1 || (e.summary?.variants.length ?? 0) > 1 ? ` · ${variantLabel(v, e.type)}` : ""}`,
    avg: v.metrics.avg_ms, p95: v.metrics.p95_ms,
  })));
  const series = [{ key: "avg", label: "Average", color: SERIES[0], format: (x: number) => fmtMs(x) }, { key: "p95", label: "P95", color: SERIES[1], format: (x: number) => fmtMs(x) }];
  return (
    <div className="mt-16">
      <Panel title="Cross-experiment comparison" subtitle={`${exps.length} experiment(s) selected · every variant shown`}
        actions={<><Legend series={series} /><Button size="sm" variant="ghost" icon={<X size={13} />} onClick={onClear}>Clear</Button></>}>
        <Bars data={data} xKey="name" series={series} layout="vertical" categoryWidth={240} height={Math.max(160, data.length * 44)} yFormat={(x) => `${Math.round(x)} ms`} />
      </Panel>
    </div>
  );
}

function ExperimentDetail({ id, running, onChanged, onDeleted }: { id: string; running: boolean; onChanged: () => void; onDeleted: () => void }) {
  const exp = useApi(() => api.experiment(id), [id], running ? 1500 : undefined);
  const toast = useToast();
  const { pending, run } = useAction();
  const [dialog, confirm] = useConfirm();
  const e = exp.data;

  if (exp.error) return <div className="mt-16"><ErrorAlert message={exp.error} onRetry={exp.reload} /></div>;
  if (!e) return <div className="mt-16 panel" style={{ padding: 20 }}><Skeleton height={200} /></div>;

  const c = e.config;
  const runs = e.runs ?? [];
  const firstRun = runs[0]?.summary;
  const failure = e.summary?.variants[0]?.failure;
  const timeline = (firstRun?.timeline ?? []);
  const timeEdges = Array.from(new Set(timeline.flatMap((b) => Object.keys(b.per_edge)))).sort();
  const refLines = (firstRun?.events ?? []).map((ev) => ({ x: ev.t, label: ev.kind.replace("edge_", "").replace("link_", ""), color: ev.kind.includes("restored") ? "#16a34a" : "#dc2626" }));

  async function del() {
    if (!(await confirm({ title: `Delete “${e!.name}”?`, body: "The experiment, its runs and all stored request records are permanently removed.", confirmLabel: "Delete", danger: true }))) return;
    await run("delete", async () => {
      try { await api.deleteExperiment(id); toast("success", "Experiment deleted"); onDeleted(); }
      catch (err) { toast("error", (err as Error).message); }
    });
  }

  return (
    <div className="stack mt-24">
      {dialog}
      <Panel title={e.name} subtitle={`${TYPE_LABELS[e.type]} · created ${fmtDateTime(e.created_at)} · id ${e.id}`}
        actions={<>
          <ExperimentStatus status={e.status} />
          {running ? <Button size="sm" variant="danger" icon={<Square size={13} />} loading={pending === "stop"} onClick={() => run("stop", async () => {
            try { await api.stopExperiment(id); toast("info", "Stopping…"); exp.reload(); } catch (err) { toast("error", (err as Error).message); }
          })}>Stop</Button> : <Button size="sm" icon={<Play size={13} />} loading={pending === "start"} onClick={() => run("start", async () => {
            try { await api.startExperiment(id); toast("success", e.status === "created" ? "Experiment started" : "Experiment re-run started"); exp.reload(); onChanged(); } catch (err) { toast("error", (err as Error).message); }
          })}>{e.status === "created" ? "Start" : "Re-run"}</Button>}
          <a className="btn sm" href={api.exportUrl(id, "runs")} download><Download size={13} />Runs CSV</a>
          <a className="btn sm" href={api.exportUrl(id, "requests")} download><Download size={13} />Requests CSV</a>
          <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} aria-label="Delete experiment" disabled={running} onClick={del} />
        </>}>
        {e.error && <div style={{ marginBottom: 12 }}><ErrorAlert message={e.error} /></div>}
        {running && e.progress && (
          <div style={{ marginBottom: 16 }}>
            <div className="row spread num" style={{ fontSize: 13, marginBottom: 6 }}>
              <span>Run {e.progress.run_index} of {e.progress.run_count}{e.progress.variant ? ` · ${e.progress.variant}` : ""}</span>
              <span className="muted">{fmtNum(e.progress.completed)} / {fmtNum(e.progress.total)} requests</span>
            </div>
            <div className="meter progress-lg"><span style={{ width: `${(e.progress.fraction ?? 0) * 100}%` }} /></div>
          </div>
        )}
        <dl className="kv" style={{ gridTemplateColumns: "repeat(4, auto 1fr)", columnGap: 24 }}>
          <dt>Requests / run</dt><dd>{fmtNum(c.workload.total_requests)}</dd>
          <dt>Concurrency</dt><dd>{c.workload.concurrency}</dd>
          <dt>Distribution</dt><dd>{c.workload.distribution}{c.workload.distribution === "zipf" ? ` (s=${c.workload.zipf_s})` : ""}</dd>
          <dt>Seed</dt><dd>{c.workload.seed}</dd>
          <dt>Rate limit</dt><dd>{c.workload.rate_rps ? `${c.workload.rate_rps}/s` : "none"}</dd>
          <dt>Resolution</dt><dd>{c.workload.resolution === "dns" ? "DNS-style (TTL)" : "per request"}</dd>
          <dt>Cache capacity</dt><dd>{fmtBytes(c.cache_capacity_bytes)}</dd>
          <dt>Repetitions</dt><dd>{c.repetitions}</dd>
          {c.failure && <><dt>Failure</dt><dd>{shortEdge(c.failure.edge_id)} at {c.failure.at_s}s{c.failure.restore_at_s != null ? `, restore ${c.failure.restore_at_s}s` : ""}</dd></>}
          {c.degradation && <><dt>Degradation</dt><dd>{shortEdge(c.degradation.edge_id)} at {c.degradation.at_s}s</dd></>}
        </dl>
      </Panel>

      {e.summary && e.summary.variants.length > 0 ? (<>
        {failure?.injected && (
          <div className="grid cols-4">
            <Stat label="Detection time" value={fmtMs(failure.detection_ms, 0)} sub={failure.detection_reason?.startsWith("passive") ? "Passive (client failures)" : "Active health probes"} />
            <Stat label="Failed requests" value={fmtNum(failure.failed_requests)} sub={`of ${fmtNum(failure.requests_during_failure)} during the outage`} />
            <Stat label="Last failure after injection" value={fmtMs(failure.last_failure_ms, 0)} sub="Requests stopped reaching the failed edge" />
            <Stat label="Recovery time" value={fmtMs(failure.recovery_ms, 0)} sub="Restore → marked healthy" />
          </div>
        )}
        <Panel title="Results" subtitle={e.summary.repetitions > 1 ? `Mean of ${e.summary.repetitions} repetitions per variant; best value per column highlighted` : "Best value per column highlighted"} flush>
          <ComparisonTable exp={e} cols={e.type === "cache_comparison" ? undefined : ["avg_ms", "p95_ms", "p99_ms", "throughput_rps", "hit_ratio", "origin_fetches", "failure_rate"]} />
        </Panel>
        {e.summary.variants.length > 1 && <ComparisonCharts exp={e} />}
        {timeline.length > 0 && (e.type === "failure" || e.type === "degradation" || e.summary.variants.length === 1) && (
          <div className="grid cols-2">
            <Panel title="Latency over the run" subtitle={`First run · ${refLines.length ? "dashed lines mark injected events" : "average per time bucket"}`}>
              <TimeLines data={timeline.map((b) => ({ t: b.t, avg: b.avg_ms }))} xKey="t" height={220} refLines={refLines}
                xFormat={(v) => `${v.toFixed(1)}s`} yFormat={(v) => `${Math.round(v)} ms`}
                series={[{ key: "avg", label: "Average latency", color: SERIES[0], format: (v) => fmtMs(v) }]} />
            </Panel>
            <Panel title="Requests per edge over time" subtitle="How traffic shifted between edges" actions={<Legend series={timeEdges.map((x) => ({ label: shortEdge(x), color: edgeColor(x) }))} />}>
              <TimeLines data={timeline.map((b) => ({ t: b.t, ...Object.fromEntries(timeEdges.map((x) => [x, b.per_edge[x] ?? 0])) }))} xKey="t" height={220} refLines={refLines}
                xFormat={(v) => `${v.toFixed(1)}s`} series={timeEdges.map((x) => ({ key: x, label: shortEdge(x), color: edgeColor(x) }))} />
            </Panel>
          </div>
        )}
        <Panel title="Runs" subtitle="Individual runs stored in SQLite" flush>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>#</th><th>Variant</th><th>Rep.</th><th className="r">Requests</th><th className="r">Failed</th><th className="r">Avg</th><th className="r">P95</th><th className="r">P99</th><th className="r">Throughput</th><th className="r">Hit ratio</th><th className="r">Origin fetches</th><th className="r">Observed at origin</th></tr></thead>
              <tbody>
                {runs.map((r) => r.summary && (
                  <tr key={r.id}>
                    <td className="faint">{r.id}</td>
                    <td>{e.type === "routing_comparison" ? STRATEGY_LABELS[r.routing_strategy] : e.type === "cache_comparison" ? POLICY_LABELS[r.cache_policy] : r.variant}</td>
                    <td>{r.repetition + 1}</td>
                    <td className="r">{fmtNum(r.summary.total_requests)}</td>
                    <td className="r">{fmtNum(r.summary.failed)}</td>
                    <td className="r">{fmtMs(r.summary.latency.avg_ms)}</td>
                    <td className="r">{fmtMs(r.summary.latency.p95_ms)}</td>
                    <td className="r">{fmtMs(r.summary.latency.p99_ms)}</td>
                    <td className="r">{fmtNum(r.summary.throughput_rps, 1)}/s</td>
                    <td className="r">{fmtPct(r.summary.cache.hit_ratio)}</td>
                    <td className="r">{fmtNum(r.summary.origin.fetches)}</td>
                    <td className="r" title="Delta of the origin's own request counter during the run (cross-check)">{fmtNum(r.summary.origin.observed_at_origin)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </>) : (
        <div className="panel"><EmptyState title={running ? "Running…" : e.status === "created" ? "Not started" : "No results"}>
          {running ? "Results are written when each run completes." : e.status === "created" ? "Start the experiment to collect results." : "This experiment has no completed runs."}
        </EmptyState></div>
      )}
    </div>
  );
}

const TEMPLATES: Record<string, Partial<NewExperiment>> = {
  single: {},
  routing_comparison: { strategies: ["random", "nearest", "lowest_latency", "least_loaded", "hybrid"] },
  cache_comparison: { routing_strategy: "nearest", policies: ["none", "lru", "lfu", "ttl"], cache_capacity_bytes: 2 * 1024 * 1024, cache_ttl_seconds: 10 },
  failure: { routing_strategy: "nearest", failure: { edge_id: "india-edge", at_s: 3, restore_at_s: 8 } },
  degradation: { routing_strategy: "lowest_latency", degradation: { edge_id: "india-edge", at_s: 3, profile: { base_latency_ms: 180, packet_loss: 0.02 } } },
};

function NewExperimentForm({ onClose, onCreated, busy }: { onClose: () => void; onCreated: (id: string) => void; busy: boolean }) {
  const toast = useToast();
  const { pending, run } = useAction();
  const [type, setType] = useState("routing_comparison");
  const [name, setName] = useState("");
  const [total, setTotal] = useState(300);
  const [conc, setConc] = useState(16);
  const [rate, setRate] = useState(0);
  const [reps, setReps] = useState(1);
  const [seed, setSeed] = useState(42);
  const [routing, setRouting] = useState("hybrid");
  const [policy, setPolicy] = useState("lru");
  const [edge, setEdge] = useState("india-edge");
  const [atS, setAtS] = useState(3);
  const [restoreS, setRestoreS] = useState(8);
  const [degLatency, setDegLatency] = useState(180);
  const [degLoss, setDegLoss] = useState(2);

  const onType = (t: string) => {
    setType(t);
    if (t === "failure") { setRouting("nearest"); setRate(60); setTotal(600); setConc(8); }
    else if (t === "degradation") { setRouting("lowest_latency"); setRate(60); setTotal(600); setConc(8); }
    else { setRate(0); setTotal(300); setConc(16); setRouting(t === "cache_comparison" ? "nearest" : "hybrid"); }
  };

  async function submit(start: boolean) {
    const cfg: NewExperiment = {
      ...TEMPLATES[type], name: name.trim() || `${TYPE_LABELS[type]} ${new Date().toLocaleTimeString([], { hour12: false })}`, type,
      routing_strategy: routing, cache_policy: policy, repetitions: reps,
      workload: { total_requests: total, concurrency: conc, rate_rps: rate, distribution: "zipf", seed }, start,
    };
    if (type === "failure") cfg.failure = { edge_id: edge, at_s: atS, restore_at_s: restoreS > atS ? restoreS : null };
    if (type === "degradation") cfg.degradation = { edge_id: edge, at_s: atS, profile: { base_latency_ms: degLatency, packet_loss: degLoss / 100 } };
    await run("create", async () => {
      try { const exp = await api.createExperiment(cfg); toast("success", start ? "Experiment started" : "Experiment saved"); onCreated(exp.id); }
      catch (e) { toast("error", (e as Error).message); }
    });
  }

  const duration = rate > 0 ? total / rate : null;
  return (
    <div style={{ marginBottom: 16 }}>
      <Panel title="New experiment" subtitle="Workload, initial cache state and network conditions are identical for every variant."
        actions={<Button size="sm" variant="ghost" icon={<X size={13} />} onClick={onClose}>Close</Button>}
        footer={<div className="row spread row-wrap">
          <span>{duration ? `≈ ${duration.toFixed(0)} s per run at the rate limit` : "Runs as fast as concurrency allows"}</span>
          <div className="row">
            <Button onClick={() => submit(false)} loading={pending === "create"}>Save only</Button>
            <Button variant="primary" icon={<Play size={14} />} disabled={busy} title={busy ? "Another experiment is running" : undefined} onClick={() => submit(true)} loading={pending === "create"}>Save and start</Button>
          </div>
        </div>}>
        <div className="stack">
          <Segmented label="Experiment type" value={type} onChange={onType} options={Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label }))} />
          <div className="grid cols-4" style={{ gap: 14 }}>
            <Field label="Name" className="span-2"><input className="input" value={name} placeholder={TYPE_LABELS[type]} maxLength={120} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Requests per run"><input className="input num" type="number" min={1} max={20000} value={total} onChange={(e) => setTotal(Number(e.target.value))} /></Field>
            <Field label="Concurrency"><input className="input num" type="number" min={1} max={256} value={conc} onChange={(e) => setConc(Number(e.target.value))} /></Field>
            <Field label="Rate limit (req/s)" help="0 = unlimited"><input className="input num" type="number" min={0} max={5000} value={rate} onChange={(e) => setRate(Number(e.target.value))} /></Field>
            <Field label="Repetitions" help="Results are averaged"><input className="input num" type="number" min={1} max={5} value={reps} onChange={(e) => setReps(Number(e.target.value))} /></Field>
            <Field label="Seed"><input className="input num" type="number" min={0} value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></Field>
            {type !== "routing_comparison" && (
              <Field label="Routing strategy"><select className="select" value={routing} onChange={(e) => setRouting(e.target.value)}>{Object.entries(STRATEGY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            )}
            {type !== "cache_comparison" && (
              <Field label="Cache policy"><select className="select" value={policy} onChange={(e) => setPolicy(e.target.value)}>{Object.entries(POLICY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            )}
            {(type === "failure" || type === "degradation") && (<>
              <Field label="Target edge"><select className="select" value={edge} onChange={(e) => setEdge(e.target.value)}>
                {["india-edge", "singapore-edge", "usa-edge"].map((x) => <option key={x} value={x}>{shortEdge(x)}</option>)}</select></Field>
              <Field label={type === "failure" ? "Fail at (s)" : "Degrade at (s)"}><input className="input num" type="number" min={0} step={0.5} value={atS} onChange={(e) => setAtS(Number(e.target.value))} /></Field>
            </>)}
            {type === "failure" && <Field label="Restore at (s)" help="≤ fail time = never"><input className="input num" type="number" min={0} step={0.5} value={restoreS} onChange={(e) => setRestoreS(Number(e.target.value))} /></Field>}
            {type === "degradation" && (<>
              <Field label="New latency (ms)"><input className="input num" type="number" min={0} max={5000} value={degLatency} onChange={(e) => setDegLatency(Number(e.target.value))} /></Field>
              <Field label="New loss (%)"><input className="input num" type="number" min={0} max={50} step={0.1} value={degLoss} onChange={(e) => setDegLoss(Number(e.target.value))} /></Field>
            </>)}
          </div>
          <Note>
            {type === "routing_comparison" && "Runs random, nearest, lowest-latency, least-loaded and hybrid routing in turn. Use the Routing Lab to pick a custom subset."}
            {type === "cache_comparison" && "Runs caching disabled, LRU, LFU and TTL (10 s) at 2 MB per edge with nearest-replica routing."}
            {type === "failure" && "The target edge's data listener is closed mid-run. Detection, failed requests and recovery time are recorded. A rate limit keeps traffic flowing across the event."}
            {type === "degradation" && "The target edge's simulated link degrades mid-run; observe how latency-aware strategies shift traffic."}
            {type === "single" && "A single run with the chosen routing and cache policy (the Traffic Generator page offers more workload options)."}
          </Note>
        </div>
      </Panel>
    </div>
  );
}

