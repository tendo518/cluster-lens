import { $, $$ } from "./dom.ts";
import type { AppState } from "./types.ts";
import { available } from "./selectors.ts";
import { processRow } from "./processes.ts";
import {
  escapeHTML,
  statusBadge,
  dateTime,
  icon,
  pct,
  num,
  bytes,
  uptime,
  shortTime,
  meter,
  gpuMemory,
} from "./format.ts";
export function createDetailView(
  state: AppState,
  pinButton: (name: string, labeled?: boolean) => string,
) {
  function openDetail(name: string, gpu = "") {
    if (!state.overview?.hosts.some((h) => h.name === name)) return;
    state.detail = name;
    state.detailGpu = String(gpu);
    renderDetail();
    if (!$<HTMLDialogElement>("#node-dialog").open)
      $<HTMLDialogElement>("#node-dialog").showModal();
    $<HTMLDialogElement>("#node-dialog").scrollTop = 0;
  }
  function renderDetail() {
    const host = state.overview?.hosts.find((h) => h.name === state.detail);
    if (!host) return;
    const focused = document.activeElement;
    const focusedPin = focused?.closest<HTMLElement>(
      "#node-detail [data-pin-node]",
    )?.dataset.pinNode;
    const focusedFilter = focused?.id === "detail-gpu-filter";
    const system = host.system || {},
      good = available(host),
      scroll = $<HTMLDialogElement>("#node-dialog").scrollTop;
    const processes = host.gpus
      .flatMap((g) =>
        g.processes.map((p) => ({
          ...p,
          host: host.name,
          gpu: g.index,
          capacity: g.memory_total_mb,
        })),
      )
      .filter(
        (p) => state.detailGpu === "" || String(p.gpu) === state.detailGpu,
      );
    const fact = (label: string, value: unknown) =>
      `<div><dt>${label}</dt><dd>${escapeHTML(value)}</dd></div>`;
    const systemStat = (label: string, value: string, sub: string) =>
      `<div class="detail-system-stat"><span class="label">${label}</span><strong>${value}</strong><small>${sub}</small></div>`;
    $("#node-detail").innerHTML =
      `<div class="detail-heading"><div class="detail-title-row"><h2 id="detail-title">${escapeHTML(host.name)} ${statusBadge(host.status)}</h2>${pinButton(host.name, true)}</div><p><span class="mono">${escapeHTML(host.address)}</span> · ${escapeHTML(host.groups.join(" / "))}</p></div>
  ${host.warnings?.length ? `<div class="detail-warnings">${host.warnings.map((w) => `<p>${escapeHTML(w)}</p>`).join("")}</div>` : ""}
  ${
    !good
      ? `<div class="detail-offline"><h3>${host.status === "pending" ? "正在等待采集" : "节点当前离线"}</h3><p>${escapeHTML(host.error || "采集结果将在连接成功后自动显示。")}</p><p>最近成功采集：${dateTime(host.last_seen)}</p><p>不会将离线节点的历史指标作为当前状态展示。</p></div>`
      : `<section class="detail-section"><h3>${icon("server")}系统运行状态</h3><div class="detail-system-grid">${systemStat("CPU 使用率", pct(system.cpu_percent, 1), `${num(system.cpu_count)} 逻辑核心`)}${systemStat("内存使用率", pct(system.memory_percent, 1), `${bytes(system.memory_used_bytes)} / ${bytes(system.memory_total_bytes)}`)}${systemStat("运行时间", `${Math.floor((system.uptime_seconds || 0) / 86400)} <small>天</small>`, uptime(system.uptime_seconds))}${systemStat("网络接收 / 秒", bytes(system.network_rx_bytes_s), `发送 ${bytes(system.network_tx_bytes_s)}/s`)}${systemStat("磁盘读取 / 秒", bytes(system.disk_read_bytes_s), `写入 ${bytes(system.disk_write_bytes_s)}/s`)}${systemStat("Swap 使用率", pct(system.swap_percent, 1), `${bytes(system.swap_used_bytes)} / ${bytes(system.swap_total_bytes)}`)}</div><dl class="detail-facts">${fact("操作系统", system.os || "—")}${fact("内核", system.kernel || "—")}${fact("系统负载 1/5/15m", (system.load || []).map((v) => num(v, 2)).join(" / "))}${fact("采集耗时", `${num(host.collection_ms)} ms`)}${fact("最近更新", shortTime(host.checked_at))}${fact("GPU 驱动", host.gpus[0]?.driver || "—")}</dl></section>
  <section class="detail-section"><h3>${icon("chip")}GPU 逐卡状态 <span class="count-badge">${host.gpus.length}</span></h3>${host.gpus.length ? host.gpus.map((g) => `<article class="detail-gpu"><div class="detail-gpu-header"><strong>GPU ${g.index} · ${escapeHTML(g.name)}</strong><small>${g.processes.length} 条进程记录</small></div><div class="detail-gpu-metrics"><div><label>计算使用率</label><b>${pct(g.utilization, 1)}</b>${meter(g.utilization)}</div><div><label>显存使用率</label><b>${pct(g.memory_percent, 1)}</b>${meter(g.memory_percent)}</div><div><label>温度 / 功耗</label><b>${num(g.temperature)}°C <small>/ ${num(g.power_w)} W</small></b><small>功率上限 ${num(g.power_limit_w)} W</small></div></div><div class="detail-gpu-info"><span>显存 ${gpuMemory(g.memory_used_mb)} / ${gpuMemory(g.memory_total_mb)}</span><span>风扇 ${pct(g.fan_percent)} · ${escapeHTML(g.uuid || "无 UUID")}</span></div></article>`).join("") : '<p class="muted">未检测到 GPU，或 GPU 指标不可读取。请检查此节点是否安装 NVIDIA 驱动及 nvidia-smi。</p>'}</section>
  <section class="detail-section"><h3>${icon("users")}GPU 进程 <select id="detail-gpu-filter" aria-label="按 GPU 筛选进程"><option value="">全部 GPU</option>${host.gpus.map((g) => `<option value="${g.index}" ${String(g.index) === state.detailGpu ? "selected" : ""}>GPU ${g.index}</option>`).join("")}</select></h3><div class="table-scroll"><table class="data-table"><thead><tr><th>用户</th><th>GPU</th><th>PID</th><th>进程</th><th>类型</th><th>显存占用</th></tr></thead><tbody>${processes.length ? processes.map((p) => processRow(p, true)).join("") : '<tr><td colspan="6" class="table-empty">暂无可读取的 GPU 进程</td></tr>'}</tbody></table></div></section>
  <section class="detail-section"><h3>${icon("memory")}磁盘与挂载点</h3><div class="table-scroll"><table class="data-table"><thead><tr><th>挂载点</th><th>使用率</th><th>已用 / 总计</th><th>可用</th></tr></thead><tbody>${(system.disks || []).map((d) => `<tr><td class="mono">${escapeHTML(d.mount)}</td><td>${pct(d.percent, 1)}${meter(d.percent, d.percent >= 90 ? "amber" : "")}</td><td class="mono">${bytes(d.used_bytes)} / ${bytes(d.total_bytes)}</td><td class="mono">${bytes(d.available_bytes)}</td></tr>`).join("") || '<tr><td colspan="4" class="table-empty">磁盘数据暂不可读</td></tr>'}</tbody></table></div></section>`
  }
  <p class="detail-caption">进程类型：C = 计算 / G = 图形</p>`;
    // A node may go offline while this filter has focus; the replacement
    // offline inspector intentionally has no filter to restore.
    const control = focusedFilter
      ? document.querySelector<HTMLSelectElement>("#detail-gpu-filter")
      : focusedPin
        ? $$("#node-detail [data-pin-node]").find(
            (button) => button.dataset.pinNode === focusedPin,
          )
        : null;
    control?.focus({ preventScroll: true });
    $<HTMLDialogElement>("#node-dialog").scrollTop = scroll;
  }
  return { openDetail, renderDetail };
}
