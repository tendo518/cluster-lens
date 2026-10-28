import { $ } from "./dom.ts";
import type { AppState } from "./types.ts";
import type { SeriesKey, HistoryPoint } from "./types.ts";
import { finite, clamp, icon, escapeHTML, dateTime, pct } from "./format.ts";
export type Coordinate = [number, number];
// Nulls split paths: missing observations must not be interpolated as zero.
export function chartSegments(
  points: HistoryPoint[],
  key: SeriesKey,
  x: (value: number) => number,
  y: (value: unknown) => number,
): Coordinate[][] {
  const output: Coordinate[][] = [];
  let current: Coordinate[] = [];
  for (const p of points) {
    if (finite(p[key])) current.push([x(p.ts), y(p[key])]);
    else if (current.length) {
      output.push(current);
      current = [];
    }
  }
  if (current.length) output.push(current);
  return output;
}
export function createChart(state: AppState) {
  function renderChart() {
    const container = $("#chart-container"),
      h = state.history;
    const keys: [SeriesKey, SeriesKey] =
      state.metric === "memory" ? ["gpu_memory", "memory"] : ["gpu", "cpu"];
    if (!h?.points.some((p) => keys.some((k) => finite(p[k])))) {
      container.innerHTML = `<div class="chart-empty">${icon("chart")}<span>此时间范围暂无有效采样</span><small>历史从监控服务启动后累积，采集缺失不表示使用率为 0。</small></div>`;
      return;
    }
    const w = Math.max(240, Math.round(container.clientWidth)),
      height = Math.round(container.clientHeight);
    const pad = { left: 46, right: 9, top: 16, bottom: 32 },
      width = w - pad.left - pad.right,
      chartHeight = height - pad.top - pad.bottom;
    const x = (ts: number) =>
      pad.left + ((ts - h.start) / (h.end - h.start)) * width;
    const y = (value: unknown) =>
      pad.top + chartHeight * (1 - clamp(value) / 100);
    const segments = (key: SeriesKey) => chartSegments(h.points, key, x, y);
    const path = (points: Coordinate[]) =>
      points
        .map(([a, b], i) => `${i ? "L" : "M"}${a.toFixed(2)},${b.toFixed(2)}`)
        .join(" ");
    const primary = segments(keys[0]),
      secondary = segments(keys[1]);
    const longRange = h.end - h.start > 36 * 3600;
    function tick(ts: number) {
      const date = new Date(ts * 1000);
      return longRange
        ? date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })
        : date.toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          });
    }
    container.innerHTML = `<svg class="trend-svg" viewBox="0 0 ${w} ${height}" role="img" aria-label="${state.metric === "memory" ? "GPU 显存与系统内存" : "GPU 与 CPU"}使用率趋势，时间范围 ${escapeHTML(dateTime(h.start))} 至 ${escapeHTML(dateTime(h.end))}"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop class="chart-fill-start" offset="0%"/><stop class="chart-fill-end" offset="100%"/></linearGradient></defs>${[0, 25, 50, 75, 100].map((v) => `<line class="chart-grid" x1="${pad.left}" x2="${w - pad.right}" y1="${y(v)}" y2="${y(v)}"/><text class="chart-axis" text-anchor="end" x="${pad.left - 10}" y="${y(v) + 3}">${v}%</text>`).join("")}${[
      0, 1, 2, 3, 4,
    ]
      .map((i) => {
        const ts = h.start + ((h.end - h.start) * i) / 4;
        return `<text class="chart-axis" text-anchor="${i === 0 ? "start" : i === 4 ? "end" : "middle"}" x="${x(ts)}" y="${height - 5}">${tick(ts)}</text>`;
      })
      .join(
        "",
      )}${primary.map((p) => `<path fill="url(#chart-fill)" d="${path(p)} L${p[p.length - 1][0]},${y(0)} L${p[0][0]},${y(0)} Z"/>`).join("")}${secondary.map((p) => `<path class="chart-secondary" d="${path(p)}"/>`).join("")}${primary.map((p) => `<path class="chart-primary" d="${path(p)}"/>${p.length === 1 ? `<circle class="chart-pointer" cx="${p[0][0]}" cy="${p[0][1]}" r="3"/>` : ""}`).join("")}<g id="chart-hover" visibility="hidden"><line id="chart-hover-line" class="chart-hover-line" y1="${pad.top}" y2="${y(0)}"/><circle id="chart-hover-dot" class="chart-pointer" r="4"/></g><rect id="chart-hit" x="${pad.left}" y="${pad.top}" width="${width}" height="${chartHeight}" fill="transparent"/></svg><div class="chart-tooltip" hidden></div>`;
    const tooltip = $(".chart-tooltip", container),
      hover = $("#chart-hover", container);
    $<SVGRectElement>("#chart-hit", container).addEventListener(
      "pointermove",
      (event) => {
        const localX = event.clientX - container.getBoundingClientRect().left;
        const index = Math.max(
          0,
          Math.min(
            h.points.length - 1,
            Math.floor(((localX - pad.left) / width) * h.points.length),
          ),
        );
        const point = h.points[index],
          pointX = x(point.ts);
        hover.setAttribute("visibility", "visible");
        $("#chart-hover-line", container).setAttribute("x1", String(pointX));
        $("#chart-hover-line", container).setAttribute("x2", String(pointX));
        const dot = $("#chart-hover-dot", container);
        dot.setAttribute("cx", String(pointX));
        dot.setAttribute("cy", String(y(point[keys[0]])));
        dot.setAttribute(
          "visibility",
          finite(point[keys[0]]) ? "visible" : "hidden",
        );
        tooltip.innerHTML = `<strong>${escapeHTML(dateTime(point.ts))} · ${h.bucket_seconds}s 均值</strong>${keys.map((key, i) => `<div class="tooltip-row"><span><i class="legend-dot ${i ? "gray" : "green"}"></i>${escapeHTML(i ? $("#secondary-legend").textContent : $("#primary-legend").textContent)}</span><b>${pct(point[key], 1)}</b></div>`).join("")}`;
        tooltip.hidden = false;
        tooltip.style.left = `${Math.max(0, Math.min(w - tooltip.offsetWidth, pointX + 12))}px`;
      },
    );
    $("#chart-hit", container).addEventListener("pointerleave", () => {
      hover.setAttribute("visibility", "hidden");
      tooltip.hidden = true;
    });
  }
  return renderChart;
}
