import { $, createToast } from "./dom.ts";
import { createAuth } from "./auth.ts";
import { createApi } from "./api.ts";
import { createState } from "./state.ts";
import { readPinnedHosts } from "./preferences.ts";
import { dateInput } from "./format.ts";
import { createNodesView } from "./nodes.ts";
import { createProcessesView } from "./processes.ts";
import { createDetailView } from "./detail.ts";
import { createHistoryView } from "./history.ts";
import { createOverviewView } from "./overview.ts";
import { createWorkspaceNavigation } from "./navigation.ts";
import { createController } from "./controller.ts";
import { requireAuth, bindAuthControls } from "./auth-controls.ts";
import { bindWorkspaceControls } from "./workspace-controls.ts";
import { bindHistoryControls } from "./history-controls.ts";

// Composition root only; there are no exported globals or test-only hooks.
const state = createState(readPinnedHosts(() => localStorage));
const toast = createToast();
const auth = createAuth({
  local: () => localStorage,
  session: () => sessionStorage,
  onSharedChange: () => location.reload(),
});
const api = createApi({
  auth,
  fetch: (...args) => window.fetch(...args),
  onUnauthorized: requireAuth,
});
const nodes = createNodesView(state, toast);
const processes = createProcessesView(state);
const detail = createDetailView(state, nodes.pinButton);
const history = createHistoryView(state);
const overview = createOverviewView(state, () => auth.token, {
  ...nodes,
  ...processes,
  ...detail,
});
const navigation = createWorkspaceNavigation();
const controller = createController(state, auth, api, {
  ...overview,
  ...history,
  ...navigation,
});
bindAuthControls(auth, api, controller, toast);
bindWorkspaceControls(state, nodes, detail, processes.renderProcesses);
bindHistoryControls(
  state,
  api,
  controller.loadHistory,
  history.renderHistory,
  toast,
);

$("#refresh-button").addEventListener(
  "click",
  () => void controller.refreshOverview(true),
);
$("#auto-refresh").addEventListener("change", () => {
  overview.updateFreshness();
  if ($<HTMLInputElement>("#auto-refresh").checked)
    void controller.refreshOverview(true);
});
window.addEventListener("appearance-storage-error", () =>
  toast("浏览器无法保存配色设置，仅本标签页生效"),
);
let resizeTimer: ReturnType<typeof setTimeout>;
new ResizeObserver(() => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(history.renderChart, 120);
}).observe($("#chart-container"));
$("#local-date").textContent = new Date().toLocaleDateString("zh-CN", {
  year: "numeric",
  month: "long",
  day: "numeric",
  weekday: "short",
});
$("#timezone-label").textContent =
  `时间均为本地时区 · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
$<HTMLInputElement>("#range-start").value = dateInput(
  new Date(Date.now() - 6 * 3600000),
);
$<HTMLInputElement>("#range-end").value = dateInput(new Date());
const canRefresh = () =>
  $<HTMLInputElement>("#auto-refresh").checked &&
  !document.hidden &&
  !$<HTMLDialogElement>("#auth-dialog").open;
setInterval(overview.updateFreshness, 1000);
setInterval(() => {
  if (canRefresh()) void controller.refreshOverview();
}, 5000);
document.addEventListener("visibilitychange", () => {
  if (canRefresh()) void controller.refreshOverview(true);
});
void controller.refreshOverview(true);
