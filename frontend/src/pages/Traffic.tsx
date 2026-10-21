import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Play, Square } from "lucide-react";
import { api } from "../api/client";
import { useLive } from "../api/live";
import type { Experiment } from "../api/types";
import { TimeLines } from "../components/charts";
import { Badge, Button, EmptyState, ErrorAlert, ExperimentStatus, Field, Note, PageHeader, Panel, Segmented, Skeleton, useToast } from "../components/ui";
import { fmtBytes, fmtDuration, fmtMs, fmtNum, fmtPct, POLICY_LABELS, STRATEGY_LABELS } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

type Dist = "uniform" | "random" | "zipf";
type Res = "per_request" | "dns";

const LAST_RUN_KEY = "traffic.lastRunId";

export function Traffic() {
  const content = useApi(api.content);
  const config = useApi(api.config);
  const live = useLive();
  const toast = useToast();
  const { pending, run } = useAction();

  const [name, setName] = useState("Traffic run");
  const [total, setTotal] = useState(500);
  const [conc, setConc] = useState(16);
  const [rate, setRate] = useState(0);
  const [dist, setDist] = useState<Dist>("zipf");
  const [zipfS, setZipfS] = useState(1.1);
  const [seed, setSeed] = useState(42);
  const [resolution, setResolution] = useState<Res>("per_request");
  const [routing, setRouting] = useState("");
  const [policy, setPolicy] = useState("");
  const [objects, setObjects] = useState<string[] | null>(null);
  // Survives navigating away and back, so the results panel still shows the last run.
  const [lastId, setLastId] = useState<string | null>(() => {
    try { return sessionStorage.getItem(LAST_RUN_KEY); } catch { return null; }
  });
  useEffect(() => {
    try { if (lastId) sessionStorage.setItem(LAST_RUN_KEY, lastId); } catch { /* storage unavailable */ }
  }, [lastId]);

  useEffect(() => {
    if (config.data && !routing) {
      setRouting(config.data.routing.strategy);
      setPolicy(config.data.cache.policy);
    }
  }, [config.data, routing]);
  useEffect(() => {
    if (content.data && objects === null) setObjects(content.data.objects.map((o) => o.object_id));
  }, [content.data, objects]);

  const progress = live.snapshot?.experiment;
  const isRunning = !!progress && ["running", "stopping"].includes(progress.status);
  const ownRun = progress && progress.experiment_id === lastId ? progress : null;
  useEffect(() => { if (progress && progress.experiment_id !== lastId && isRunning) setLastId(progress.experiment_id); }, [progress, lastId, isRunning]);
  const last = useApi<Experiment | null>(() => (lastId ? api.experiment(lastId) : Promise.resolve(null)), [lastId, progress?.status], isRunning ? 2000 : undefined);

  const selectedBytes = useMemo(() => (content.data?.objects ?? []).filter((o) => objects?.includes(o.object_id)).reduce((a, o) => a + o.size, 0), [content.data, objects]);
  const allSelected = objects && content.data && objects.length === content.data.objects.length;
  const valid = total >= 1 && total <= 20000 && conc >= 1 && conc <= 256 && rate >= 0 && (objects?.length ?? 0) > 0 && name.trim().length > 0;

  async function start() {
    await run("start", async () => {
      try {
        const exp = await api.createExperiment({
          name: name.trim(), type: "single", routing_strategy: routing, cache_policy: policy,
          cache_capacity_bytes: config.data?.cache.capacity_bytes, cache_ttl_seconds: config.data?.cache.ttl_seconds ?? 30,
          workload: { total_requests: total, concurrency: conc, rate_rps: rate, distribution: dist, zipf_s: zipfS, seed, resolution,
            objects: allSelected ? [] : objects ?? [] },
          start: true,
        });
        setLastId(exp.id);
        toast("success", "Traffic started");
      } catch (e) { toast("error", (e as Error).message); }
    });
  }

  async function stop() {
    if (!progress) return;
    await run("stop", async () => {
      try { await api.stopExperiment(progress.experiment_id); toast("info", "Stopping after in-flight requests complete"); }
      catch (e) { toast("error", (e as Error).message); }
    });
  }

  const runSummary = last.data?.runs?.[0]?.summary;
  const timeline = (runSummary?.timeline ?? []).map((b) => ({ t: b.t, avg: b.avg_ms, req: b.requests }));
  const recent = live.snapshot?.recent;

  return (
    <>
      <PageHeader title="Traffic Generator"
        description="Generate real client traffic: every request performs a UDP resolution and an HTTP/1.1 GET over TCP to the selected edge. Each run is saved as an experiment." />
      <div className="grid split-rev">
        <Panel title="Workload" subtitle="Seeded and reproducible"
          footer={<div className="row spread">
            <span>{fmtNum(objects?.length)} objects · {fmtBytes(selectedBytes)}</span>
            {isRunning ? <Button variant="danger" icon={<Square size={13} />} loading={pending === "stop"} onClick={stop} disabled={progress?.status === "stopping"}>Stop</Button>
              : <Button variant="primary" icon={<Play size={14} />} loading={pending === "start"} disabled={!valid || !routing} onClick={start}>Start traffic</Button>}
          </div>}>
          {(content.error || config.error) && <ErrorAlert message={content.error ?? config.error ?? ""} onRetry={() => { content.reload(); config.reload(); }} />}
          <div className="form-grid">
            <Field label="Run name" className="full"><input className="input" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Requests" help="1 – 20,000"><input className="input num" type="number" min={1} max={20000} value={total} onChange={(e) => setTotal(Number(e.target.value))} aria-invalid={total < 1 || total > 20000} /></Field>
            <Field label="Concurrency" help="Max in-flight requests"><input className="input num" type="number" min={1} max={256} value={conc} onChange={(e) => setConc(Number(e.target.value))} /></Field>
            <Field label="Rate limit (req/s)" help="0 = unlimited"><input className="input num" type="number" min={0} max={5000} value={rate} onChange={(e) => setRate(Number(e.target.value))} /></Field>
            <Field label="Random seed" help="Same seed, same sequence"><input className="input num" type="number" min={0} value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></Field>
            <div className="field full">
              <span className="field-label">Popularity distribution</span>
              <Segmented label="Distribution" value={dist} onChange={setDist} options={[{ value: "zipf", label: "Zipf" }, { value: "uniform", label: "Uniform" }, { value: "random", label: "Random" }]} />
              <span className="field-help">{dist === "zipf" ? "P(rank k) ∝ 1/kˢ — a few hot objects dominate." : dist === "uniform" ? "Every object requested equally often, shuffled." : "Independent uniform random choice per request."}</span>
            </div>
            {dist === "zipf" && <Field label="Zipf exponent s" hint={zipfS.toFixed(2)} className="full"><input type="range" min={0.5} max={2} step={0.05} value={zipfS} onChange={(e) => setZipfS(Number(e.target.value))} aria-label="Zipf exponent" /></Field>}
            <div className="field full">
              <span className="field-label">Resolution mode</span>
              <Segmented label="Resolution" value={resolution} onChange={setResolution} options={[{ value: "per_request", label: "Per request" }, { value: "dns", label: `DNS-style (TTL ${config.data?.resolver.ttl_s ?? 5}s)` }]} />
              <span className="field-help">{resolution === "dns" ? "Answers are cached for their TTL, so requests stick to one edge until it expires — like real DNS." : "Resolve before every request so the strategy is consulted each time."}</span>
            </div>
            <Field label="Routing strategy"><select className="select" value={routing} onChange={(e) => setRouting(e.target.value)}>{Object.entries(STRATEGY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Cache policy"><select className="select" value={policy} onChange={(e) => setPolicy(e.target.value)}>{Object.entries(POLICY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          </div>
          <div className="mt-16"><Note>Caches are emptied at the start of each run so results are comparable. Settings in place before the run are restored afterwards.</Note></div>
        </Panel>

        <div className="stack">
          <Panel title="Progress" subtitle={ownRun ? ownRun.name : progress && isRunning ? `Another run is active: ${progress.name}` : "No run in progress"}
            actions={progress && (ownRun || isRunning) && <ExperimentStatus status={progress.status} />}>
            {progress && (isRunning || progress.experiment_id === lastId) ? (
              <div className="stack" style={{ gap: 12 }}>
                <div className="row spread num" style={{ fontSize: 13 }}>
                  <span>{fmtNum(progress.completed)} / {fmtNum(progress.total)} requests</span>
                  <span className="muted">{fmtPct(progress.fraction, 0)}</span>
                </div>
                <div className="meter progress-lg"><span style={{ width: `${(progress.fraction ?? 0) * 100}%` }} /></div>
                <div className="grid cols-4" style={{ gap: 12 }}>
                  <Mini label="Failed" value={fmtNum(progress.failed)} />
                  <Mini label="Elapsed" value={fmtDuration(((progress.finished_at ?? Date.now() / 1000) - progress.started_at))} />
                  <Mini label="Live RPS (10 s)" value={isRunning ? fmtNum(recent?.throughput_rps, 1) : "—"} />
                  <Mini label="Live avg latency" value={isRunning ? fmtMs(recent?.latency.avg_ms) : "—"} />
                </div>
              </div>
            ) : <EmptyState title="Ready">Configure a workload and start traffic. Progress streams here over the WebSocket.</EmptyState>}
          </Panel>

          <Panel title="Run results" subtitle={last.data ? `${last.data.name}` : "Results of the latest run started here"}
            actions={last.data && <Link className="btn sm" to={`/experiments/${last.data.id}`}>Open experiment</Link>}>
            {!lastId || (last.error && !last.data) ? <EmptyState title="No run yet" /> : !last.data ? <Skeleton height={160} /> : !runSummary ? (
              <EmptyState title={isRunning ? "Running…" : "No results"}>{isRunning ? "Summary is computed when the run finishes." : last.data.error ?? ""}</EmptyState>
            ) : (
              <div className="stack">
                <div className="grid cols-4" style={{ gap: 12 }}>
                  <Mini label="Average" value={fmtMs(runSummary.latency.avg_ms)} />
                  <Mini label="P95 / P99" value={`${fmtMs(runSummary.latency.p95_ms, 0)} / ${fmtMs(runSummary.latency.p99_ms, 0)}`} />
                  <Mini label="Throughput" value={`${fmtNum(runSummary.throughput_rps, 1)}/s`} />
                  <Mini label="Hit ratio" value={fmtPct(runSummary.cache.hit_ratio)} />
                </div>
                <TimeLines data={timeline} xKey="t" height={180} xFormat={(v) => `${v.toFixed(0)}s`} yFormat={(v) => `${Math.round(v)} ms`}
                  series={[{ key: "avg", label: "Average latency", color: "#2563eb", format: (v) => fmtMs(v) }]} />
                <div>
                  <div className="field-label" style={{ marginBottom: 8 }}>Error summary</div>
                  {runSummary.errors && Object.keys(runSummary.errors).length ? (
                    <div className="row row-wrap">{Object.entries(runSummary.errors).map(([k, v]) => <Badge key={k} tone="error">{k}: {v}</Badge>)}</div>
                  ) : <span className="muted" style={{ fontSize: 13 }}>No failed requests ({fmtNum(runSummary.total_requests)} total).</span>}
                </div>
              </div>
            )}
          </Panel>
        </div>
      </div>

      <div className="mt-16">
        <Panel title="Content catalogue" subtitle="Objects served by the origin; uncheck to exclude from the workload"
          actions={<><Button size="sm" variant="ghost" onClick={() => setObjects(content.data?.objects.map((o) => o.object_id) ?? [])}>Select all</Button>
            <Button size="sm" variant="ghost" onClick={() => setObjects([])}>Clear</Button></>} flush>
          {content.data ? (
            <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
              <table className="table">
                <thead><tr><th style={{ width: 40 }} /><th>Object</th><th>Type</th><th className="r">Size</th></tr></thead>
                <tbody>
                  {content.data.objects.map((o) => (
                    <tr key={o.object_id} className="clickable" onClick={() => setObjects((s) => (s?.includes(o.object_id) ? s.filter((x) => x !== o.object_id) : [...(s ?? []), o.object_id]))}>
                      <td><input type="checkbox" checked={objects?.includes(o.object_id) ?? false} readOnly aria-label={`Include ${o.object_id}`} /></td>
                      <td className="mono">{o.object_id}</td>
                      <td className="muted">{o.content_type}</td>
                      <td className="r">{fmtBytes(o.size)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div style={{ padding: "0 20px 16px" }}><Skeleton height={160} /></div>}
        </Panel>
      </div>
    </>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: "var(--panel-muted)", border: "1px solid var(--border)", borderRadius: 6, padding: "10px 12px" }}>
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
      <div className="num" style={{ fontWeight: 600, fontSize: 15, marginTop: 2 }}>{value}</div>
    </div>
  );
}

