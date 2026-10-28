import { $ } from "./dom.ts";
import type { AppState } from "./types.ts";
import { num, pct, dateTime, escapeHTML } from "./format.ts";
import { createChart } from "./charts.ts";
export function createHistoryView(state: AppState) {
  const renderChart = createChart(state);
  function renderHistory() {
    const h = state.history,
      a = h?.aggregate || {};
    const memory = state.metric === "memory";
    $("#primary-legend").textContent = memory ? "GPU 显存" : "GPU";
    $("#secondary-legend").textContent = memory ? "系统内存" : "CPU";
    $("#period-stats").innerHTML =
      `<div><div class="period-stat-label">时段 ${memory ? "显存" : "GPU"} 均值</div><div class="period-stat-value">${num(memory ? a.avg_gpu_memory : a.avg_gpu, 1)}<small>%</small></div><div class="period-stat-note">${memory ? "按有效显存容量加权" : `单卡峰值 ${pct(a.peak_gpu)}`}</div></div><div><div class="period-stat-label">时段 ${memory ? "内存" : "CPU"} 均值</div><div class="period-stat-value">${num(memory ? a.avg_memory : a.avg_cpu, 1)}<small>%</small></div><div class="period-stat-note">按节点有效样本平均</div></div><div><div class="period-stat-label">繁忙 GPU 卡时</div><div class="period-stat-value">${h?.aggregate.samples ? num(a.busy_gpu_hours, 1) : "—"}<small>GPU·h</small></div><div class="period-stat-note">采集覆盖率 ${pct(a.coverage, 1)}</div></div>`;
    $("#range-label").textContent = h
      ? `${dateTime(h.start)} — ${dateTime(h.end)}`
      : "";
    $("#usage-rows").innerHTML = h?.stats?.length
      ? h.stats
          .map(
            (row) =>
              `<tr><td><button class="table-link mono" data-open-node="${escapeHTML(row.host)}">${escapeHTML(row.host)}</button></td><td class="mono">${pct(row.avg_gpu, 1)} / ${pct(row.peak_gpu)}</td><td class="mono">${pct(row.avg_cpu, 1)}</td><td class="mono">${pct(row.avg_gpu_memory, 1)}</td><td class="mono">${row.samples ? num(row.busy_gpu_hours, 2) : "—"}</td><td class="mono">${pct(row.availability, 1)}</td><td class="mono ${(row.coverage ?? 0) < 50 ? "coverage-low" : ""}">${pct(row.coverage, 1)}</td></tr>`,
          )
          .join("")
      : '<tr><td colspan="7" class="table-empty">暂无历史统计，数据会在采集后逐步累积。</td></tr>';
    renderChart();
  }
  return { renderHistory, renderChart };
}
