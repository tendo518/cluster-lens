import type {
  AppState,
  Host,
  Overview,
  ProcessRow,
  TimeRange,
} from "./types.ts";
export const available = (host: Host): boolean =>
  ["online", "warning"].includes(host.status);
export function allProcesses(overview: Overview | null): ProcessRow[] {
  return (overview?.hosts || []).filter(available).flatMap((host) =>
    host.gpus.flatMap((gpu) =>
      gpu.processes.map((process) => ({
        ...process,
        host: host.name,
        address: host.address,
        gpu: gpu.index,
        capacity: gpu.memory_total_mb,
      })),
    ),
  );
}
export function filteredHosts(
  state: Pick<AppState, "overview" | "search" | "status">,
): Host[] {
  const query = state.search.toLowerCase();
  return (state.overview?.hosts || []).filter(
    (h) =>
      (state.status === "all" || h.status === state.status) &&
      `${h.name} ${h.address} ${h.gpus.map((g) => g.name).join(" ")}`
        .toLowerCase()
        .includes(query),
  );
}
export function pinnedHosts(
  overview: Overview | null,
  pinned: string[],
): Host[] {
  const managed = new Map(
    (overview?.hosts || []).map((host) => [host.name, host]),
  );
  return pinned
    .map((name) => managed.get(name))
    .filter((host): host is Host => !!host);
}
export function processPage(
  state: Pick<
    AppState,
    "overview" | "user" | "processSearch" | "processPage" | "processPageSize"
  >,
) {
  const all = allProcesses(state.overview),
    users = new Map<string, number>();
  for (const p of all)
    if (p.user)
      users.set(p.user, (users.get(p.user) || 0) + (p.memory_mb || 0));
  const query = state.processSearch.toLowerCase();
  // Filter and sort the entire association set BEFORE pagination. A PID can be
  // present on multiple GPUs; count processes separately from associations.
  const rows = all
    .filter(
      (p) =>
        (!state.user || p.user === state.user) &&
        `${p.user || "未知"} ${p.host} ${p.address} ${p.pid} ${p.name} GPU${p.gpu}`
          .toLowerCase()
          .includes(query),
    )
    .sort((a, b) => (b.memory_mb || 0) - (a.memory_mb || 0));
  const pages = Math.ceil(rows.length / state.processPageSize),
    last = Math.max(1, pages);
  const page = Math.min(last, Math.max(1, state.processPage));
  const start = (page - 1) * state.processPageSize,
    end = Math.min(start + state.processPageSize, rows.length);
  return {
    all,
    users,
    rows,
    pages,
    last,
    page,
    start,
    end,
    visible: rows.slice(start, end),
  };
}
export function historyParams(
  state: Pick<AppState, "custom" | "hours" | "history" | "historyHost">,
  now: number,
  useDisplayed = false,
): URLSearchParams {
  const end = state.custom ? state.custom.end : now;
  const start = state.custom ? state.custom.start : end - state.hours * 3600;
  const range = useDisplayed && state.history ? state.history : { start, end };
  return new URLSearchParams({
    start: String(range.start),
    end: String(range.end),
    ...(state.historyHost ? { host: state.historyHost } : {}),
  });
}
export function validRange(range: TimeRange, now: number): boolean {
  const { start, end } = range;
  return (
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    start < end &&
    end - start <= 31 * 86400 &&
    end <= now + 60
  );
}
