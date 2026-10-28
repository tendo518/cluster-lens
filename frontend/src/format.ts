import type { HostStatus } from "./types.ts";
export const escapeHTML = (value: unknown) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ] || c,
  );
export const icon = (name: string) =>
  `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
export const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
export const num = (value: unknown, digits = 0) =>
  finite(value)
    ? value.toLocaleString("zh-CN", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      })
    : "—";
export const pct = (value: unknown, digits = 0) =>
  finite(value) ? `${num(value, digits)}%` : "—";
export const clamp = (value: unknown) =>
  finite(value) ? Math.min(100, Math.max(0, value)) : 0;
export const mean = (values: number[]) =>
  values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
export const sum = <T, K extends keyof T>(values: T[], key: K): number =>
  values.reduce((total, value) => {
    const item = value[key];
    return total + (finite(item) ? item : 0);
  }, 0);
export const statusLabels = {
  online: "正常",
  warning: "需关注",
  offline: "离线",
  pending: "待上报",
};
export const statusBadge = (status: HostStatus) =>
  `<span class="status-badge ${escapeHTML(status)}">${statusLabels[status] || "未知"}</span>`;
export const dateTime = (ts: unknown) =>
  finite(ts)
    ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false })
    : "暂无记录";
export const shortTime = (ts: unknown) =>
  finite(ts)
    ? new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })
    : "—";
export const dateInput = (date: Date) =>
  new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
export const initials = (user: unknown) =>
  String(user || "?")
    .slice(0, 2)
    .toUpperCase();
export function bytes(value: unknown, digits = 1) {
  if (!finite(value)) return "—";
  let amount = value;
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index++;
  }
  return `${num(amount, index ? digits : 0)} ${units[index]}`;
}
export const gpuMemory = (mb: unknown) =>
  bytes(finite(mb) ? mb * 1024 ** 2 : null);
export function uptime(seconds: unknown) {
  if (!finite(seconds)) return "—";
  const days = Math.floor(seconds / 86400),
    hours = Math.floor((seconds % 86400) / 3600);
  return days
    ? `${days} 天 ${hours} 小时`
    : `${hours} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟`;
}
export function ago(ts: unknown, now = Date.now() / 1000) {
  if (!finite(ts)) return "暂无成功采集记录";
  const seconds = Math.max(0, Math.floor(now - ts));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}
export function meter(value: unknown, extra = "") {
  return `<div class="resource-meter ${extra}" role="meter" aria-label="使用率" aria-valuemin="0" aria-valuemax="100" ${finite(value) ? `aria-valuenow="${clamp(value)}"` : 'aria-valuetext="无数据"'}><span style="width:${clamp(value)}%"></span></div>`;
}
export function resource(label: string, value: unknown) {
  return `<div><div class="resource-label"><span>${label}</span><strong>${pct(value)}</strong></div>${meter(value, finite(value) && value >= 90 ? "amber" : "")}</div>`;
}
