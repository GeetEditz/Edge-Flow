import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDownUp, Check, Flame, Play, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { ComparisonCharts, ComparisonTable } from "../components/Comparison";
import { Badge, Button, EmptyState, ErrorAlert, Field, Meter, Note, OptionList, PageHeader, Panel, Segmented, Skeleton, Stat, useConfirm, useToast } from "../components/ui";
import { edgeColor, fmtBytes, fmtDateTime, fmtNum, fmtPct, fmtTime, POLICY_LABELS, shortEdge } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

const POLICY_DESC: Record<string, string> = {
  none: "Every request is fetched from the origin (baseline).",
  lru: "Evicts the least recently used object when full.",
  lfu: "Evicts the least frequently used object; ties by recency.",
  ttl: "Objects expire after a fixed time; evicts the soonest-to-expire when full.",
};

const CAPACITIES = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768].map((k) => k * 1024);

export function CacheLab() {
  const servers = useApi(api.servers, [], 2000);
  const cfg = useApi(api.cacheStats, [], 4000);
  const exps = useApi(api.experiments, [], 3000);
  const toast = useToast();
  const { pending, run } = useAction();
  const [dialog, confirm] = useConfirm();
  const [policy, setPolicy] = useState("lru");
  const [capIdx, setCapIdx] = useState(4);
  const [ttl, setTtl] = useState(30);
  const [edge, setEdge] = useState("india-edge");

  useEffect(() => {
    if (!cfg.data) return;
    setPolicy(cfg.data.config.policy);
    const i = CAPACITIES.indexOf(cfg.data.config.capacity_bytes);
    setCapIdx(i >= 0 ? i : 4);
    if (cfg.data.config.ttl_seconds) setTtl(cfg.data.config.ttl_seconds);
    // only on first load
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg.data === null]);

  const active = cfg.data?.config;
  const dirty = active && (policy !== active.policy || CAPACITIES[capIdx] !== active.capacity_bytes || (policy === "ttl" && ttl !== active.ttl_seconds));
  const t = cfg.data?.totals;

  async function apply() {
    const ok = await confirm({ title: "Apply cache configuration?", confirmLabel: "Apply",
      body: `All three edge caches will switch to ${POLICY_LABELS[policy]} with ${fmtBytes(CAPACITIES[capIdx])} capacity and restart empty.` });
    if (!ok) return;
    await run("apply", async () => {
      try {
        await api.setCachePolicy(policy, CAPACITIES[capIdx], policy === "ttl" ? ttl : null);
        await Promise.all([cfg.reload(), servers.reload()]);
        toast("success", `Cache policy set to ${POLICY_LABELS[policy]}`);
      } catch (e) { toast("error", (e as Error).message); }
    });
  }

  const latest = useMemo(() => exps.data?.experiments.find((e) => e.type === "cache_comparison" && e.summary?.variants.length), [exps.data]);

  return (
    <>
      {dialog}
      <PageHeader title="Cache Lab" description="Configure the per-edge cache policy, inspect what each edge holds, and compare policies on identical workloads." />
      <div className="grid cols-4">
        <Stat loading={!t} label="Fleet hit ratio" value={fmtPct(t?.hit_ratio as number | null)} sub={t ? `${fmtNum(t.hits)} hits / ${fmtNum(t.eligible_lookups)} lookups` : undefined} />
        <Stat loading={!t} label="Cache occupancy" value={fmtBytes(t?.used_bytes)} sub={t ? `of ${fmtBytes(t.capacity_bytes)} across edges` : undefined} />
        <Stat loading={!t} label="Origin fetches" value={fmtNum(t?.origin_fetches)} sub="Performed by edges since start" />
        <Stat loading={!t} label="Capacity evictions" value={fmtNum(t?.evictions)} sub="Since edge start" />
      </div>

      <div className="grid split-rev mt-16">
        <Panel title="Cache policy" subtitle="Applied to every edge"
          footer={<div className="row spread"><span>Active: <b style={{ color: "var(--text)" }}>{active ? `${POLICY_LABELS[active.policy]} · ${fmtBytes(active.capacity_bytes)}` : "—"}</b></span>
            <Button variant="primary" size="sm" icon={<Check size={14} />} disabled={!dirty} loading={pending === "apply"} onClick={apply}>Apply</Button></div>}>
          {cfg.error && <ErrorAlert message={cfg.error} onRetry={cfg.reload} />}
          <div className="stack">
            <OptionList label="Cache policy" value={policy} onChange={setPolicy}
              options={["none", "lru", "lfu", "ttl"].map((p) => ({ value: p, title: POLICY_LABELS[p], description: POLICY_DESC[p],
                extra: active?.policy === p ? <Badge tone="accent">Active</Badge> : undefined }))} />
            <Field label="Capacity per edge" hint={fmtBytes(CAPACITIES[capIdx])}>
              <input type="range" min={0} max={CAPACITIES.length - 1} value={capIdx} onChange={(e) => setCapIdx(Number(e.target.value))} aria-label="Capacity" disabled={policy === "none"} />
            </Field>
            {policy === "ttl" && (
              <Field label="Time to live" hint={`${ttl} s`}>
                <input type="range" min={1} max={300} value={ttl} onChange={(e) => setTtl(Number(e.target.value))} aria-label="TTL" />
              </Field>
            )}
            <Note>The sample catalogue is about 9 MB, so capacities below that force evictions.</Note>
          </div>
        </Panel>

        <Panel title="Per-edge cache statistics" subtitle="Counters reported by each edge's admin endpoint" flush>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Edge</th><th>Occupancy</th><th className="r">Objects</th><th className="r">Hits</th><th className="r">Misses</th><th className="r">Coalesced</th><th className="r">Hit ratio</th><th className="r">Evictions</th><th className="r">Expired</th></tr></thead>
              <tbody>
                {servers.data ? servers.data.servers.map((s) => {
                  const c = s.cache;
                  return (
                    <tr key={s.edge_id}>
                      <td><span className="row"><i className="swatch" style={{ background: edgeColor(s.edge_id) }} />{shortEdge(s.edge_id)}</span></td>
                      <td style={{ minWidth: 150 }}>
                        <div className="num faint" style={{ fontSize: 12, marginBottom: 4 }}>{fmtBytes(c.used_bytes)} / {fmtBytes(c.capacity_bytes)}</div>
                        <Meter value={c.capacity_bytes ? (c.used_bytes ?? 0) / c.capacity_bytes : 0} warnAt={2} label="occupancy" />
                      </td>
                      <td className="r">{fmtNum(c.objects)}</td>
                      <td className="r">{fmtNum(c.hits)}</td>
                      <td className="r">{fmtNum(c.misses)}</td>
                      <td className="r">{fmtNum(c.coalesced)}</td>
                      <td className="r">{fmtPct(c.hit_ratio)}</td>
                      <td className="r">{fmtNum(c.evictions)}</td>
                      <td className="r">{fmtNum(c.expirations)}</td>
                    </tr>
                  );
                }) : <tr><td colSpan={9}><Skeleton height={90} /></td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "8px 20px 12px" }}><Note>Hit ratio = hits ÷ (hits + misses + coalesced). Coalesced requests waited for an identical in-flight origin fetch instead of duplicating it.</Note></div>
        </Panel>
      </div>

      <div className="mt-16">
        <Segmented label="Edge" value={edge} onChange={setEdge}
          options={(servers.data?.servers ?? []).map((s) => ({ value: s.edge_id, label: s.name }))} />
      </div>
      <CacheInspector key={edge} edge={edge} />

      <div className="mt-24"><QuickCacheComparison running={!!exps.data?.current && ["running", "stopping"].includes(exps.data.current.status)} onCreated={exps.reload} /></div>
      {latest && (
        <div className="stack mt-16">
          <Panel title={`Latest cache comparison · ${latest.name}`} flush
            subtitle={`${fmtDateTime(latest.created_at)} · ${latest.config.workload.total_requests} requests per policy · ${fmtBytes(latest.config.cache_capacity_bytes)} per edge · routing ${latest.config.routing_strategy}`}
            actions={<Link className="btn sm" to={`/experiments/${latest.id}`}>Open experiment</Link>}>
            <ComparisonTable exp={latest} />
          </Panel>
          <ComparisonCharts exp={latest} />
        </div>
      )}
    </>
  );
}

type SortKey = "object_id" | "size" | "frequency" | "age_s" | "idle_s";

function CacheInspector({ edge }: { edge: string }) {
  const detail = useApi(() => api.cacheDetail(edge), [edge], 2500);
  const toast = useToast();
  const { pending, run } = useAction();
  const [dialog, confirm] = useConfirm();
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "size", dir: -1 });
  const [filter, setFilter] = useState("");

  const entries = useMemo(() => {
    const list = (detail.data?.entries ?? []).filter((e) => e.object_id.includes(filter.trim()));
    return [...list].sort((a, b) => (a[sort.key] < b[sort.key] ? -1 : a[sort.key] > b[sort.key] ? 1 : 0) * sort.dir);
  }, [detail.data, sort, filter]);

  const th = (key: SortKey, label: string, right = false) => (
    <th className={`sortable${right ? " r" : ""}`} onClick={() => setSort({ key, dir: sort.key === key ? (sort.dir === 1 ? -1 : 1) : -1 })}
      aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      {label}{sort.key === key && <ArrowDownUp size={11} style={{ marginLeft: 4, verticalAlign: -1 }} />}
    </th>
  );

  async function clear() {
    if (!(await confirm({ title: `Clear ${shortEdge(edge)} cache?`, body: "All cached objects on this edge are removed. Subsequent requests will miss and refetch from the origin.", confirmLabel: "Clear cache", danger: true }))) return;
    await run("clear", async () => {
      try { const r = await api.clearCache(edge); toast("success", `Removed ${r.cleared} objects`); await detail.reload(); }
      catch (e) { toast("error", (e as Error).message); }
    });
  }

  return (
    <div className="grid cols-2 mt-16">
      {dialog}
      <Panel title={`Cached objects · ${shortEdge(edge)}`} subtitle={detail.data ? `${detail.data.entries.length} objects · ${fmtBytes(detail.data.stats.used_bytes)} used` : "Loading…"} flush
        actions={<>
          <input className="input" placeholder="Filter objects" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: 160, height: 28 }} aria-label="Filter cached objects" />
          <Button size="sm" icon={<Flame size={14} />} loading={pending === "warm"} title="Prefetch the first 10 catalogue objects from the origin"
            onClick={() => run("warm", async () => { try { const r = await api.warm(edge); toast("success", `Warmed ${r.warmed} objects`); await detail.reload(); } catch (e) { toast("error", (e as Error).message); } })}>Warm</Button>
          <Button size="sm" variant="danger" icon={<Trash2 size={14} />} loading={pending === "clear"} onClick={clear}>Clear</Button>
        </>}>
        {detail.error && <div style={{ padding: "0 20px" }}><ErrorAlert message={detail.error} onRetry={detail.reload} /></div>}
        {detail.data && !entries.length ? <EmptyState title={filter ? "No matching objects" : "Cache is empty"}>{filter ? "Try a different filter." : "Requests that miss will populate it, or use Warm."}</EmptyState> : (
          <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
            <table className="table">
              <thead><tr>{th("object_id", "Object")}{th("size", "Size", true)}{th("frequency", "Accesses", true)}{th("age_s", "Age", true)}{th("idle_s", "Idle", true)}<th className="r">TTL left</th><th /></tr></thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.object_id}>
                    <td className="mono">{e.object_id}</td>
                    <td className="r">{fmtBytes(e.size)}</td>
                    <td className="r">{fmtNum(e.frequency)}</td>
                    <td className="r">{e.age_s.toFixed(0)} s</td>
                    <td className="r">{e.idle_s.toFixed(0)} s</td>
                    <td className="r">{e.ttl_remaining_s == null ? "—" : `${e.ttl_remaining_s.toFixed(0)} s`}</td>
                    <td className="r">
                      <Button size="sm" variant="ghost" icon={<X size={13} />} aria-label={`Invalidate ${e.object_id}`} title="Invalidate"
                        onClick={async () => { try { await api.invalidate(edge, e.object_id); await detail.reload(); } catch (err) { toast("error", (err as Error).message); } }} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <Panel title="Eviction history" subtitle="Most recent removals on this edge" flush>
        {detail.data?.evictions.length ? (
          <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
            <table className="table">
              <thead><tr><th>Time</th><th>Object</th><th>Reason</th><th className="r">Size</th></tr></thead>
              <tbody>
                {detail.data.evictions.slice(0, 50).map((e, i) => (
                  <tr key={i}>
                    <td className="faint num">{fmtTime(e.at)}</td>
                    <td className="mono" style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={e.object_id}>{e.object_id}</td>
                    <td><Badge tone={e.reason === "capacity" ? "warning" : e.reason === "expired" ? "info" : "neutral"}>{e.reason}</Badge></td>
                    <td className="r" style={{ whiteSpace: "nowrap" }}>{fmtBytes(e.size)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No evictions yet">Evictions occur when the cache is full or entries expire.</EmptyState>}
      </Panel>
    </div>
  );
}

function QuickCacheComparison({ running, onCreated }: { running: boolean; onCreated: () => void }) {
  const toast = useToast();
  const { pending, run } = useAction();
  const [n, setN] = useState(400);
  const [capIdx, setCapIdx] = useState(3);
  const [policies, setPolicies] = useState(["none", "lru", "lfu", "ttl"]);
  return (
    <Panel title="Compare cache policies" subtitle="Same seeded Zipf workload per policy with nearest-replica routing, so each edge's cache sees a consistent stream.">
      <div className="row row-wrap" style={{ gap: 16, alignItems: "flex-end" }}>
        <Field label="Requests per policy"><input className="input num" type="number" min={20} max={5000} value={n} onChange={(e) => setN(Number(e.target.value))} style={{ width: 140 }} /></Field>
        <Field label="Capacity per edge" hint={fmtBytes(CAPACITIES[capIdx])}>
          <input type="range" min={0} max={CAPACITIES.length - 1} value={capIdx} onChange={(e) => setCapIdx(Number(e.target.value))} style={{ width: 180 }} aria-label="Comparison capacity" />
        </Field>
        <div className="field">
          <span className="field-label">Policies</span>
          <div className="row" style={{ gap: 14, minHeight: 34 }}>
            {["none", "lru", "lfu", "ttl"].map((p) => (
              <label key={p} className="check"><input type="checkbox" checked={policies.includes(p)}
                onChange={(e) => setPolicies(e.target.checked ? [...policies, p] : policies.filter((x) => x !== p))} />{POLICY_LABELS[p]}</label>
            ))}
          </div>
        </div>
        <Button variant="primary" icon={<Play size={14} />} disabled={running || !policies.length} loading={pending === "run"}
          onClick={() => run("run", async () => {
            try {
              await api.createExperiment({ name: `Cache comparison @ ${fmtBytes(CAPACITIES[capIdx])}`, type: "cache_comparison", routing_strategy: "nearest",
                policies: ["none", "lru", "lfu", "ttl"].filter((p) => policies.includes(p)), cache_capacity_bytes: CAPACITIES[capIdx], cache_ttl_seconds: 10,
                workload: { total_requests: n, concurrency: 8, distribution: "zipf" }, start: true });
              toast("success", "Cache comparison started");
              onCreated();
            } catch (e) { toast("error", (e as Error).message); }
          })}>Run comparison</Button>
      </div>
    </Panel>
  );
}
