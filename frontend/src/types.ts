// Public JSON API contracts. Missing/unknown measurements remain null/undefined;
// renderers must never turn missing or offline metrics into a measured zero.
export type Measurement = number | null | undefined;
export type HostStatus = "online" | "warning" | "offline" | "pending";
export interface GPUProcess {
  pid: number;
  user: string | null;
  name: string;
  type?: string | null;
  memory_mb?: Measurement;
}
export interface DCGMMetrics {
  sm_active_percent?: Measurement;
  sm_occupancy_percent?: Measurement;
  tensor_active_percent?: Measurement;
  dram_active_percent?: Measurement;
  pcie_rx_bytes_s?: Measurement;
  pcie_tx_bytes_s?: Measurement;
  nvlink_rx_bytes_s?: Measurement;
  nvlink_tx_bytes_s?: Measurement;
  memory_temperature?: Measurement;
  sm_clock_mhz?: Measurement;
  memory_clock_mhz?: Measurement;
  ecc_sbe_volatile?: Measurement;
  ecc_dbe_volatile?: Measurement;
  xid_last?: Measurement;
}
export interface GPU {
  index: number;
  name: string;
  uuid?: string;
  driver?: string;
  utilization?: Measurement;
  memory_total_mb?: Measurement;
  memory_used_mb?: Measurement;
  memory_percent?: Measurement;
  temperature?: Measurement;
  power_w?: Measurement;
  power_limit_w?: Measurement;
  fan_percent?: Measurement;
  // Legacy collectors omit this flag; only explicit false means unreadable.
  processes_available?: boolean;
  processes: GPUProcess[];
  dcgm?: DCGMMetrics | null;
}
export interface Disk {
  mount: string;
  percent: number;
  used_bytes?: Measurement;
  total_bytes?: Measurement;
  available_bytes?: Measurement;
}
export interface SystemMetrics {
  cpu_percent?: Measurement;
  cpu_count?: Measurement;
  memory_percent?: Measurement;
  memory_used_bytes?: Measurement;
  memory_total_bytes?: Measurement;
  uptime_seconds?: Measurement;
  network_rx_bytes_s?: Measurement;
  network_tx_bytes_s?: Measurement;
  disk_read_bytes_s?: Measurement;
  disk_write_bytes_s?: Measurement;
  swap_percent?: Measurement;
  swap_used_bytes?: Measurement;
  swap_total_bytes?: Measurement;
  os?: string;
  kernel?: string;
  load?: number[];
  disks?: Disk[];
}
export interface Host {
  name: string;
  address: string;
  groups: string[];
  status: HostStatus;
  system: SystemMetrics;
  gpus: GPU[];
  gpu_provider?: "nvidia-smi" | "dcgm";
  warnings?: string[];
  error?: string | null;
  checked_at?: Measurement;
  last_seen?: Measurement;
  collection_ms?: Measurement;
}
export interface Overview {
  hosts: Host[];
  server_time: number;
  last_cycle: number | null;
  interval: number;
  stale_after: number;
  retention_days: number;
  mode: "push" | "demo";
  demo: boolean;
  collecting: boolean;
}
export interface ProcessRow extends GPUProcess {
  host: string;
  address?: string;
  gpu: number;
  capacity?: Measurement;
}
export interface TimeRange {
  start: number;
  end: number;
}
export type HistoryMetric = "compute" | "memory";
export type SeriesKey = "gpu" | "cpu" | "gpu_memory" | "memory";
export interface HistoryPoint extends Record<SeriesKey, Measurement> {
  ts: number;
}
export interface HistoryAggregate {
  samples?: number;
  avg_gpu?: Measurement;
  peak_gpu?: Measurement;
  avg_cpu?: Measurement;
  avg_gpu_memory?: Measurement;
  avg_memory?: Measurement;
  busy_gpu_hours?: Measurement;
  coverage?: Measurement;
  availability?: Measurement;
}
export interface History extends TimeRange {
  bucket_seconds: number;
  points: HistoryPoint[];
  stats: (HistoryAggregate & { host: string })[];
  aggregate: HistoryAggregate;
}
export interface AppState {
  overview: Overview | null;
  history: History | null;
  hours: number;
  custom: TimeRange | null;
  metric: HistoryMetric;
  historyHost: string;
  search: string;
  status: HostStatus | "all";
  processSearch: string;
  user: string;
  processPage: number;
  processPageSize: number;
  detail: string | null;
  detailGpu: string;
  pinned: string[];
  connected: boolean;
  lastSuccess: number;
  clockOffset: number;
  refreshing: boolean;
  historyRequest: number;
  historyCycle: number | null;
}
