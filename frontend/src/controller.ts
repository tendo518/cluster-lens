import type { AppState } from "./types.ts";
import type { Auth } from "./auth.ts";
import { AuthError } from "./auth.ts";
import type { Api } from "./api.ts";
import { $, errorMessage } from "./dom.ts";
import { nowSeconds } from "./state.ts";
import { historyParams } from "./selectors.ts";

interface Views {
  renderOverview: () => void;
  renderHistory: () => void;
  updateFreshness: () => void;
  restoreWorkspaceAnchor: (ready: boolean) => void;
}
export function createController(
  state: AppState,
  auth: Auth,
  api: Api,
  views: Views,
) {
  async function loadHistory() {
    const version = ++state.historyRequest;
    $("#history-error").hidden = true;
    $<HTMLButtonElement>("#export-button").disabled = true;
    $("#chart-container").setAttribute("aria-busy", "true");
    try {
      const history = await api.history(
        historyParams(state, nowSeconds(state)),
      );
      if (version !== state.historyRequest) return;
      state.history = history;
      views.renderHistory();
    } catch (error) {
      if (version !== state.historyRequest || error instanceof AuthError)
        return;
      state.history = null;
      $("#history-error").textContent =
        `历史数据读取失败：${errorMessage(error)}`;
      $("#history-error").hidden = false;
      views.renderHistory();
    } finally {
      if (version === state.historyRequest) {
        $<HTMLButtonElement>("#export-button").disabled = !state.history;
        $("#chart-container").setAttribute("aria-busy", "false");
      }
    }
  }
  async function refreshOverview(forceHistory = false) {
    if (state.refreshing) return;
    state.refreshing = true;
    $<HTMLButtonElement>("#refresh-button").disabled = true;
    $("#refresh-button .icon").classList.add("rotating");
    const snapshot = auth.snapshot();
    try {
      const data = await api.overview();
      auth.check(snapshot);
      auth.migrateLegacy(snapshot);
      state.overview = data;
      state.connected = true;
      state.lastSuccess = Date.now();
      state.clockOffset = data.server_time - Date.now() / 1000;
      $("#connection-error").hidden = true;
      views.renderOverview();
      if (
        forceHistory ||
        !state.history ||
        state.historyCycle !== data.last_cycle
      ) {
        state.historyCycle = data.last_cycle;
        await loadHistory();
      }
    } catch (error) {
      if (!(error instanceof AuthError)) {
        state.connected = false;
        $("#connection-error").textContent =
          `监控服务连接中断：${errorMessage(error)}。页面保留最后一次数据，请检查服务后重试。`;
        $("#connection-error").hidden = false;
      }
    } finally {
      state.refreshing = false;
      $<HTMLButtonElement>("#refresh-button").disabled = false;
      $("#refresh-button .icon").classList.remove("rotating");
      views.updateFreshness();
      views.restoreWorkspaceAnchor(!!state.overview);
    }
  }
  return { loadHistory, refreshOverview };
}
export type Controller = ReturnType<typeof createController>;
