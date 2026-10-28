import type { AppState } from "./types.ts";

export function createState(pinned: string[] = []): AppState {
  return {
    overview: null,
    history: null,
    hours: 1,
    custom: null,
    metric: "compute",
    historyHost: "",
    search: "",
    status: "all",
    processSearch: "",
    user: "",
    processPage: 1,
    processPageSize: 25,
    detail: null,
    detailGpu: "",
    pinned,
    connected: false,
    lastSuccess: 0,
    clockOffset: 0,
    refreshing: false,
    historyRequest: 0,
    historyCycle: null,
  };
}
export const nowSeconds = (state: AppState) =>
  Date.now() / 1000 + state.clockOffset;
