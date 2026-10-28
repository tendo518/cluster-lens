import { $, closest, inputValue } from "./dom.ts";
import { PIN_STORAGE_KEY, readPinnedHosts } from "./preferences.ts";
import type { AppState, HostStatus } from "./types.ts";
import type { createNodesView } from "./nodes.ts";
import type { createDetailView } from "./detail.ts";

export function bindWorkspaceControls(
  state: AppState,
  nodes: ReturnType<typeof createNodesView>,
  detail: ReturnType<typeof createDetailView>,
  renderProcesses: () => void,
) {
  $("#server-search").addEventListener("input", (event) => {
    state.search = inputValue(event).trim();
    nodes.renderNodes();
  });
  $("#status-filter").addEventListener("change", (event) => {
    const value = inputValue(event);
    if (!["all", "online", "warning", "offline", "pending"].includes(value))
      return;
    state.status = value as HostStatus | "all";
    nodes.renderNodes();
  });
  $("#clear-filters").addEventListener("click", () => {
    state.search = "";
    state.status = "all";
    $<HTMLInputElement>("#server-search").value = "";
    $<HTMLSelectElement>("#status-filter").value = "all";
    nodes.renderNodes();
  });
  $("#process-search").addEventListener("input", (event) => {
    state.processSearch = inputValue(event).trim();
    state.processPage = 1;
    renderProcesses();
  });
  $("#process-page-size").addEventListener("change", (event) => {
    const size = Number(inputValue(event));
    if (![25, 50, 100].includes(size)) {
      $<HTMLSelectElement>("#process-page-size").value = String(
        state.processPageSize,
      );
      return;
    }
    state.processPageSize = size;
    state.processPage = 1;
    renderProcesses();
  });
  $("#process-pagination").addEventListener("click", (event) => {
    const button = closest<HTMLButtonElement>(
      event.target,
      "[data-process-page]",
    );
    if (!button || button.disabled) return;
    state.processPage = Number(button.dataset.processPage);
    renderProcesses();
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== PIN_STORAGE_KEY && event.key !== null) return;
    state.pinned = readPinnedHosts(() => localStorage);
    nodes.renderPinnedNodes();
    nodes.syncPinButtons();
  });
  document.addEventListener("click", (event) => {
    const pin = closest(event.target, "[data-pin-node]");
    if (pin?.dataset.pinNode) {
      nodes.togglePinned(pin.dataset.pinNode);
      return;
    }
    const opener = closest(event.target, "[data-open-node]");
    if (opener?.dataset.openNode)
      detail.openDetail(opener.dataset.openNode, opener.dataset.gpu ?? "");
    const user = closest(event.target, "[data-user]");
    if (user) {
      state.user = user.dataset.user || "";
      state.processPage = 1;
      renderProcesses();
    }
  });
  const dialog = $<HTMLDialogElement>("#node-dialog");
  dialog.addEventListener("change", (event) => {
    if (
      event.target instanceof HTMLSelectElement &&
      event.target.id === "detail-gpu-filter"
    ) {
      state.detailGpu = event.target.value;
      detail.renderDetail();
    }
  });
  $("#close-detail").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    if (
      event.target === dialog &&
      (event.clientX < rect.left || event.clientX > rect.right)
    )
      dialog.close();
  });
  dialog.addEventListener("close", () => {
    // Native close events are queued: an earlier close can arrive after a new
    // node has already reopened the dialog. Do not erase that new selection.
    if (!dialog.open) state.detail = null;
  });
}
