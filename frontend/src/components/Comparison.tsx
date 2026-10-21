import type { Experiment, Variant, VariantMetrics } from "../api/types";
import { edgeColor, fmtBytes, fmtMs, fmtNum, fmtPct, POLICY_LABELS, shortEdge, STRATEGY_LABELS } from "../lib/format";
import { Bars, Legend } from "./charts";
import { Panel } from "./ui";

type Col = { key: keyof VariantMetrics; label: string; fmt: (v: number | null) => string; better: "low" | "high" | null };

const COLS: Record<string, Col> = {
  avg_ms: { key: "avg_ms", label: "Avg", fmt: (v) => fmtMs(v), better: "low" },
  p95_ms: { key: "p95_ms", label: "P95", fmt: (v) => fmtMs(v), better: "low" },
  p99_ms: { key: "p99_ms", label: "P99", fmt: (v) => fmtMs(v), better: "low" },
  throughput_rps: { key: "throughput_rps", label: "Throughput", fmt: (v) => (v == null ? "—" : `${v.toFixed(1)}/s`), better: "high" },
  hit_ratio: { key: "hit_ratio", label: "Hit ratio", fmt: (v) => fmtPct(v), better: "high" },
  origin_fetches: { key: "origin_fetches", label: "Origin fetches", fmt: (v) => fmtNum(v), better: "low" },
  origin_bytes: { key: "origin_bytes", label: "Origin bytes", fmt: (v) => fmtBytes(v), better: "low" },
  evictions: { key: "evictions", label: "Evictions", fmt: (v) => fmtNum(v), better: null },
  failure_rate: { key: "failure_rate", label: "Failure rate", fmt: (v) => fmtPct(v, 2), better: "low" },
  total_requests: { key: "total_requests", label: "Requests", fmt: (v) => fmtNum(v), better: null },
};

export function variantLabel(v: Variant, type: string): string {
  if (type === "routing_comparison") return STRATEGY_LABELS[v.routing] ?? v.routing;
  if (type === "cache_comparison") return POLICY_LABELS[v.cache_policy] ?? v.cache_policy;
  return `${STRATEGY_LABELS[v.routing] ?? v.routing} · ${POLICY_LABELS[v.cache_policy] ?? v.cache_policy}`;
}

function best(variants: Variant[], c: Col): number | null {
  if (!c.better || variants.length < 2) return null;
  const vals = variants.map((v) => v.metrics[c.key]).filter((x): x is number => x != null);
  if (!vals.length || vals.every((x) => x === vals[0])) return null;
  return c.better === "low" ? Math.min(...vals) : Math.max(...vals);
}

export function ComparisonTable({ exp, cols }: { exp: Experiment; cols?: string[] }) {
  const variants = exp.summary?.variants ?? [];
  const keys = cols ?? (exp.type === "cache_comparison"
    ? ["hit_ratio", "origin_fetches", "origin_bytes", "avg_ms", "p95_ms", "evictions"]
    : ["avg_ms", "p95_ms", "p99_ms", "throughput_rps", "hit_ratio", "failure_rate"]);
  const columns = keys.map((k) => COLS[k]);
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>{exp.type === "cache_comparison" ? "Cache policy" : exp.type === "routing_comparison" ? "Strategy" : "Variant"}</th>
            {columns.map((c) => <th key={c.key} className="r">{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {variants.map((v) => (
            <tr key={v.label}>
              <td style={{ fontWeight: 500 }}>{variantLabel(v, exp.type)}{v.runs > 1 && <span className="faint"> · mean of {v.runs}</span>}</td>
              {columns.map((c) => {
                const b = best(variants, c);
                const val = v.metrics[c.key];
                return <td key={c.key} className={`r${b != null && val === b ? " best" : ""}`}>{c.fmt(val)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Standard chart set for a comparison experiment. */
export function ComparisonCharts({ exp }: { exp: Experiment }) {
  const variants = exp.summary?.variants ?? [];
  const lat = variants.map((v) => ({ name: variantLabel(v, exp.type), avg: v.metrics.avg_ms, p95: v.metrics.p95_ms }));
  const edges = Array.from(new Set(variants.flatMap((v) => Object.keys(v.edge_share)))).filter((e) => e !== "unrouted").sort();
  const share = variants.map((v) => ({ name: variantLabel(v, exp.type), ...Object.fromEntries(edges.map((e) => [e, v.edge_share[e] ?? 0])) }));
  const latSeries = [
    { key: "avg", label: "Average", color: "#2a78d6", format: (x: number) => fmtMs(x) },
    { key: "p95", label: "P95", color: "#eb6834", format: (x: number) => fmtMs(x) },
  ];
  const shareSeries = edges.map((e) => ({ key: e, label: shortEdge(e), color: edgeColor(e), format: (x: number) => fmtPct(x, 0) }));
  const catWidth = exp.type === "cache_comparison" ? 70 : 130;

  return (
    <div className="grid cols-2">
      <Panel title="Latency by variant" subtitle="Client-measured, successful requests" actions={<Legend series={latSeries} />}>
        <Bars data={lat} xKey="name" series={latSeries} layout="vertical" categoryWidth={catWidth} height={Math.max(180, variants.length * 52)} yFormat={(x) => `${Math.round(x)} ms`} />
      </Panel>
      {exp.type === "cache_comparison" ? (
        <Panel title="Origin fetches by policy" subtitle="Requests that reached the origin (MISS + BYPASS)">
          <Bars data={variants.map((v) => ({ name: variantLabel(v, exp.type), fetches: v.metrics.origin_fetches }))} xKey="name" layout="vertical"
            categoryWidth={catWidth} height={Math.max(180, variants.length * 52)}
            series={[{ key: "fetches", label: "Origin fetches", color: "#2a78d6", format: (x) => fmtNum(x) }]} />
        </Panel>
      ) : (
        <Panel title="Request distribution" subtitle="Share of requests served by each edge" actions={<Legend series={shareSeries} />}>
          <Bars data={share} xKey="name" series={shareSeries} stacked layout="vertical" categoryWidth={catWidth}
            height={Math.max(180, variants.length * 52)} yFormat={(x) => `${Math.round(x * 100)}%`} />
        </Panel>
      )}
    </div>
  );
}
