import type { ReactNode } from "react";
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { BarChart3 } from "lucide-react";
import { EmptyState } from "./ui";

export const AXIS = { stroke: "#94a3b8", fontSize: 11, tickLine: false, axisLine: { stroke: "#e5e7eb" } } as const;
export const GRID = { stroke: "#f1f3f6", vertical: false } as const;

export interface SeriesDef {
  key: string;
  label: string;
  color: string;
  format?: (v: number) => string;
}

export function Legend({ series }: { series: { label: string; color: string }[] }) {
  return (
    <div className="legend" aria-hidden={series.length < 2}>
      {series.map((s) => (
        <span key={s.label}><i className="swatch" style={{ background: s.color }} />{s.label}</span>
      ))}
    </div>
  );
}

interface TooltipPayload { dataKey?: string | number; name?: string; value?: number | null; color?: string; payload?: Record<string, unknown> }

export function ChartTooltip({ active, payload, label, labelFormatter, series }: {
  active?: boolean; payload?: TooltipPayload[]; label?: string | number; labelFormatter?: (l: string | number) => string; series: SeriesDef[];
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="t-title">{labelFormatter ? labelFormatter(label ?? "") : label}</div>
      {series.map((s) => {
        const p = payload.find((x) => x.dataKey === s.key);
        if (!p) return null;
        const v = p.value;
        return (
          <div className="t-row" key={s.key}>
            <span><i className="swatch" style={{ background: s.color }} />{s.label}</span>
            <b>{v == null ? "—" : s.format ? s.format(v) : v}</b>
          </div>
        );
      })}
    </div>
  );
}

export function ChartEmpty({ height, children }: { height: number; children?: ReactNode }) {
  return (
    <div style={{ height, display: "grid", placeItems: "center" }}>
      <EmptyState icon={<BarChart3 size={22} />} title="No data yet">{children ?? "Send traffic to populate this chart."}</EmptyState>
    </div>
  );
}

/** Time-series line chart (one axis). */
export function TimeLines({ data, xKey, series, height = 240, yFormat, xFormat, refLines = [] }: {
  data: Record<string, unknown>[]; xKey: string; series: SeriesDef[]; height?: number;
  yFormat?: (v: number) => string; xFormat?: (v: number) => string; refLines?: { x: number; label: string; color?: string }[];
}) {
  if (!data.length) return <ChartEmpty height={height} />;
  return (
    <div className="chart-box" style={{ height }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey={xKey} {...AXIS} tickFormatter={xFormat} minTickGap={40} type="number" domain={["dataMin", "dataMax"]} />
          <YAxis {...AXIS} tickFormatter={yFormat} width={56} />
          <Tooltip cursor={{ stroke: "#cbd5e1", strokeWidth: 1 }}
            content={<ChartTooltip series={series} labelFormatter={(l) => (xFormat ? xFormat(Number(l)) : String(l))} />} />
          {refLines.map((r) => (
            <ReferenceLine key={`${r.x}-${r.label}`} x={r.x} stroke={r.color ?? "#94a3b8"} strokeDasharray="4 3"
              label={{ value: r.label, position: "insideTopRight", fill: "#64748b", fontSize: 11 }} />
          ))}
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2} dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff" }} isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Grouped or stacked bars over categories (one axis). */
export function Bars({ data, xKey, series, height = 240, yFormat, stacked, layout = "horizontal", categoryWidth = 110 }: {
  data: Record<string, unknown>[]; xKey: string; series: SeriesDef[]; height?: number; yFormat?: (v: number) => string;
  stacked?: boolean; layout?: "horizontal" | "vertical"; categoryWidth?: number;
}) {
  if (!data.length) return <ChartEmpty height={height} />;
  const vertical = layout === "vertical";
  return (
    <div className="chart-box" style={{ height }}>
      <ResponsiveContainer>
        <BarChart data={data} layout={layout} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} barGap={2} barCategoryGap="28%">
          <CartesianGrid {...GRID} vertical={vertical} horizontal={!vertical} />
          {vertical ? (
            <>
              <XAxis type="number" {...AXIS} tickFormatter={yFormat} />
              <YAxis type="category" dataKey={xKey} {...AXIS} width={categoryWidth} tick={{ fill: "#475569", fontSize: 12 }} />
            </>
          ) : (
            <>
              <XAxis dataKey={xKey} {...AXIS} tick={{ fill: "#475569", fontSize: 12 }} interval={0} />
              <YAxis {...AXIS} tickFormatter={yFormat} width={56} />
            </>
          )}
          <Tooltip cursor={{ fill: "rgba(148,163,184,0.08)" }} content={<ChartTooltip series={series} />} />
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color} stackId={stacked ? "s" : undefined}
              radius={stacked ? (i === series.length - 1 ? (vertical ? [0, 4, 4, 0] : [4, 4, 0, 0]) : 0) : (vertical ? [0, 4, 4, 0] : [4, 4, 0, 0])}
              stroke="#fff" strokeWidth={stacked ? 1 : 0} maxBarSize={36} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
