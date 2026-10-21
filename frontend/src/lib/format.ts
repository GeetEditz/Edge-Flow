export const DASH = "—";

export function fmtMs(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v.toFixed(digits)} ms`;
}

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtBytes(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  const units = ["B", "KB", "MB", "GB"];
  let n = v;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function fmtTime(ts: number | null | undefined, seconds = true): string {
  if (!ts) return DASH;
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: seconds ? "2-digit" : undefined, hour12: false });
}

export function fmtDateTime(ts: number | null | undefined): string {
  if (!ts) return DASH;
  return new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function fmtDuration(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return DASH;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export const STRATEGY_LABELS: Record<string, string> = {
  random: "Random",
  nearest: "Nearest replica",
  lowest_latency: "Lowest latency",
  least_loaded: "Least loaded",
  hybrid: "Hybrid",
  failover: "Health-aware failover",
};

export const POLICY_LABELS: Record<string, string> = { none: "Disabled", lru: "LRU", lfu: "LFU", ttl: "TTL" };

export const TYPE_LABELS: Record<string, string> = {
  single: "Single run",
  routing_comparison: "Routing comparison",
  cache_comparison: "Cache comparison",
  failure: "Server failure",
  degradation: "Network degradation",
};

/** Categorical slots in fixed order; colour follows the entity, never its rank. */
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];

const EDGE_ORDER = ["india-edge", "singapore-edge", "usa-edge"];
export function edgeColor(id: string): string {
  const i = EDGE_ORDER.indexOf(id);
  if (i >= 0) return SERIES[i];
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return SERIES[3 + (h % 3)];
}

const STRATEGY_ORDER = ["random", "nearest", "lowest_latency", "least_loaded", "hybrid", "failover"];
export function strategyColor(s: string): string {
  return SERIES[Math.max(0, STRATEGY_ORDER.indexOf(s)) % SERIES.length];
}

const POLICY_ORDER = ["none", "lru", "lfu", "ttl"];
export function policyColor(p: string): string {
  return SERIES[Math.max(0, POLICY_ORDER.indexOf(p)) % SERIES.length];
}

export function shortEdge(id: string): string {
  const base = id.replace(/-edge$/, "");
  return base.length <= 3 ? base.toUpperCase() : base.replace(/^./, (c) => c.toUpperCase());
}
