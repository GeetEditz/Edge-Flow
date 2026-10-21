export type HealthStatus = "healthy" | "unhealthy" | "unknown";

export interface LatencyStats {
  samples: number;
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  min_ms: number | null;
  max_ms: number | null;
}

export interface EdgeShare {
  requests: number;
  failed: number;
  share: number | null;
  avg_ms: number | null;
  p95_ms: number | null;
  hits: number;
  hit_ratio: number | null;
}

export interface Summary {
  total_requests: number;
  successful: number;
  failed: number;
  failure_rate: number | null;
  duration_s?: number | null;
  throughput_rps: number | null;
  latency: LatencyStats;
  resolve?: LatencyStats;
  cache: { hits: number; misses: number; coalesced: number; bypass: number; eligible_lookups: number; hit_ratio: number | null };
  origin: { fetches: number; bytes: number; baseline_fetches: number; reduction: number | null; observed_at_origin?: number | null };
  bytes_served: number;
  per_edge: Record<string, EdgeShare>;
  errors?: Record<string, number>;
  since?: number;
}

export interface NetworkProfile {
  base_latency_ms: number;
  jitter_ms: number;
  bandwidth_mbps: number;
  packet_loss: number;
}

export interface CacheStats {
  policy: string;
  capacity_bytes: number;
  ttl_seconds: number | null;
  used_bytes: number;
  objects: number;
  hits: number;
  misses: number;
  coalesced: number;
  bypass: number;
  eligible_lookups: number;
  hit_ratio: number | null;
  origin_fetches: number;
  origin_bytes: number;
  origin_errors: number;
  evictions: number;
  expirations: number;
  invalidations: number;
  oversize_rejections: number;
  inflight_fetches: number;
}

export interface Transition {
  at: number;
  from: string;
  to: string;
  reason: string;
  detection_ms?: number;
  recovery_ms?: number;
}

export interface EdgeServer {
  edge_id: string;
  name: string;
  location: string;
  host: string;
  data_port: number;
  admin_port: number;
  distance_km: number;
  priority: number;
  status: HealthStatus;
  failed_simulated: boolean;
  last_error: string | null;
  latency: { probe_rtt_ewma_ms: number | null; last_probe_rtt_ms: number | null; kind: string; simulated_base_ms: number | null };
  load: { value: number; active_requests: number | null; queued_requests: number | null; client_inflight: number; concurrency_limit: number };
  packet_loss: { estimated: number | null; configured: number | null };
  profile: NetworkProfile;
  requests: number | null;
  responses_ok: number | null;
  bytes_served: number | null;
  errors_4xx: number | null;
  errors_5xx: number | null;
  cpu_percent: number | null;
  memory_rss_bytes: number | null;
  pid: number | null;
  shared_process: boolean | null;
  cache: Partial<CacheStats>;
  transitions: Transition[];
}

export interface ServersResponse {
  servers: EdgeServer[];
  origin: { status: HealthStatus; stats: Record<string, unknown> | null; host: string; port: number; location: string };
}

export interface TimePoint {
  ts: number;
  requests: number;
  errors: number;
  rps: number;
  avg_ms: number | null;
  p95_ms: number | null;
  hits: number;
  misses: number;
  bypass: number;
  origin_fetches: number;
  per_edge: Record<string, number>;
}

export interface Weights {
  latency_weight: number;
  load_weight: number;
  packet_loss_weight: number;
}

export interface RoutingConfig {
  strategy: string;
  weights: Weights;
  strategies: { name: string; label: string }[];
  decision_counts: Record<string, number>;
}

export interface RoutingDecision {
  edge_id: string | null;
  strategy: string;
  reason: string;
  scores: Record<string, number | null> | null;
  candidates: string[];
  excluded: string[];
  at: number;
}

export interface Progress {
  experiment_id: string;
  name: string;
  type: string;
  status: string;
  completed: number;
  failed: number;
  total: number;
  run_index: number;
  run_count: number;
  variant: string | null;
  started_at: number;
  finished_at?: number;
  fraction: number | null;
}

export interface Workload {
  total_requests: number;
  concurrency: number;
  rate_rps: number;
  distribution: "uniform" | "random" | "zipf";
  zipf_s: number;
  seed: number;
  objects: string[];
  resolution: "per_request" | "dns";
  hostname?: string;
  timeout_s?: number;
}

export interface VariantMetrics {
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  throughput_rps: number | null;
  hit_ratio: number | null;
  origin_fetches: number | null;
  origin_bytes: number | null;
  origin_reduction: number | null;
  failure_rate: number | null;
  evictions: number | null;
  total_requests: number | null;
  failed: number | null;
}

export interface FailureMetrics {
  edge_id: string;
  injected: boolean;
  detection_ms?: number | null;
  detection_reason?: string | null;
  failed_requests?: number;
  failed_on_failed_edge?: number;
  last_failure_ms?: number | null;
  first_success_elsewhere_ms?: number | null;
  requests_during_failure?: number;
  recovery_ms?: number | null;
}

export interface Variant {
  label: string;
  routing: string;
  cache_policy: string;
  runs: number;
  metrics: VariantMetrics;
  edge_share: Record<string, number>;
  failure?: FailureMetrics | null;
}

export interface ExperimentConfig {
  name: string;
  type: string;
  workload: Workload;
  routing_strategy: string;
  weights: Weights | null;
  cache_policy: string;
  cache_capacity_bytes: number;
  cache_ttl_seconds: number | null;
  strategies: string[];
  policies: string[];
  repetitions: number;
  failure: { edge_id: string; at_s: number; restore_at_s: number | null } | null;
  degradation: { edge_id: string; at_s: number; profile: Partial<NetworkProfile> } | null;
}

export interface TimelineBucket {
  t: number;
  requests: number;
  errors: number;
  avg_ms: number | null;
  per_edge: Record<string, number>;
}

export interface Run {
  id: number;
  variant: string;
  repetition: number;
  routing_strategy: string;
  cache_policy: string;
  started_at: number;
  finished_at: number | null;
  summary: (Summary & { timeline?: TimelineBucket[]; events?: { t: number; kind: string; edge_id?: string }[]; evictions?: number; failure?: FailureMetrics }) | null;
}

export interface Experiment {
  id: string;
  name: string;
  type: string;
  config: ExperimentConfig;
  status: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
  summary: { type: string; variants: Variant[]; repetitions: number } | null;
  runs?: Run[];
  progress?: Progress | null;
}

export interface CdnEvent {
  ts: number;
  kind: string;
  edge_id: string | null;
  detail: Record<string, unknown>;
}

export interface RequestRecord {
  ts: number;
  object_id: string;
  edge_id: string | null;
  status_code: number | null;
  ok: number;
  error: string | null;
  latency_ms: number | null;
  resolve_ms: number | null;
  bytes: number;
  cache_status: string | null;
  service_ms: number | null;
  sim_delay_ms: number | null;
  explanation?: string | null;
}

export interface CatalogObject {
  object_id: string;
  size: number;
  content_type: string;
}

export interface CacheDetail {
  edge_id: string;
  stats: CacheStats;
  entries: { object_id: string; size: number; content_type: string; frequency: number; age_s: number; idle_s: number; ttl_remaining_s: number | null; expired: boolean }[];
  evictions: { object_id: string; size: number; reason: string; at: number }[];
}

export interface Snapshot {
  summary: Summary;
  recent: Summary;
  servers: { edge_id: string; status: HealthStatus; load: number; active_requests: number | null; latency_ms: number | null; packet_loss_est: number | null; cache_used_bytes: number | null; cache_hit_ratio: number | null; requests: number | null }[];
  origin: { status: HealthStatus; content_requests: number | null; bytes_served: number | null };
  experiment: Progress | null;
  routing: { strategy: string; weights: Weights };
}

export interface Topology {
  mode: string;
  kind: string;
  note: string;
  nodes: { id: string; kind: string; label: string; detail: string; status: HealthStatus; delay_ms?: number }[];
  links: { source: string; target: string; protocol: string; label: string; profile?: NetworkProfile; profile_kind?: string; measured_rtt_ms?: number | null; status?: HealthStatus; share?: number | null }[];
}

export interface AppConfig {
  mode: string;
  launch: string;
  hostname: string;
  resolver: { host: string; port: number; ttl_s: number };
  health: { probe_interval_s: number; fail_threshold: number; recover_threshold: number };
  client: { name: string; location: string };
  routing: { strategy: string; weights: Weights };
  strategies: { name: string; label: string }[];
  cache: { policy: string; capacity_bytes: number; ttl_seconds: number | null };
}
