"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const finite = value => typeof value === "number" && Number.isFinite(value);
const num = (value, digits = 0) => finite(value) ? value.toLocaleString("zh-CN", {maximumFractionDigits: digits, minimumFractionDigits: digits}) : "—";
const pct = (value, digits = 0) => finite(value) ? `${num(value, digits)}%` : "—";
const clamp = value => finite(value) ? Math.min(100, Math.max(0, value)) : 0;
const mean = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
const sum = (values, key) => values.reduce((total, value) => total + (finite(value[key]) ? value[key] : 0), 0);
const available = host => ["online", "warning"].includes(host.status);
const statusLabels = {online:"正常", warning:"需关注", offline:"离线", pending:"待上报"};
const statusBadge = status => `<span class="status-badge ${escapeHTML(status)}">${statusLabels[status] || "未知"}</span>`;
const dateTime = ts => finite(ts) ? new Date(ts * 1000).toLocaleString("zh-CN", {hour12:false}) : "暂无记录";
const shortTime = ts => finite(ts) ? new Date(ts * 1000).toLocaleTimeString("zh-CN", {hour12:false}) : "—";
const dateInput = date => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
const initials = user => String(user || "?").slice(0, 2).toUpperCase();
function bytes(value, digits = 1) {
  if (!finite(value)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {value /= 1024; index++;}
  return `${num(value, index ? digits : 0)} ${units[index]}`;
}
const gpuMemory = mb => bytes(finite(mb) ? mb * 1024 ** 2 : null);
function uptime(seconds) {
  if (!finite(seconds)) return "—";
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600);
  return days ? `${days} 天 ${hours} 小时` : `${hours} 小时 ${Math.floor(seconds % 3600 / 60)} 分钟`;
}
function ago(ts) {
  if (!finite(ts)) return "暂无成功采集记录";
  const seconds = Math.max(0, Math.floor(nowSeconds() - ts));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}
function meter(value, extra = "") {
  return `<div class="resource-meter ${extra}" role="meter" aria-label="使用率" aria-valuemin="0" aria-valuemax="100" ${finite(value) ? `aria-valuenow="${clamp(value)}"` : 'aria-valuetext="无数据"'}><span style="width:${clamp(value)}%"></span></div>`;
}
function resource(label, value) {
  return `<div><div class="resource-label"><span>${label}</span><strong>${pct(value)}</strong></div>${meter(value, value >= 90 ? "amber" : "")}</div>`;
}
const PIN_STORAGE_KEY = "hqlab-monitor-pinned-hosts";
function readPinnedHosts() {
  try {
    const saved = JSON.parse(localStorage.getItem(PIN_STORAGE_KEY) || "[]");
    return Array.isArray(saved) ? [...new Set(saved.filter(name => typeof name === "string" && name.length > 0 && name.length <= 255))] : [];
  } catch {return [];}
}
const TOKEN_STORAGE_KEY = "hqlab-monitor-token";
// Non-secret change marker, not a session ID. Even an empty -> empty logout must
// invalidate pending legacy migrations before the browser delivers storage events.
// A session fallback records the same marker so a later logout also wins on reload.
const AUTH_REVISION_KEY = "hqlab-monitor-auth-revision";
function readSharedAuth() {
  try {return {token:localStorage.getItem(TOKEN_STORAGE_KEY) || "", revision:localStorage.getItem(AUTH_REVISION_KEY) || ""};}
  catch {return null;}
}
let sharedAuth = readSharedAuth(), authVersion = 0, legacyToken = "";
function discardSessionToken() {
  try {sessionStorage.removeItem(TOKEN_STORAGE_KEY); sessionStorage.removeItem(AUTH_REVISION_KEY);} catch { /* Storage may be disabled. */ }
}
function readToken() {
  if (sharedAuth?.token) {discardSessionToken(); return sharedAuth.token;}
  try {
    const token = sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
    if (sharedAuth?.revision && sessionStorage.getItem(AUTH_REVISION_KEY) !== sharedAuth.revision) {
      discardSessionToken(); return "";
    }
    legacyToken = token; return token;
  } catch {return "";}
}
function writeAuthRevision() {
  const revision = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try {localStorage.setItem(AUTH_REVISION_KEY, revision); return revision;}
  catch {return sharedAuth?.revision || "";}
}
// Only an explicit validated login or the one-time legacy migration calls this.
function saveToken(token) {
  if (!token) return true;
  try {
    // Without readable shared state we cannot safely overwrite another tab's login.
    if (!sharedAuth) throw new Error("Persistent storage unavailable");
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    try {
      sessionStorage.setItem(AUTH_REVISION_KEY, sharedAuth?.revision || "");
      sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
    } catch { /* In-memory authentication still works. */ }
    return false;
  }
  sharedAuth = {token, revision:writeAuthRevision()};
  discardSessionToken();
  return true;
}
function clearToken(broadcast = true) {
  state.token = ""; authVersion++; legacyToken = "";
  if (broadcast) {
    const revision = writeAuthRevision();
    try {localStorage.removeItem(TOKEN_STORAGE_KEY);} catch { /* Storage may be disabled. */ }
    sharedAuth = {token:"", revision};
  }
  discardSessionToken();
  $("#logout-button").hidden = true;
}
function syncSharedAuth() {
  const current = readSharedAuth();
  if (!current) return false;
  if (current.token === (sharedAuth?.token || "") && current.revision === (sharedAuth?.revision || "")) {
    sharedAuth = current; return false;
  }
  sharedAuth = current; authVersion++; legacyToken = ""; state.token = current.token;
  discardSessionToken();
  location.reload();
  return true;
}
const authSnapshot = () => ({token:state.token, version:authVersion});
function checkAuth(auth) {
  // Inspect shared storage, not event.newValue: events can be delayed or obsolete.
  if (syncSharedAuth() || auth.token !== state.token || auth.version !== authVersion) throw new AuthError("登录状态已变更");
}
function migrateLegacyToken(auth) {
  if (!legacyToken || legacyToken !== auth.token) return;
  checkAuth(auth);
  legacyToken = ""; // One validated attempt; a failed write stays session/in-memory.
  saveToken(auth.token);
}
const state = {overview:null, history:null, hours:1, custom:null, metric:"compute", historyHost:"", search:"", status:"all", processSearch:"", user:"", processPage:1, processPageSize:25, detail:null, detailGpu:"", pinned:readPinnedHosts(), token:readToken(), connected:false, lastSuccess:0, clockOffset:0, refreshing:false, historyRequest:0, historyCycle:null};
const nowSeconds = () => Date.now() / 1000 + state.clockOffset;
class AuthError extends Error {}
function requireAuth() {
  if ($("#node-dialog").open) $("#node-dialog").close();
  if (!$("#auth-dialog").open) $("#auth-dialog").showModal();
}
async function request(path, auth = authSnapshot()) {
  checkAuth(auth);
  const response = await fetch(path, {headers: auth.token ? {Authorization:`Bearer ${auth.token}`} : {}, signal:AbortSignal.timeout(20000), cache:"no-store"});
  checkAuth(auth);
  if (response.status === 401) {clearToken(!!auth.token); requireAuth(); throw new AuthError("需要访问令牌");}
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {message = (await response.json()).error || message;} catch { /* Non-JSON gateway errors. */ }
    throw new Error(message);
  }
  return response;
}
const api = async path => {
  const auth = authSnapshot(), data = await (await request(path, auth)).json();
  checkAuth(auth);
  return data;
};
let toastTimeout;
function toast(message) {
  clearTimeout(toastTimeout); $("#toast").textContent = message; $("#toast").hidden = false;
  toastTimeout = setTimeout(() => {$("#toast").hidden = true;}, 3500);
}
function historyParams(useDisplayed = false) {
  const end = state.custom ? state.custom.end : nowSeconds();
  const start = state.custom ? state.custom.start : end - state.hours * 3600;
  const range = useDisplayed && state.history ? state.history : {start, end};
  return new URLSearchParams({start:range.start, end:range.end, ...(state.historyHost ? {host:state.historyHost} : {})});
}
async function refreshOverview(forceHistory = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  $("#refresh-button").disabled = true;
  $("#refresh-button .icon").classList.add("rotating");
  const auth = authSnapshot();
  try {
    const data = await api("/api/overview");
    checkAuth(auth);
    migrateLegacyToken(auth);
    state.overview = data; state.connected = true; state.lastSuccess = Date.now();
    state.clockOffset = data.server_time - Date.now() / 1000;
    $("#connection-error").hidden = true;
    renderOverview();
    if (forceHistory || !state.history || state.historyCycle !== data.last_cycle) {
      state.historyCycle = data.last_cycle;
      await loadHistory();
    }
  } catch (error) {
    if (!(error instanceof AuthError)) {
      state.connected = false;
      $("#connection-error").textContent = `监控服务连接中断：${error.message}。页面保留最后一次数据，请检查服务后重试。`;
      $("#connection-error").hidden = false;
    }
  } finally {
    state.refreshing = false; $("#refresh-button").disabled = false;
    $("#refresh-button .icon").classList.remove("rotating"); updateFreshness(); restoreWorkspaceAnchor();
  }
}
async function loadHistory() {
  const version = ++state.historyRequest;
  $("#history-error").hidden = true; $("#export-button").disabled = true;
  $("#chart-container").setAttribute("aria-busy", "true");
  try {
    const history = await api(`/api/history?${historyParams()}`);
    if (version !== state.historyRequest) return;
    state.history = history; renderHistory();
  } catch (error) {
    if (version !== state.historyRequest || error instanceof AuthError) return;
    state.history = null;
    $("#history-error").textContent = `历史数据读取失败：${error.message}`;
    $("#history-error").hidden = false;
    renderHistory();
  } finally {
    if (version === state.historyRequest) {
      $("#export-button").disabled = !state.history;
      $("#chart-container").setAttribute("aria-busy", "false");
    }
  }
}
function allProcesses() {
  return (state.overview?.hosts || []).filter(available).flatMap(host => host.gpus.flatMap(gpu => gpu.processes.map(process => ({...process, host:host.name, address:host.address, gpu:gpu.index, capacity:gpu.memory_total_mb}))));
}
function renderOverview() {
  const data = state.overview;
  $("#logout-button").hidden = !state.token;
  $("#demo-banner").hidden = !data.demo;
  $("#sidebar-source").textContent = data.demo ? "演示数据 · 本地模拟" : "已连接监控服务";
  $("#nav-host-count").textContent = data.hosts.length;
  $("#host-count").textContent = data.hosts.length;
  $("#retention-label").textContent = ` 历史保留 ${data.retention_days} 天。`;
  const hostOptions = JSON.stringify(data.hosts.map(h => h.name));
  if ($("#history-host").dataset.hosts !== hostOptions) {
    $("#history-host").innerHTML = `<option value="">全部服务器</option>${data.hosts.map(h => `<option value="${escapeHTML(h.name)}">${escapeHTML(h.name)}</option>`).join("")}`;
    $("#history-host").value = state.historyHost; $("#history-host").dataset.hosts = hostOptions;
  }
  renderSummary(); renderNodes(); renderPinnedNodes(); renderProcesses();
  if ($("#node-dialog").open && state.detail) renderDetail();
  updateFreshness();
}
function renderSummary() {
  const hosts = state.overview.hosts, online = hosts.filter(available), gpus = online.flatMap(h => h.gpus);
  const measured = gpus.map(g => g.utilization).filter(finite);
  const mem = gpus.filter(g => finite(g.memory_used_mb) && g.memory_total_mb > 0);
  const used = sum(mem, "memory_used_mb"), total = sum(mem, "memory_total_mb");
  const processes = allProcesses(), users = new Set(processes.map(p => p.user).filter(Boolean));
  const uniqueProcesses = new Set(processes.map(p => `${p.host}:${p.pid}`)).size;
  const normal = hosts.filter(h => h.status === "online").length, warnings = hosts.filter(h => h.status === "warning").length;
  const offline = hosts.filter(h => h.status === "offline").length, pending = hosts.filter(h => h.status === "pending").length;
  const avg = mean(measured), busy = measured.filter(v => v >= 10).length;
  const card = (title, symbol, value, foot, bar = null) => `<article class="summary-card"><div class="summary-top"><span>${title}</span><span class="summary-icon">${icon(symbol)}</span></div><div class="summary-value">${value}</div>${bar !== null ? `<div class="mini-bar"><span style="width:${clamp(bar)}%"></span></div>` : ""}<div class="summary-foot">${foot}</div></article>`;
  $("#summary").innerHTML = [
    card("在线服务器", "server", `${online.length}<small>/ ${hosts.length} 台</small>`, `<span class="tiny-status"></span>${normal} 正常${warnings ? ` · ${warnings} 需关注` : ""}<span class="tiny-status red"></span>${offline} 离线${pending ? ` · ${pending} 待上报` : ""}`),
    card("GPU 平均使用率", "chip", `${num(avg, 1)}<span class="unit">%</span>`, `<span class="good">${busy} 张繁忙</span><span> / ${gpus.length} 张在线 GPU</span>`, avg),
    card("GPU 显存使用率", "memory", `${total ? num(used / total * 100, 1) : "—"}<span class="unit">%</span>`, `${total ? `${num(used / 1024, 1)} / ${num(total / 1024, 0)} GiB 已使用` : "等待有效显存采样"}`, total ? used / total * 100 : null),
    card("活跃 GPU 用户", "users", `${state.overview.hosts.some(available) ? users.size : "—"}<small>位</small>`, `${uniqueProcesses} 个进程 · ${processes.length} 条 GPU 关联${processes.some(p => !p.user) ? "（含未知用户）" : ""}`),
  ].join("");
}
function updateFreshness() {
  const data = state.overview, auto = $("#auto-refresh").checked;
  const latest = Math.max(0, ...(data?.hosts || []).map(h => h.checked_at || 0));
  const delayed = auto && state.connected && ((state.lastSuccess && Date.now() - state.lastSuccess > 60000) || (latest && nowSeconds() - latest > Math.max(60, data.interval * 2)));
  const badge = $("#live-badge");
  badge.classList.toggle("disconnected", !state.connected); badge.classList.toggle("paused", state.connected && (!auto || !!delayed));
  badge.innerHTML = `<span class="live-dot"></span>${!state.connected ? "连接中断" : !auto ? "已暂停刷新" : delayed ? "数据待更新" : data?.collecting ? "采集中" : "实时监控"}`;
  if (!data) return;
  $(".sidebar-status").classList.toggle("disconnected", !state.connected);
  $("#sidebar-source").textContent = !state.connected ? "监控连接已中断" : data.demo ? "演示数据 · 本地模拟" : "已连接监控服务";
  $("#update-label").textContent = latest ? `最近更新 ${shortTime(latest)} · ${ago(latest)}` : "正在等待节点采集脚本首次上报…";
  $("#collection-label").textContent = `${data.mode === "push" ? `节点约每 ${data.interval} 秒上报 · ${data.stale_after} 秒无上报视为离线` : `每 ${data.interval} 秒模拟采集`}${!auto ? " · 页面自动刷新已暂停" : ""}`;
}
function renderNodes() {
  if (!state.overview) return;
  const focused = document.activeElement?.closest("#node-grid [data-pin-node]")?.dataset.pinNode;
  const query = state.search.toLowerCase();
  const hosts = state.overview.hosts.filter(h => (state.status === "all" || h.status === state.status) && `${h.name} ${h.address} ${h.gpus.map(g => g.name).join(" ")}`.toLowerCase().includes(query));
  $("#nodes-empty").hidden = !!hosts.length;
  $("#node-grid").innerHTML = hosts.map(nodeCard).join("");
  if (focused) $$("#node-grid [data-pin-node]").find(button => button.dataset.pinNode === focused)?.focus({preventScroll:true});
  $("#host-count").textContent = hosts.length === state.overview.hosts.length ? hosts.length : `${hosts.length} / ${state.overview.hosts.length}`;
}
function pinButton(name, labeled = false) {
  const pinned = state.pinned.includes(name);
  return `<button type="button" class="pin-button${labeled ? " pin-button-labeled" : ""}" data-pin-node="${escapeHTML(name)}" aria-pressed="${pinned}" aria-label="置顶服务器 ${escapeHTML(name)}" title="${pinned ? "取消置顶" : "置顶关注"} ${escapeHTML(name)}">${icon("pin")}${labeled ? `<span class="pin-label">${pinned ? "已置顶" : "置顶关注"}</span>` : ""}</button>`;
}
function renderPinnedNodes() {
  if (!state.overview) return;
  const focused = document.activeElement?.closest("#pinned-grid [data-pin-node]")?.dataset.pinNode;
  const managed = new Map(state.overview.hosts.map(host => [host.name, host]));
  const hosts = state.pinned.map(name => managed.get(name)).filter(Boolean);
  $("#pinned-servers").hidden = false;
  $("#pinned-servers").classList.toggle("is-empty", !hosts.length);
  $("#pinned-count").textContent = hosts.length;
  $("#pinned-hint").hidden = !!hosts.length;
  $("#pinned-hint").textContent = hosts.length ? "" : "点击服务器卡片上的图钉置顶。";
  $("#pinned-grid").hidden = !hosts.length;
  $("#pinned-grid").innerHTML = hosts.map(nodeCard).join("");
  if (focused) {
    const next = $$("#pinned-grid [data-pin-node]").find(button => button.dataset.pinNode === focused);
    (next || $("#pinned-grid [data-pin-node]") || $("#choose-pinned")).focus({preventScroll:true});
  }
}
function syncPinButtons() {
  for (const button of $$("[data-pin-node]")) {
    const name = button.dataset.pinNode, pinned = state.pinned.includes(name);
    button.setAttribute("aria-pressed", String(pinned));
    button.title = `${pinned ? "取消置顶" : "置顶关注"} ${name}`;
    const label = $(".pin-label", button);
    if (label) label.textContent = pinned ? "已置顶" : "置顶关注";
    button.closest(".node-card")?.classList.toggle("is-pinned", pinned);
  }
}
function togglePinned(name) {
  if (!state.overview?.hosts.some(host => host.name === name)) return;
  const pinning = !state.pinned.includes(name);
  state.pinned = pinning ? [...state.pinned, name] : state.pinned.filter(value => value !== name);
  let saved = true;
  try {localStorage.setItem(PIN_STORAGE_KEY, JSON.stringify(state.pinned));} catch {saved = false;}
  renderPinnedNodes(); syncPinButtons();
  toast(`${name} ${pinning ? "已置顶到顶部重点关注区" : "已取消置顶"}${saved ? "" : "（浏览器无法保存设置，仅本标签页生效）"}`);
}
function nodeCard(host) {
  const good = available(host), system = good ? host.system : {}, gpus = good ? host.gpus : [];
  const processes = gpus.flatMap(g => g.processes), users = [...new Set(processes.map(p => p.user).filter(Boolean))];
  const processCount = new Set(processes.map(p => p.pid)).size;
  const maxDisk = system.disks?.length ? Math.max(...system.disks.map(d => d.percent)) : null;
  const model = [...new Set(gpus.map(g => g.name))].join(" / ");
  return `<article class="node-card ${good ? "" : "offline"}${state.pinned.includes(host.name) ? " is-pinned" : ""}" data-node="${escapeHTML(host.name)}">
    <div class="node-header"><div class="node-identity"><span class="node-icon">${icon("server")}</span><div><button class="node-name" data-open-node="${escapeHTML(host.name)}">${escapeHTML(host.name)}</button><div class="node-address mono">${escapeHTML(host.address)}</div></div></div><div class="node-actions">${statusBadge(host.status)}${pinButton(host.name)}</div></div>
    ${good ? `<div class="node-model">${icon("chip")}<span>${escapeHTML(model || "无 GPU / 未检测到 nvidia-smi")}</span>${gpus.length ? `<span>× ${gpus.length}</span>` : ""}</div><div class="gpu-grid" style="--gpu-columns:${Math.min(gpus.length || 1, 8)};--gpu-compact-columns:${Math.min(gpus.length || 1, 4)}">${gpus.map(g => `<button class="gpu-tile ${g.temperature >= 85 ? "hot" : g.utilization >= 10 ? "busy" : "idle"}" data-open-node="${escapeHTML(host.name)}" data-gpu="${g.index}" title="GPU ${g.index} · 使用率 ${pct(g.utilization)} · 显存 ${gpuMemory(g.memory_used_mb)} / ${gpuMemory(g.memory_total_mb)} · ${num(g.temperature)}°C"><span class="gpu-index">GPU ${g.index}</span><span class="gpu-util">${num(g.utilization)}<small>${finite(g.utilization) ? "%" : ""}</small></span><span class="gpu-memory">${finite(g.memory_used_mb) ? num(g.memory_used_mb / 1024, 1) : "—"}G</span></button>`).join("")}</div>${!gpus.length ? '<div class="node-offline-state"><p>CPU 节点或 GPU 指标暂不可读取</p></div>' : ""}` : `<div class="node-offline-state">${icon(host.status === "pending" ? "clock" : "server")}<div><strong>${host.status === "pending" ? "等待节点采集脚本上报" : "节点离线或采集上报失败"}</strong><p title="${escapeHTML(host.error || "")}">${host.status === "pending" ? "收到本机采集脚本数据后自动更新" : escapeHTML(host.error || "节点暂无响应")}</p></div></div>`}
    <div class="node-resources">${resource("CPU", system.cpu_percent)}${resource("内存", system.memory_percent)}${resource("磁盘最高", maxDisk)}</div>
    ${host.warnings?.length ? `<div class="node-warning">${icon("alert")}<span>${escapeHTML(host.warnings[0])}${host.warnings.length > 1 ? ` · 另有 ${host.warnings.length - 1} 项提醒` : ""}</span></div>` : ""}
    <div class="node-footer"><div class="node-users">${good ? `<div class="mini-avatars">${users.slice(0, 3).map(u => `<span class="mini-avatar" title="${escapeHTML(u)}">${escapeHTML(initials(u))}</span>`).join("")}</div><span>${processCount} 个进程 · ${users.length} 位用户</span>` : `<span>${host.status === "pending" ? "等待采集" : `最近在线：${ago(host.last_seen)}`}</span>`}</div><button class="detail-link" data-open-node="${escapeHTML(host.name)}">查看详情 ${icon("arrow")}</button></div>
  </article>`;
}
function renderProcesses() {
  const all = allProcesses(), users = new Map();
  for (const p of all) {if (p.user) users.set(p.user, (users.get(p.user) || 0) + (p.memory_mb || 0));}
  const query = state.processSearch.toLowerCase();
  const rows = all.filter(p => (!state.user || p.user === state.user) && `${p.user || "未知"} ${p.host} ${p.address} ${p.pid} ${p.name} GPU${p.gpu}`.toLowerCase().includes(query)).sort((a, b) => (b.memory_mb || 0) - (a.memory_mb || 0));
  $("#process-count").textContent = new Set(all.map(p => `${p.host}:${p.pid}`)).size;
  const selectedUsers = [...users].sort((a, b) => b[1] - a[1]).slice(0, 12);
  $("#user-strip").innerHTML = `<button class="user-pill ${!state.user ? "selected" : ""}" data-user="">${icon("users")}全部用户 <b>${users.size}</b></button>${selectedUsers.map(([user, mb]) => `<button class="user-pill ${state.user === user ? "selected" : ""}" data-user="${escapeHTML(user)}">${escapeHTML(user)} <b>${num(mb / 1024, 1)}G</b></button>`).join("")}${users.size > 12 ? '<span class="muted"><small>其余用户可通过搜索查看</small></span>' : ""}`;
  const pages = Math.ceil(rows.length / state.processPageSize), last = Math.max(1, pages);
  state.processPage = Math.min(last, Math.max(1, state.processPage));
  const start = (state.processPage - 1) * state.processPageSize, end = Math.min(start + state.processPageSize, rows.length);
  $("#process-rows").innerHTML = rows.length ? rows.slice(start, end).map(p => processRow(p)).join("") : `<tr><td colspan="6" class="table-empty">${all.length ? "没有匹配的进程，请调整搜索条件或用户筛选。" : "当前暂无可读取的 GPU 进程。"}</td></tr>`;
  $("#process-footer").textContent = `共 ${new Set(rows.map(p => `${p.host}:${p.pid}`)).size} 个进程 · ${rows.length} 条 GPU 关联${rows.length ? ` · 第 ${start + 1}–${end} 条` : ""}`;
  const pageInfo = `${pages ? state.processPage : 0} / ${pages}`;
  if ($("#process-page-info").textContent !== pageInfo) $("#process-page-info").textContent = pageInfo;
  for (const [id, page] of [["first", 1], ["previous", Math.max(1, state.processPage - 1)], ["next", Math.min(last, state.processPage + 1)], ["last", last]]) {
    const button = $(`#process-${id}`);
    button.dataset.processPage = page;
    button.disabled = !rows.length || page === state.processPage;
  }
  $("#process-page-size").value = String(state.processPageSize);
}
function processRow(p, compact = false) {
  const user = p.user || "未知 / 无权限";
  return `<tr><td><span class="user-cell"><span class="user-avatar">${escapeHTML(initials(p.user))}</span><span title="${p.user ? escapeHTML(p.user) : "用户不可读取、进程已退出或容器 PID 不可见"}">${escapeHTML(user)}</span></span></td><td>${compact ? `<span class="mono">GPU ${p.gpu}</span>` : `<button class="table-link mono" data-open-node="${escapeHTML(p.host)}">${escapeHTML(p.host)}</button><span class="muted"> / GPU ${p.gpu}</span>`}</td><td class="mono">${p.pid}</td><td><span class="process-name mono" title="${escapeHTML(p.name)}">${escapeHTML(p.name.split("/").filter(Boolean).pop() || p.name)}</span></td><td><span class="type-tag">${escapeHTML(p.type || "—")}</span></td><td><div class="memory-cell"><span class="mono">${gpuMemory(p.memory_mb)}</span>${meter(finite(p.memory_mb) && p.capacity ? p.memory_mb / p.capacity * 100 : null)}</div></td></tr>`;
}
function renderHistory() {
  const h = state.history, a = h?.aggregate || {};
  const memory = state.metric === "memory";
  $("#primary-legend").textContent = memory ? "GPU 显存" : "GPU";
  $("#secondary-legend").textContent = memory ? "系统内存" : "CPU";
  $("#period-stats").innerHTML = `<div><div class="period-stat-label">时段 ${memory ? "显存" : "GPU"} 均值</div><div class="period-stat-value">${num(memory ? a.avg_gpu_memory : a.avg_gpu, 1)}<small>%</small></div><div class="period-stat-note">${memory ? "按有效显存容量加权" : `单卡峰值 ${pct(a.peak_gpu)}`}</div></div><div><div class="period-stat-label">时段 ${memory ? "内存" : "CPU"} 均值</div><div class="period-stat-value">${num(memory ? a.avg_memory : a.avg_cpu, 1)}<small>%</small></div><div class="period-stat-note">按节点有效样本平均</div></div><div><div class="period-stat-label">繁忙 GPU 卡时</div><div class="period-stat-value">${h?.aggregate.samples ? num(a.busy_gpu_hours, 1) : "—"}<small>GPU·h</small></div><div class="period-stat-note">采集覆盖率 ${pct(a.coverage, 1)}</div></div>`;
  $("#range-label").textContent = h ? `${dateTime(h.start)} — ${dateTime(h.end)}` : "";
  $("#usage-rows").innerHTML = h?.stats?.length ? h.stats.map(row => `<tr><td><button class="table-link mono" data-open-node="${escapeHTML(row.host)}">${escapeHTML(row.host)}</button></td><td class="mono">${pct(row.avg_gpu, 1)} / ${pct(row.peak_gpu)}</td><td class="mono">${pct(row.avg_cpu, 1)}</td><td class="mono">${pct(row.avg_gpu_memory, 1)}</td><td class="mono">${row.samples ? num(row.busy_gpu_hours, 2) : "—"}</td><td class="mono">${pct(row.availability, 1)}</td><td class="mono ${row.coverage < 50 ? "coverage-low" : ""}">${pct(row.coverage, 1)}</td></tr>`).join("") : '<tr><td colspan="7" class="table-empty">暂无历史统计，数据会在采集后逐步累积。</td></tr>';
  renderChart();
}
function renderChart() {
  const container = $("#chart-container"), h = state.history;
  const keys = state.metric === "memory" ? ["gpu_memory", "memory"] : ["gpu", "cpu"];
  if (!h?.points.some(p => keys.some(k => finite(p[k])))) {
    container.innerHTML = `<div class="chart-empty">${icon("chart")}<span>此时间范围暂无有效采样</span><small>历史从监控服务启动后累积，采集缺失不表示使用率为 0。</small></div>`;
    return;
  }
  const w = Math.max(240, Math.round(container.clientWidth)), height = Math.round(container.clientHeight);
  const pad = {left:46, right:9, top:16, bottom:32}, width = w - pad.left - pad.right, chartHeight = height - pad.top - pad.bottom;
  const x = ts => pad.left + (ts - h.start) / (h.end - h.start) * width;
  const y = value => pad.top + chartHeight * (1 - clamp(value) / 100);
  function segments(key) {
    const output = []; let current = [];
    for (const p of h.points) {
      if (finite(p[key])) current.push([x(p.ts), y(p[key])]);
      else if (current.length) {output.push(current); current = [];}
    }
    if (current.length) output.push(current);
    return output;
  }
  const path = points => points.map(([a, b], i) => `${i ? "L" : "M"}${a.toFixed(2)},${b.toFixed(2)}`).join(" ");
  const primary = segments(keys[0]), secondary = segments(keys[1]);
  const longRange = h.end - h.start > 36 * 3600;
  function tick(ts) {
    const date = new Date(ts * 1000);
    return longRange ? date.toLocaleDateString("zh-CN", {month:"2-digit", day:"2-digit"}) : date.toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", hour12:false});
  }
  container.innerHTML = `<svg class="trend-svg" viewBox="0 0 ${w} ${height}" role="img" aria-label="${state.metric === "memory" ? "GPU 显存与系统内存" : "GPU 与 CPU"}使用率趋势，时间范围 ${escapeHTML(dateTime(h.start))} 至 ${escapeHTML(dateTime(h.end))}"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop class="chart-fill-start" offset="0%"/><stop class="chart-fill-end" offset="100%"/></linearGradient></defs>${[0, 25, 50, 75, 100].map(v => `<line class="chart-grid" x1="${pad.left}" x2="${w - pad.right}" y1="${y(v)}" y2="${y(v)}"/><text class="chart-axis" text-anchor="end" x="${pad.left - 10}" y="${y(v) + 3}">${v}%</text>`).join("")}${[0, 1, 2, 3, 4].map(i => {const ts = h.start + (h.end - h.start) * i / 4; return `<text class="chart-axis" text-anchor="${i === 0 ? "start" : i === 4 ? "end" : "middle"}" x="${x(ts)}" y="${height - 5}">${tick(ts)}</text>`;}).join("")}${primary.map(p => `<path fill="url(#chart-fill)" d="${path(p)} L${p.at(-1)[0]},${y(0)} L${p[0][0]},${y(0)} Z"/>`).join("")}${secondary.map(p => `<path class="chart-secondary" d="${path(p)}"/>`).join("")}${primary.map(p => `<path class="chart-primary" d="${path(p)}"/>${p.length === 1 ? `<circle class="chart-pointer" cx="${p[0][0]}" cy="${p[0][1]}" r="3"/>` : ""}`).join("")}<g id="chart-hover" visibility="hidden"><line id="chart-hover-line" class="chart-hover-line" y1="${pad.top}" y2="${y(0)}"/><circle id="chart-hover-dot" class="chart-pointer" r="4"/></g><rect id="chart-hit" x="${pad.left}" y="${pad.top}" width="${width}" height="${chartHeight}" fill="transparent"/></svg><div class="chart-tooltip" hidden></div>`;
  const tooltip = $(".chart-tooltip", container), hover = $("#chart-hover", container);
  $("#chart-hit", container).addEventListener("pointermove", event => {
    const localX = event.clientX - container.getBoundingClientRect().left;
    const index = Math.max(0, Math.min(h.points.length - 1, Math.floor((localX - pad.left) / width * h.points.length)));
    const point = h.points[index], pointX = x(point.ts);
    hover.setAttribute("visibility", "visible");
    $("#chart-hover-line", container).setAttribute("x1", pointX); $("#chart-hover-line", container).setAttribute("x2", pointX);
    const dot = $("#chart-hover-dot", container); dot.setAttribute("cx", pointX); dot.setAttribute("cy", y(point[keys[0]])); dot.setAttribute("visibility", finite(point[keys[0]]) ? "visible" : "hidden");
    tooltip.innerHTML = `<strong>${escapeHTML(dateTime(point.ts))} · ${h.bucket_seconds}s 均值</strong>${keys.map((key, i) => `<div class="tooltip-row"><span><i class="legend-dot ${i ? "gray" : "green"}"></i>${escapeHTML(i ? $("#secondary-legend").textContent : $("#primary-legend").textContent)}</span><b>${pct(point[key], 1)}</b></div>`).join("")}`;
    tooltip.hidden = false; tooltip.style.left = `${Math.max(0, Math.min(w - tooltip.offsetWidth, pointX + 12))}px`;
  });
  $("#chart-hit", container).addEventListener("pointerleave", () => {hover.setAttribute("visibility", "hidden"); tooltip.hidden = true;});
}
function openDetail(name, gpu = "") {
  if (!state.overview?.hosts.some(h => h.name === name)) return;
  state.detail = name; state.detailGpu = String(gpu); renderDetail();
  if (!$("#node-dialog").open) $("#node-dialog").showModal();
  $("#node-dialog").scrollTop = 0;
}
function renderDetail() {
  const host = state.overview.hosts.find(h => h.name === state.detail);
  if (!host) return;
  const focused = document.activeElement;
  const focusedPin = focused?.closest("#node-detail [data-pin-node]")?.dataset.pinNode;
  const focusedFilter = focused?.id === "detail-gpu-filter";
  const system = host.system || {}, good = available(host), scroll = $("#node-dialog").scrollTop;
  const processes = host.gpus.flatMap(g => g.processes.map(p => ({...p, host:host.name, gpu:g.index, capacity:g.memory_total_mb}))).filter(p => state.detailGpu === "" || String(p.gpu) === state.detailGpu);
  const fact = (label, value) => `<div><dt>${label}</dt><dd>${escapeHTML(value)}</dd></div>`;
  const systemStat = (label, value, sub) => `<div class="detail-system-stat"><span class="label">${label}</span><strong>${value}</strong><small>${sub}</small></div>`;
  $("#node-detail").innerHTML = `<div class="detail-heading"><div class="detail-title-row"><h2 id="detail-title">${escapeHTML(host.name)} ${statusBadge(host.status)}</h2>${pinButton(host.name, true)}</div><p><span class="mono">${escapeHTML(host.address)}</span> · ${escapeHTML(host.groups.join(" / "))}</p></div>
  ${host.warnings?.length ? `<div class="detail-warnings">${host.warnings.map(w => `<p>${escapeHTML(w)}</p>`).join("")}</div>` : ""}
  ${!good ? `<div class="detail-offline"><h3>${host.status === "pending" ? "正在等待采集" : "节点当前离线"}</h3><p>${escapeHTML(host.error || "采集结果将在连接成功后自动显示。")}</p><p>最近成功采集：${dateTime(host.last_seen)}</p><p>不会将离线节点的历史指标作为当前状态展示。</p></div>` : `<section class="detail-section"><h3>${icon("server")}系统运行状态</h3><div class="detail-system-grid">${systemStat("CPU 使用率", pct(system.cpu_percent, 1), `${num(system.cpu_count)} 逻辑核心`)}${systemStat("内存使用率", pct(system.memory_percent, 1), `${bytes(system.memory_used_bytes)} / ${bytes(system.memory_total_bytes)}`)}${systemStat("运行时间", `${Math.floor((system.uptime_seconds || 0) / 86400)} <small>天</small>`, uptime(system.uptime_seconds))}${systemStat("网络接收 / 秒", bytes(system.network_rx_bytes_s), `发送 ${bytes(system.network_tx_bytes_s)}/s`)}${systemStat("磁盘读取 / 秒", bytes(system.disk_read_bytes_s), `写入 ${bytes(system.disk_write_bytes_s)}/s`)}${systemStat("Swap 使用率", pct(system.swap_percent, 1), `${bytes(system.swap_used_bytes)} / ${bytes(system.swap_total_bytes)}`)}</div><dl class="detail-facts">${fact("操作系统", system.os || "—")}${fact("内核", system.kernel || "—")}${fact("系统负载 1/5/15m", (system.load || []).map(v => num(v, 2)).join(" / "))}${fact("采集耗时", `${num(host.collection_ms)} ms`)}${fact("最近更新", shortTime(host.checked_at))}${fact("GPU 驱动", host.gpus[0]?.driver || "—")}</dl></section>
  <section class="detail-section"><h3>${icon("chip")}GPU 逐卡状态 <span class="count-badge">${host.gpus.length}</span></h3>${host.gpus.length ? host.gpus.map(g => `<article class="detail-gpu"><div class="detail-gpu-header"><strong>GPU ${g.index} · ${escapeHTML(g.name)}</strong><small>${g.processes.length} 条进程记录</small></div><div class="detail-gpu-metrics"><div><label>计算使用率</label><b>${pct(g.utilization, 1)}</b>${meter(g.utilization)}</div><div><label>显存使用率</label><b>${pct(g.memory_percent, 1)}</b>${meter(g.memory_percent)}</div><div><label>温度 / 功耗</label><b>${num(g.temperature)}°C <small>/ ${num(g.power_w)} W</small></b><small>功率上限 ${num(g.power_limit_w)} W</small></div></div><div class="detail-gpu-info"><span>显存 ${gpuMemory(g.memory_used_mb)} / ${gpuMemory(g.memory_total_mb)}</span><span>风扇 ${pct(g.fan_percent)} · ${escapeHTML(g.uuid || "无 UUID")}</span></div></article>`).join("") : '<p class="muted">未检测到 GPU，或 GPU 指标不可读取。请检查此节点是否安装 NVIDIA 驱动及 nvidia-smi。</p>'}</section>
  <section class="detail-section"><h3>${icon("users")}GPU 进程 <select id="detail-gpu-filter" aria-label="按 GPU 筛选进程"><option value="">全部 GPU</option>${host.gpus.map(g => `<option value="${g.index}" ${String(g.index) === state.detailGpu ? "selected" : ""}>GPU ${g.index}</option>`).join("")}</select></h3><div class="table-scroll"><table class="data-table"><thead><tr><th>用户</th><th>GPU</th><th>PID</th><th>进程</th><th>类型</th><th>显存占用</th></tr></thead><tbody>${processes.length ? processes.map(p => processRow(p, true)).join("") : '<tr><td colspan="6" class="table-empty">暂无可读取的 GPU 进程</td></tr>'}</tbody></table></div></section>
  <section class="detail-section"><h3>${icon("memory")}磁盘与挂载点</h3><div class="table-scroll"><table class="data-table"><thead><tr><th>挂载点</th><th>使用率</th><th>已用 / 总计</th><th>可用</th></tr></thead><tbody>${(system.disks || []).map(d => `<tr><td class="mono">${escapeHTML(d.mount)}</td><td>${pct(d.percent, 1)}${meter(d.percent, d.percent >= 90 ? "amber" : "")}</td><td class="mono">${bytes(d.used_bytes)} / ${bytes(d.total_bytes)}</td><td class="mono">${bytes(d.available_bytes)}</td></tr>`).join("") || '<tr><td colspan="4" class="table-empty">磁盘数据暂不可读</td></tr>'}</tbody></table></div></section>`}
  <p class="detail-caption">进程类型：C = 计算 / G = 图形</p>`;
  const control = focusedFilter ? $("#detail-gpu-filter") : focusedPin ? $$("#node-detail [data-pin-node]").find(button => button.dataset.pinNode === focusedPin) : null;
  control?.focus({preventScroll:true});
  $("#node-dialog").scrollTop = scroll;
}

$("#refresh-button").addEventListener("click", () => refreshOverview(true));
$("#auto-refresh").addEventListener("change", () => {updateFreshness(); if ($("#auto-refresh").checked) refreshOverview(true);});
$("#server-search").addEventListener("input", event => {state.search = event.target.value.trim(); renderNodes();});
$("#status-filter").addEventListener("change", event => {state.status = event.target.value; renderNodes();});
$("#clear-filters").addEventListener("click", () => {state.search = ""; state.status = "all"; $("#server-search").value = ""; $("#status-filter").value = "all"; renderNodes();});
$("#process-search").addEventListener("input", event => {state.processSearch = event.target.value.trim(); state.processPage = 1; renderProcesses();});
$("#process-page-size").addEventListener("change", event => {
  const size = Number(event.target.value);
  if (![25, 50, 100].includes(size)) {event.target.value = String(state.processPageSize); return;}
  state.processPageSize = size; state.processPage = 1; renderProcesses();
});
$("#process-pagination").addEventListener("click", event => {
  const button = event.target.closest("[data-process-page]");
  if (!button || button.disabled) return;
  state.processPage = Number(button.dataset.processPage); renderProcesses();
});
$("#history-host").addEventListener("change", event => {state.historyHost = event.target.value; loadHistory();});
$("#range-buttons").addEventListener("click", event => {
  const button = event.target.closest("button[data-hours]"); if (!button) return;
  state.hours = Number(button.dataset.hours); state.custom = null;
  $$("#range-buttons button").forEach(b => {b.classList.toggle("selected", b === button); b.setAttribute("aria-pressed", String(b === button));});
  $("#custom-range").hidden = true; $("#custom-range-toggle").setAttribute("aria-expanded", "false"); loadHistory();
});
$("#custom-range-toggle").addEventListener("click", () => {
  const form = $("#custom-range"); form.hidden = !form.hidden;
  $("#custom-range-toggle").setAttribute("aria-expanded", String(!form.hidden));
  if (!form.hidden) {
    const now = new Date(nowSeconds() * 1000);
    $("#range-end").max = dateInput(now);
    if (!state.custom) {$("#range-start").value = dateInput(new Date(now.getTime() - 6 * 3600000)); $("#range-end").value = dateInput(now);}
    $("#range-start").focus();
  }
});
$("#custom-range").addEventListener("submit", event => {
  event.preventDefault();
  const start = new Date($("#range-start").value).getTime() / 1000, end = new Date($("#range-end").value).getTime() / 1000;
  if (!finite(start) || !finite(end) || start >= end || end - start > 31 * 86400 || end > nowSeconds() + 60) {toast("请选择有效时间范围：结束晚于开始，不超过 31 天，且不晚于当前时间。"); return;}
  state.custom = {start, end};
  $$("#range-buttons button").forEach(b => {const selected = b.id === "custom-range-toggle"; b.classList.toggle("selected", selected); b.setAttribute("aria-pressed", String(selected));});
  loadHistory();
});
$$("[data-metric]").forEach(button => button.addEventListener("click", () => {
  state.metric = button.dataset.metric;
  $$("[data-metric]").forEach(b => {b.classList.toggle("selected", b === button); b.setAttribute("aria-pressed", String(b === button));}); renderHistory();
}));
$("#export-button").addEventListener("click", async () => {
  const button = $("#export-button"); button.disabled = true;
  try {
    const response = await request(`/api/export.csv?${historyParams(true)}`), blob = await response.blob();
    const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = "hqlab-usage.csv";
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast("时段使用率统计已导出");
  } catch (error) {if (!(error instanceof AuthError)) toast(`导出失败：${error.message}`);}
  finally {button.disabled = !state.history;}
});
window.addEventListener("appearance-storage-error", () => toast("浏览器无法保存配色设置，仅本标签页生效"));
window.addEventListener("storage", event => {
  if (event.key !== PIN_STORAGE_KEY && event.key !== null) return;
  state.pinned = readPinnedHosts(); renderPinnedNodes(); syncPinButtons();
});
document.addEventListener("click", event => {
  const pin = event.target.closest("[data-pin-node]");
  if (pin) {togglePinned(pin.dataset.pinNode); return;}
  const opener = event.target.closest("[data-open-node]");
  if (opener) openDetail(opener.dataset.openNode, opener.dataset.gpu ?? "");
  const user = event.target.closest("[data-user]");
  if (user) {state.user = user.dataset.user; state.processPage = 1; renderProcesses();}
});
$("#node-dialog").addEventListener("change", event => {
  if (event.target.id === "detail-gpu-filter") {state.detailGpu = event.target.value; renderDetail();}
});
$("#close-detail").addEventListener("click", () => $("#node-dialog").close());
$("#node-dialog").addEventListener("click", event => {
  const rect = event.currentTarget.getBoundingClientRect();
  if (event.target === event.currentTarget && (event.clientX < rect.left || event.clientX > rect.right)) event.currentTarget.close();
});
$("#node-dialog").addEventListener("close", () => {state.detail = null;});
$("#logout-button").addEventListener("click", () => {clearToken(); location.reload();});
window.addEventListener("storage", event => {
  try {
    if (event.storageArea !== localStorage || ![TOKEN_STORAGE_KEY, AUTH_REVISION_KEY, null].includes(event.key)) return;
  } catch {return;}
  syncSharedAuth();
});
$("#auth-dialog").addEventListener("cancel", event => event.preventDefault());
$("#auth-form").addEventListener("submit", async event => {
  event.preventDefault(); const button = $("#auth-form button"); button.disabled = true;
  const token = $("#access-token").value.trim();
  state.token = token; authVersion++; legacyToken = "";
  const auth = authSnapshot(); $("#auth-error").hidden = true;
  try {
    await api("/api/overview");
    checkAuth(auth);
    if (!saveToken(token)) toast("浏览器无法持久保存令牌，当前会话仍可使用；重新打开可能需要再次登录。");
    $("#access-token").value = ""; $("#auth-dialog").close(); await refreshOverview(true);
  } catch (error) {
    $("#auth-error").textContent = error instanceof AuthError ? "令牌无效，请重新输入。" : `无法连接服务：${error.message}`;
    $("#auth-error").hidden = false;
  } finally {button.disabled = false;}
});
// Keep native fragment links (including keyboard navigation and Back/Forward).
// The CSS offset also accounts for the sticky navigation bar on narrow screens.
const workspaceLinks = $$(".sidebar [data-nav]").map(link => ({link, section:document.getElementById(link.dataset.nav)}));
let navigationQueued = false, navigationIntent = null, initialAnchorPending = true;
function rememberWorkspaceAnchor(hash) {
  const entry = workspaceLinks.find(({link}) => link.hash === hash);
  navigationIntent = entry ? {entry, arrived:false} : null;
  queueWorkspaceNavigation();
}
function updateWorkspaceNavigation() {
  const offset = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0;
  const maxScroll = Math.max(0, document.documentElement.scrollHeight - innerHeight);
  let active = workspaceLinks[0];
  for (const entry of workspaceLinks) if (entry.section.getBoundingClientRect().top <= offset + 2) active = entry;
  // A short final table cannot necessarily reach the top of the viewport.
  if (scrollY > 0 && scrollY >= maxScroll - 2) active = workspaceLinks[workspaceLinks.length - 1];
  if (navigationIntent) {
    const destination = Math.max(0, Math.min(maxScroll, scrollY + navigationIntent.entry.section.getBoundingClientRect().top - offset));
    if (Math.abs(scrollY - destination) <= 2) {
      // Several short sections can share the same clamped scroll destination.
      active = navigationIntent.entry;
      if (!navigationIntent.arrived) active.section.querySelector("h1,h2").focus({preventScroll:true});
      navigationIntent.arrived = true;
    } else if (navigationIntent.arrived) navigationIntent = null;
  }
  for (const entry of workspaceLinks) {
    entry.link.classList.toggle("active", entry === active);
    if (entry === active) entry.link.setAttribute("aria-current", "location");
    else entry.link.removeAttribute("aria-current");
  }
}
function queueWorkspaceNavigation() {
  if (navigationQueued) return;
  navigationQueued = true;
  requestAnimationFrame(() => {navigationQueued = false; updateWorkspaceNavigation();});
}
function restoreWorkspaceAnchor() {
  if (!initialAnchorPending || !state.overview) return;
  initialAnchorPending = false;
  // The first authenticated render expands the skeleton, pins and history.
  // Restore the fragment once, never on subsequent automatic refreshes.
  requestAnimationFrame(() => {
    rememberWorkspaceAnchor(location.hash);
    navigationIntent?.entry.section.scrollIntoView({block:"start", behavior:"instant"});
  });
}
function releaseWorkspaceAnchor() {
  navigationIntent = null; initialAnchorPending = false; queueWorkspaceNavigation();
}
document.addEventListener("click", event => {
  const link = event.target.closest('a[href^="#"]');
  if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  rememberWorkspaceAnchor(link.hash);
});
window.addEventListener("hashchange", () => rememberWorkspaceAnchor(location.hash));
window.addEventListener("scroll", queueWorkspaceNavigation, {passive:true});
window.addEventListener("resize", queueWorkspaceNavigation, {passive:true});
window.addEventListener("wheel", releaseWorkspaceAnchor, {passive:true});
window.addEventListener("touchmove", releaseWorkspaceAnchor, {passive:true});
document.addEventListener("keydown", event => {
  if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)
      && !event.target.closest("input,select,textarea,button,[contenteditable],[role=dialog],dialog")) releaseWorkspaceAnchor();
});
const workspaceResizeObserver = new ResizeObserver(queueWorkspaceNavigation);
workspaceLinks.forEach(({section}) => workspaceResizeObserver.observe(section));
queueWorkspaceNavigation();
let resizeTimer;
new ResizeObserver(() => {clearTimeout(resizeTimer); resizeTimer = setTimeout(renderChart, 120);}).observe($("#chart-container"));
$("#local-date").textContent = new Date().toLocaleDateString("zh-CN", {year:"numeric", month:"long", day:"numeric", weekday:"short"});
$("#timezone-label").textContent = `时间均为本地时区 · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
$("#range-start").value = dateInput(new Date(Date.now() - 6 * 3600000)); $("#range-end").value = dateInput(new Date());
setInterval(updateFreshness, 1000);
setInterval(() => {if ($("#auto-refresh").checked && !document.hidden && !$("#auth-dialog").open) refreshOverview();}, 5000);
document.addEventListener("visibilitychange", () => {if (!document.hidden && $("#auto-refresh").checked && !$("#auth-dialog").open) refreshOverview(true);});
refreshOverview(true);
