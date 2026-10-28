import { $ } from "./dom.ts";
import type { AppState } from "./types.ts";
import { available, allProcesses, processCoverage } from "./selectors.ts";
import { nowSeconds as now } from "./state.ts";
import {
  ago as elapsed,
  escapeHTML,
  finite,
  sum,
  mean,
  icon,
  clamp,
  num,
  shortTime,
} from "./format.ts";
interface OverviewViews {
  renderNodes: () => void;
  renderPinnedNodes: () => void;
  renderProcesses: () => void;
  renderDetail: () => void;
}
export function createOverviewView(
  state: AppState,
  token: () => string,
  {
    renderNodes,
    renderPinnedNodes,
    renderProcesses,
    renderDetail,
  }: OverviewViews,
) {
  const nowSeconds = () => now(state);
  const ago = (ts: unknown) => elapsed(ts, nowSeconds());
  function renderOverview() {
    const data = state.overview;
    if (!data) return;
    $("#logout-button").hidden = !token();
    $("#demo-banner").hidden = !data.demo;
    $("#sidebar-source").textContent = data.demo
      ? "演示数据 · 本地模拟"
      : "已连接监控服务";
    $("#nav-host-count").textContent = String(data.hosts.length);
    $("#host-count").textContent = String(data.hosts.length);
    $("#retention-label").textContent = ` 历史保留 ${data.retention_days} 天。`;
    const hostOptions = JSON.stringify(data.hosts.map((h) => h.name));
    if ($<HTMLSelectElement>("#history-host").dataset.hosts !== hostOptions) {
      $<HTMLSelectElement>("#history-host").innerHTML =
        `<option value="">全部服务器</option>${data.hosts.map((h) => `<option value="${escapeHTML(h.name)}">${escapeHTML(h.name)}</option>`).join("")}`;
      $<HTMLSelectElement>("#history-host").value = state.historyHost;
      $<HTMLSelectElement>("#history-host").dataset.hosts = hostOptions;
    }
    renderSummary();
    renderNodes();
    renderPinnedNodes();
    renderProcesses();
    if ($<HTMLDialogElement>("#node-dialog").open && state.detail)
      renderDetail();
    updateFreshness();
  }
  function renderSummary() {
    const hosts = state.overview?.hosts || [],
      online = hosts.filter(available),
      gpus = online.flatMap((h) => h.gpus);
    const measured = gpus.map((g) => g.utilization).filter(finite);
    const mem = gpus.filter(
      (g) =>
        finite(g.memory_used_mb) &&
        finite(g.memory_total_mb) &&
        g.memory_total_mb > 0,
    );
    const used = sum(mem, "memory_used_mb"),
      total = sum(mem, "memory_total_mb");
    const processes = allProcesses(state.overview),
      users = new Set(processes.map((p) => p.user).filter(Boolean));
    const coverage = processCoverage(gpus, online),
      unreadable = coverage.unavailable > 0 && !processes.length;
    const uniqueProcesses = new Set(processes.map((p) => `${p.host}:${p.pid}`))
      .size;
    const normal = hosts.filter((h) => h.status === "online").length,
      warnings = hosts.filter((h) => h.status === "warning").length;
    const offline = hosts.filter((h) => h.status === "offline").length,
      pending = hosts.filter((h) => h.status === "pending").length;
    const avg = mean(measured),
      busy = measured.filter((v) => v >= 10).length;
    const card = (
      title: string,
      symbol: string,
      value: string,
      foot: string,
      bar: number | null = null,
    ) =>
      `<article class="summary-card"><div class="summary-top"><span>${title}</span><span class="summary-icon">${icon(symbol)}</span></div><div class="summary-value">${value}</div>${bar !== null ? `<div class="mini-bar"><span style="width:${clamp(bar)}%"></span></div>` : ""}<div class="summary-foot">${foot}</div></article>`;
    $("#summary").innerHTML = [
      card(
        "在线服务器",
        "server",
        `${online.length}<small>/ ${hosts.length} 台</small>`,
        `<span class="tiny-status"></span>${normal} 正常${warnings ? ` · ${warnings} 需关注` : ""}<span class="tiny-status red"></span>${offline} 离线${pending ? ` · ${pending} 待上报` : ""}`,
      ),
      card(
        "GPU 平均使用率",
        "chip",
        `${num(avg, 1)}<span class="unit">%</span>`,
        `<span class="good">${busy} 张繁忙</span><span> / ${gpus.length} 张在线 GPU</span>`,
        avg,
      ),
      card(
        "GPU 显存使用率",
        "memory",
        `${total ? num((used / total) * 100, 1) : "—"}<span class="unit">%</span>`,
        `${total ? `${num(used / 1024, 1)} / ${num(total / 1024, 0)} GiB 已使用` : "等待有效显存采样"}`,
        total ? (used / total) * 100 : null,
      ),
      card(
        "活跃 GPU 用户",
        "users",
        `${online.length && !unreadable ? users.size : "—"}<small>位${coverage.unavailable && processes.length ? "（部分可读）" : ""}</small>`,
        `<span class="process-summary-coverage">${
          unreadable
            ? `${coverage.readable ? "部分 GPU " : ""}进程信息不可读取`
            : `${coverage.unavailable ? "已知 " : ""}${uniqueProcesses} 个进程 · ${processes.length} 条 GPU 关联${processes.some((p) => !p.user) ? "（含未知用户）" : ""}${coverage.unavailable ? " · 部分 GPU 进程信息不可读取" : ""}`
        }</span>`,
      ),
    ].join("");
  }
  function updateFreshness() {
    const data = state.overview,
      auto = $<HTMLInputElement>("#auto-refresh").checked;
    const latest = Math.max(
      0,
      ...(data?.hosts || []).map((h) => h.checked_at || 0),
    );
    const delayed =
      auto &&
      state.connected &&
      ((state.lastSuccess && Date.now() - state.lastSuccess > 60000) ||
        (latest &&
          nowSeconds() - latest > Math.max(60, (data?.interval || 0) * 2)));
    const badge = $("#live-badge");
    badge.classList.toggle("disconnected", !state.connected);
    badge.classList.toggle("paused", state.connected && (!auto || !!delayed));
    badge.innerHTML = `<span class="live-dot"></span>${!state.connected ? "连接中断" : !auto ? "已暂停刷新" : delayed ? "数据待更新" : data?.collecting ? "采集中" : "实时监控"}`;
    if (!data) return;
    $(".sidebar-status").classList.toggle("disconnected", !state.connected);
    $("#sidebar-source").textContent = !state.connected
      ? "监控连接已中断"
      : data.demo
        ? "演示数据 · 本地模拟"
        : "已连接监控服务";
    $("#update-label").textContent = latest
      ? `最近更新 ${shortTime(latest)} · ${ago(latest)}`
      : "正在等待节点上报程序首次上报…";
    $("#collection-label").textContent =
      `${data.mode === "push" ? `节点约每 ${data.interval} 秒上报 · ${data.stale_after} 秒无上报视为离线` : `每 ${data.interval} 秒模拟采集`}${!auto ? " · 页面自动刷新已暂停" : ""}`;
  }
  return { renderOverview, updateFreshness };
}
