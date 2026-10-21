import type {
  AppConfig, CacheDetail, CatalogObject, CdnEvent, Experiment, ExperimentConfig, NetworkProfile, Progress,
  RequestRecord, RoutingConfig, RoutingDecision, ServersResponse, Summary, TimePoint, Topology, Weights,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network", "Control plane unreachable. Is the backend running on port 8000?");
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const d = (data ?? {}) as { error?: string; detail?: unknown };
    const detail = typeof d.detail === "string" ? d.detail : res.statusText || "Request failed";
    throw new ApiError(res.status, d.error ?? "error", detail);
  }
  return data as T;
}

const get = <T,>(p: string) => request<T>("GET", p);
const post = <T,>(p: string, b: unknown = {}) => request<T>("POST", p, b);

export type NewExperiment = Partial<Omit<ExperimentConfig, "workload">> & {
  name: string;
  type: string;
  workload: Partial<ExperimentConfig["workload"]>;
  start?: boolean;
};

export const api = {
  health: () => get<{ status: string; mode: string; healthy_edges: number; origin: string }>("/api/health"),
  config: () => get<AppConfig>("/api/config"),
  servers: () => get<ServersResponse>("/api/servers"),
  failServer: (id: string) => post(`/api/servers/${encodeURIComponent(id)}/simulate-failure`),
  restoreServer: (id: string) => post(`/api/servers/${encodeURIComponent(id)}/restore`),
  setProfile: (id: string, p: Partial<NetworkProfile> & { concurrency_limit?: number }) =>
    post(`/api/servers/${encodeURIComponent(id)}/profile`, p),
  setOriginDelay: (delay_ms: number) => post("/api/origin/config", { delay_ms }),
  content: () => get<{ objects: CatalogObject[]; count: number; total_bytes: number }>("/api/content"),
  summary: (experimentId?: string) =>
    get<Summary & { source: string }>(`/api/metrics/summary${experimentId ? `?experiment_id=${experimentId}` : ""}`),
  timeseries: (windowS = 300) => get<{ points: TimePoint[] }>(`/api/metrics/timeseries?window_s=${windowS}`),
  errors: () => get<{ errors: Partial<RequestRecord>[] }>("/api/metrics/errors"),
  resetLive: () => post("/api/metrics/reset"),
  resetAll: () => post("/api/reset"),
  events: (limit = 100) => get<{ events: CdnEvent[] }>(`/api/events?limit=${limit}`),
  routing: () => get<RoutingConfig>("/api/routing"),
  setRouting: (strategy: string, weights?: Weights) => post<RoutingConfig>("/api/routing/strategy", { strategy, weights }),
  decisions: (limit = 30) => get<{ decisions: RoutingDecision[] }>(`/api/routing/decisions?limit=${limit}`),
  preview: () => get<{ inputs: Record<string, unknown>[]; decisions: RoutingDecision[] }>("/api/routing/preview"),
  probe: (object_id: string, hostname?: string) => post<RequestRecord>("/api/requests/probe", { object_id, hostname }),
  cacheStats: () => get<{ config: AppConfig["cache"]; totals: Record<string, number | null> }>("/api/cache/stats"),
  cacheDetail: (id: string) => get<CacheDetail>(`/api/cache/${encodeURIComponent(id)}`),
  setCachePolicy: (policy: string, capacity_bytes: number, ttl_seconds: number | null) =>
    post("/api/cache/policy", { policy, capacity_bytes, ttl_seconds }),
  clearCache: (id: string) => post<{ cleared: number }>(`/api/cache/${encodeURIComponent(id)}/clear`),
  invalidate: (id: string, object_id: string) =>
    post<{ invalidated: boolean }>(`/api/cache/${encodeURIComponent(id)}/invalidate`, { object_id }),
  warm: (id: string, object_ids: string[] = []) => post<{ warmed: number }>(`/api/cache/${encodeURIComponent(id)}/warm`, { object_ids }),
  experiments: () => get<{ experiments: Experiment[]; current: Progress | null }>("/api/experiments"),
  experiment: (id: string) => get<Experiment>(`/api/experiments/${id}`),
  createExperiment: (cfg: NewExperiment) => post<Experiment>("/api/experiments", cfg),
  startExperiment: (id: string) => post<Progress>(`/api/experiments/${id}/start`),
  stopExperiment: (id: string) => post<Progress>(`/api/experiments/${id}/stop`),
  deleteExperiment: (id: string) => request("DELETE", `/api/experiments/${id}`),
  topology: () => get<Topology>("/api/topology"),
  exportUrl: (id: string, kind: "runs" | "requests" = "runs") => `/api/experiments/${id}/export.csv?kind=${kind}`,
  exportAllUrl: () => "/api/experiments/export/all.csv",
};
