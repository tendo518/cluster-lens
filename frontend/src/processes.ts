import { $ } from "./dom.ts";
import type { AppState } from "./types.ts";
import type { ProcessRow } from "./types.ts";
import { available, processCoverage, processPage } from "./selectors.ts";
import {
  icon,
  escapeHTML,
  num,
  initials,
  gpuMemory,
  meter,
  finite,
} from "./format.ts";
export function processRow(p: ProcessRow, compact = false) {
  const user = p.user || "未知 / 无权限";
  return `<tr><td><span class="user-cell"><span class="user-avatar">${escapeHTML(initials(p.user))}</span><span title="${p.user ? escapeHTML(p.user) : "用户不可读取、进程已退出或容器 PID 不可见"}">${escapeHTML(user)}</span></span></td><td>${compact ? `<span class="mono">GPU ${escapeHTML(p.gpu)}</span>` : `<button class="table-link mono" data-open-node="${escapeHTML(p.host)}">${escapeHTML(p.host)}</button><span class="muted"> / GPU ${escapeHTML(p.gpu)}</span>`}</td><td class="mono">${escapeHTML(p.pid)}</td><td><span class="process-name mono" title="${escapeHTML(p.name)}">${escapeHTML(p.name.split("/").filter(Boolean).pop() || p.name)}</span></td><td><span class="type-tag">${escapeHTML(p.type || "—")}</span></td><td><div class="memory-cell"><span class="mono">${gpuMemory(p.memory_mb)}</span>${meter(finite(p.memory_mb) && p.capacity ? (p.memory_mb / p.capacity) * 100 : null)}</div></td></tr>`;
}

export function createProcessesView(state: AppState) {
  function renderProcesses() {
    const { all, users, rows, pages, last, page, start, end, visible } =
      processPage(state);
    state.processPage = page;
    const coverage = processCoverage(
      (state.overview?.hosts || []).filter(available).flatMap((h) => h.gpus),
      (state.overview?.hosts || []).filter(available),
    );
    const partial = coverage.unavailable > 0,
      unreadable = partial && !all.length;
    const coverageHint = partial
      ? `${coverage.readable ? "部分 GPU " : ""}进程信息不可读取${all.length ? "，仅显示已知进程（部分可读）" : "，无法确认完整进程活动"}。`
      : "";
    const count = new Set(all.map((p) => `${p.host}:${p.pid}`)).size;
    $("#process-count").textContent = unreadable
      ? "—"
      : `${count}${partial ? "（部分可读）" : ""}`;
    const selectedUsers = [...users].sort((a, b) => b[1] - a[1]).slice(0, 12);
    $("#user-strip").innerHTML =
      `<button class="user-pill ${!state.user ? "selected" : ""}" data-user="">${icon("users")}全部用户 <b>${unreadable ? "—" : users.size}</b></button>${selectedUsers.map(([user, mb]) => `<button class="user-pill ${state.user === user ? "selected" : ""}" data-user="${escapeHTML(user)}">${escapeHTML(user)} <b>${num(mb / 1024, 1)}G</b></button>`).join("")}${users.size > 12 ? '<span class="muted"><small>其余用户可通过搜索查看</small></span>' : ""}${partial ? `<span class="process-coverage-hint muted"><small>${coverageHint}</small></span>` : ""}`;
    $("#process-rows").innerHTML = rows.length
      ? visible.map((p) => processRow(p)).join("")
      : `<tr><td colspan="6" class="table-empty">${all.length ? `没有匹配的${partial ? "已知" : ""}进程，请调整搜索条件或用户筛选。` : unreadable ? coverageHint : "当前暂无可读取的 GPU 进程。"}</td></tr>`;
    $("#process-footer").textContent = unreadable
      ? "进程信息不可读取"
      : `${partial ? "已知" : "共"} ${new Set(rows.map((p) => `${p.host}:${p.pid}`)).size} 个进程 · ${rows.length} 条 GPU 关联${rows.length ? ` · 第 ${start + 1}–${end} 条` : ""}${partial ? "（部分可读）" : ""}`;
    const pageInfo = `${pages ? state.processPage : 0} / ${pages}`;
    if ($("#process-page-info").textContent !== pageInfo)
      $("#process-page-info").textContent = pageInfo;
    for (const [id, page] of [
      ["first", 1],
      ["previous", Math.max(1, state.processPage - 1)],
      ["next", Math.min(last, state.processPage + 1)],
      ["last", last],
    ] as const) {
      const button = $<HTMLButtonElement>(`#process-${id}`);
      button.dataset.processPage = String(page);
      button.disabled = !rows.length || page === state.processPage;
    }
    $<HTMLSelectElement>("#process-page-size").value = String(
      state.processPageSize,
    );
  }
  return { renderProcesses };
}
