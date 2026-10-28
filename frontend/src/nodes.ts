import { $, $$ } from "./dom.ts";
import type { AppState } from "./types.ts";
import type { Host } from "./types.ts";
import { available, filteredHosts, pinnedHosts } from "./selectors.ts";
import { savePinnedHosts } from "./preferences.ts";
import { nowSeconds } from "./state.ts";
import {
  ago as elapsed,
  escapeHTML,
  icon,
  statusBadge,
  resource,
  pct,
  gpuMemory,
  num,
  finite,
  initials,
} from "./format.ts";
export function createNodesView(
  state: AppState,
  toast: (message: string) => void,
) {
  const ago = (ts: unknown) => elapsed(ts, nowSeconds(state));
  function renderNodes() {
    if (!state.overview) return;
    const focused = document.activeElement?.closest<HTMLElement>(
      "#node-grid [data-pin-node]",
    )?.dataset.pinNode;
    const hosts = filteredHosts(state);
    $("#nodes-empty").hidden = !!hosts.length;
    $("#node-grid").innerHTML = hosts.map(nodeCard).join("");
    if (focused)
      $$("#node-grid [data-pin-node]")
        .find((button) => button.dataset.pinNode === focused)
        ?.focus({ preventScroll: true });
    $("#host-count").textContent =
      hosts.length === state.overview.hosts.length
        ? String(hosts.length)
        : `${hosts.length} / ${state.overview.hosts.length}`;
  }
  function pinButton(name: string, labeled = false) {
    const pinned = state.pinned.includes(name || "");
    return `<button type="button" class="pin-button${labeled ? " pin-button-labeled" : ""}" data-pin-node="${escapeHTML(name)}" aria-pressed="${pinned}" aria-label="置顶服务器 ${escapeHTML(name)}" title="${pinned ? "取消置顶" : "置顶关注"} ${escapeHTML(name)}">${icon("pin")}${labeled ? `<span class="pin-label">${pinned ? "已置顶" : "置顶关注"}</span>` : ""}</button>`;
  }
  function renderPinnedNodes() {
    if (!state.overview) return;
    const focused = document.activeElement?.closest<HTMLElement>(
      "#pinned-grid [data-pin-node]",
    )?.dataset.pinNode;
    const hosts = pinnedHosts(state.overview, state.pinned);
    $("#pinned-servers").hidden = false;
    $("#pinned-servers").classList.toggle("is-empty", !hosts.length);
    $("#pinned-count").textContent = String(hosts.length);
    $("#pinned-hint").hidden = !!hosts.length;
    $("#pinned-hint").textContent = hosts.length
      ? ""
      : "点击服务器卡片上的图钉置顶。";
    $("#pinned-grid").hidden = !hosts.length;
    $("#pinned-grid").innerHTML = hosts.map(nodeCard).join("");
    if (focused) {
      const next = $$("#pinned-grid [data-pin-node]").find(
        (button) => button.dataset.pinNode === focused,
      );
      (
        next ||
        document.querySelector<HTMLElement>("#pinned-grid [data-pin-node]") ||
        $<HTMLElement>("#choose-pinned")
      ).focus({ preventScroll: true });
    }
  }
  function syncPinButtons() {
    for (const button of $$("[data-pin-node]")) {
      const name = button.dataset.pinNode || "",
        pinned = state.pinned.includes(name);
      button.setAttribute("aria-pressed", String(pinned));
      button.title = `${pinned ? "取消置顶" : "置顶关注"} ${name}`;
      const label = button.querySelector<HTMLElement>(".pin-label");
      if (label) label.textContent = pinned ? "已置顶" : "置顶关注";
      button.closest(".node-card")?.classList.toggle("is-pinned", pinned);
    }
  }
  function togglePinned(name: string) {
    if (!state.overview?.hosts.some((host) => host.name === name)) return;
    const pinning = !state.pinned.includes(name);
    state.pinned = pinning
      ? [...state.pinned, name]
      : state.pinned.filter((value) => value !== name);
    const saved = savePinnedHosts(() => localStorage, state.pinned);
    renderPinnedNodes();
    syncPinButtons();
    toast(
      `${name} ${pinning ? "已置顶到顶部重点关注区" : "已取消置顶"}${saved ? "" : "（浏览器无法保存设置，仅本标签页生效）"}`,
    );
  }
  function nodeCard(host: Host) {
    const good = available(host),
      system = good ? host.system : {},
      gpus = good ? host.gpus : [];
    const processes = gpus.flatMap((g) => g.processes),
      users = [...new Set(processes.map((p) => p.user).filter(Boolean))];
    const processCount = new Set(processes.map((p) => p.pid)).size;
    const maxDisk = system.disks?.length
      ? Math.max(...system.disks.map((d) => d.percent))
      : null;
    const model = [...new Set(gpus.map((g) => g.name))].join(" / ");
    return `<article class="node-card ${good ? "" : "offline"}${state.pinned.includes(host.name) ? " is-pinned" : ""}" data-node="${escapeHTML(host.name)}">
    <div class="node-header"><div class="node-identity"><span class="node-icon">${icon("server")}</span><div><button class="node-name" data-open-node="${escapeHTML(host.name)}">${escapeHTML(host.name)}</button><div class="node-address mono">${escapeHTML(host.address)}</div></div></div><div class="node-actions">${statusBadge(host.status)}${pinButton(host.name)}</div></div>
    ${good ? `<div class="node-model">${icon("chip")}<span>${escapeHTML(model || "无 GPU / 未检测到 nvidia-smi")}</span>${gpus.length ? `<span>× ${gpus.length}</span>` : ""}</div><div class="gpu-grid" style="--gpu-columns:${Math.min(gpus.length || 1, 8)};--gpu-compact-columns:${Math.min(gpus.length || 1, 4)}">${gpus.map((g) => `<button class="gpu-tile ${(g.temperature ?? 0) >= 85 ? "hot" : (g.utilization ?? 0) >= 10 ? "busy" : "idle"}" data-open-node="${escapeHTML(host.name)}" data-gpu="${g.index}" title="GPU ${g.index} · 使用率 ${pct(g.utilization)} · 显存 ${gpuMemory(g.memory_used_mb)} / ${gpuMemory(g.memory_total_mb)} · ${num(g.temperature)}°C"><span class="gpu-index">GPU ${g.index}</span><span class="gpu-util">${num(g.utilization)}<small>${finite(g.utilization) ? "%" : ""}</small></span><span class="gpu-memory">${finite(g.memory_used_mb) ? num(g.memory_used_mb / 1024, 1) : "—"}G</span></button>`).join("")}</div>${!gpus.length ? '<div class="node-offline-state"><p>CPU 节点或 GPU 指标暂不可读取</p></div>' : ""}` : `<div class="node-offline-state">${icon(host.status === "pending" ? "clock" : "server")}<div><strong>${host.status === "pending" ? "等待节点上报程序发送数据" : "节点离线或采集上报失败"}</strong><p title="${escapeHTML(host.error || "")}">${host.status === "pending" ? "收到节点上报程序数据后自动更新" : escapeHTML(host.error || "节点暂无响应")}</p></div></div>`}
    <div class="node-resources">${resource("CPU", system.cpu_percent)}${resource("内存", system.memory_percent)}${resource("磁盘最高", maxDisk)}</div>
    ${host.warnings?.length ? `<div class="node-warning">${icon("alert")}<span>${escapeHTML(host.warnings[0])}${host.warnings.length > 1 ? ` · 另有 ${host.warnings.length - 1} 项提醒` : ""}</span></div>` : ""}
    <div class="node-footer"><div class="node-users">${
      good
        ? `<div class="mini-avatars">${users
            .slice(0, 3)
            .map(
              (u) =>
                `<span class="mini-avatar" title="${escapeHTML(u)}">${escapeHTML(initials(u))}</span>`,
            )
            .join(
              "",
            )}</div><span>${processCount} 个进程 · ${users.length} 位用户</span>`
        : `<span>${host.status === "pending" ? "等待采集" : `最近在线：${ago(host.last_seen)}`}</span>`
    }</div><button class="detail-link" data-open-node="${escapeHTML(host.name)}">查看详情 ${icon("arrow")}</button></div>
  </article>`;
  }
  return {
    renderNodes,
    renderPinnedNodes,
    syncPinButtons,
    togglePinned,
    pinButton,
  };
}
