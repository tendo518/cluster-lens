// Dependency-free browser integration test. Requires Node >=22 and Chrome/Chromium.
// Run from this project: node tests/browser-smoke.mjs (optional: CHROME=chromium PYTHON=python3).
// Only starts a local, authenticated DEMO server; no real SSH or inventory access.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtempSync, readFileSync, readdirSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {setTimeout as sleep} from "node:timers/promises";
import {fileURLToPath} from "node:url";
import {checkWorkspaceNavigation} from "./workspace-navigation.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const artifacts = mkdtempSync(join(tmpdir(), "hqlab-browser-"));
const token = "browser-smoke-test-token-12345678";
const python = process.env.PYTHON || "python3";
let server, chrome, socket;
function waitOutput(child, regex, label) {
  return new Promise((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => reject(new Error(`${label} timed out: ${output}`)), 30000);
    const onData = chunk => {
      output += chunk.toString();
      const match = output.match(regex);
      if (match) {clearTimeout(timeout); resolve(match[1]);}
    };
    child.stderr.on("data", onData); child.stdout.on("data", onData);
    child.on("error", error => {clearTimeout(timeout); reject(error);});
    child.on("exit", code => {clearTimeout(timeout); reject(new Error(`${label} exited (${code}): ${output}`));});
  });
}
try {
  server = spawn(python, ["-m", "monitor", "--demo", "--port", "0", "--db", join(artifacts, "demo.sqlite3"), "--config", "/DOES-NOT-EXIST"], {cwd:root, env:{...process.env, HQLAB_MONITOR_TOKEN:token}, stdio:["ignore", "pipe", "pipe"]});
  const base = await waitOutput(server, /(http:\/\/127\.0\.0\.1:\d+)/, "demo server");
  chrome = spawn(process.env.CHROME || "google-chrome", ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-proxy-server", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${join(artifacts, "chrome-profile")}`, "about:blank"], {stdio:["ignore", "pipe", "pipe"]});
  const debuggerURL = await waitOutput(chrome, /DevTools listening on (ws:\/\/[^\s]+)/, "Chrome");
  socket = new WebSocket(debuggerURL);
  await new Promise((resolve, reject) => {socket.addEventListener("open", resolve, {once:true}); socket.addEventListener("error", reject, {once:true});});
  let nextID = 0, session;
  const pending = new Map(), exceptions = [];
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const {resolve, reject, timeout} = pending.get(message.id); pending.delete(message.id); clearTimeout(timeout);
      if (message.error) reject(new Error(JSON.stringify(message.error))); else resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails);
  });
  function cdp(method, params = {}, target = session) {
    return new Promise((resolve, reject) => {
      const id = ++nextID;
      const timeout = setTimeout(() => {pending.delete(id); reject(new Error(`CDP timeout: ${method}`));}, 20000);
      pending.set(id, {resolve, reject, timeout});
      socket.send(JSON.stringify({id, method, params, ...(target ? {sessionId:target} : {})}));
    });
  }
  const {targetId} = await cdp("Target.createTarget", {url:"about:blank"});
  ({sessionId:session} = await cdp("Target.attachToTarget", {targetId, flatten:true}));
  await cdp("Runtime.enable"); await cdp("Page.enable");
  await cdp("Browser.setDownloadBehavior", {behavior:"allow", downloadPath:artifacts}, null);
  const evaluate = async expression => {
    const response = await cdp("Runtime.evaluate", {expression, awaitPromise:true, returnByValue:true});
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  async function waitFor(expression, label) {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {if (await evaluate(expression)) return; await sleep(100);}
    throw new Error(`Timed out: ${label} (${expression})`);
  }
  const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const setInput = (selector, value, event = "input") => evaluate(`{const el=document.querySelector(${JSON.stringify(selector)});el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(${JSON.stringify(event)},{bubbles:true}));}`);
  const screenshot = async name => {
    const result = await cdp("Page.captureScreenshot", {format:"png", captureBeyondViewport:false});
    writeFileSync(join(artifacts, name), Buffer.from(result.data, "base64"));
  };
  const appearance = () => evaluate(`(() => {
    const root = getComputedStyle(document.documentElement);
    const rgb = value => value.match(/[\\d.]+/g).map(Number);
    const blend = (fg, bg) => fg.slice(0, 3).map((v, i) => v * (fg[3] ?? 1) + bg[i] * (1 - (fg[3] ?? 1)));
    const luminance = color => color.slice(0, 3).map(v => {v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;}).reduce((n, v, i) => n + v * [.2126, .7152, .0722][i], 0);
    const ratio = (fg, bg) => {const [a, b] = [luminance(fg), luminance(bg)].sort((a, b) => a - b); return (b + .05) / (a + .05);};
    const background = el => {
      const chain = []; for (let parent = el; parent; parent = parent.parentElement) chain.unshift(parent);
      return chain.reduce((bg, parent) => blend(rgb(getComputedStyle(parent).backgroundColor), bg), [255, 255, 255]);
    };
    const selectors = ['h1', 'h2', 'h3', '.brand', '.brand-mark', '.workspace strong', '.workspace small', '.nav-label', '.nav-item', '.nav-count', '.sidebar-status', '.version', '.breadcrumb', '.topbar-right', '.eyebrow', '.button', '.live-badge', '.notice', '.notice-tag', '.summary-top', '.summary-value', '.summary-value small', '.summary-value .unit', '.summary-foot', '.summary-foot .good', '.update-line', '.section-heading p', '.pin-button', '.segmented button', 'select', '.appearance-panel label', '.custom-range', '.custom-range input', '.metric-tabs button', '.chart-legend', '.chart-axis', '.chart-empty', '.chart-tooltip', '.tooltip-row b', '.period-stat-label', '.period-stat-value', '.period-stat-value small', '.period-stat-note', '.usage-details>summary', '.details-action', '.subtle-label', '.chart-footnote', '.count-badge', '.search-field input', '.node-name', '.node-address', '.status-badge', '.node-model', '.gpu-index', '.gpu-util', '.gpu-memory', '.resource-label', '.resource-label strong', '.node-footer', '.mini-avatar', '.detail-link', '.node-warning', '.node-offline-state', '.node-offline-state strong', '.empty-state', '.empty-state h3', '.user-pill', '.user-pill b', '.data-table th', '.data-table td', '.data-table .mono', '.user-cell', '.user-avatar', '.type-tag', '.data-table .table-empty', '.table-footer', '.table-link', '.page-footer', '.footer-mark', '.detail-heading p', '.detail-section h3', '.detail-system-stat .label', '.detail-system-stat strong', '.detail-system-stat small', '.detail-facts dt', '.detail-facts dd', '.detail-gpu-header strong', '.detail-gpu-header small', '.detail-gpu-metrics label', '.detail-gpu-metrics b', '.detail-gpu-metrics small', '.detail-gpu-info', '.detail-warnings', '.detail-offline', '.detail-offline h3', '.detail-caption', '.auth-dialog p', '.auth-dialog label', '.auth-dialog input', '.auth-dialog small', '.inline-error', '.toast'];
    const failures = [];
    for (const selector of selectors) for (const el of document.querySelectorAll(selector)) {
      if (!el.getClientRects().length || el.closest(':disabled') || getComputedStyle(el).visibility === 'hidden') continue;
      const style = getComputedStyle(el), bg = background(el);
      const fg = rgb(el instanceof SVGElement ? style.fill : style.color);
      const contrast = ratio(blend(fg, bg), bg);
      const large = parseFloat(style.fontSize) >= 24 || parseFloat(style.fontSize) >= 18.67 && parseFloat(style.fontWeight) >= 700;
      if (contrast < (large ? 3 : 4.5)) failures.push({selector, contrast:+contrast.toFixed(2), color:style.color, background:bg});
    }
    for (const el of document.querySelectorAll('input[placeholder]')) {
      if (el.getClientRects().length && ratio(rgb(getComputedStyle(el, '::placeholder').color), background(el)) < 4.5) failures.push({selector:'input::placeholder'});
    }
    const chart = document.querySelector('.chart-primary'), secondary = document.querySelector('.chart-secondary');
    return {
      scheme:root.colorScheme, palette:document.documentElement.dataset.palette, body:getComputedStyle(document.body).backgroundColor,
      themeColor:[...document.querySelectorAll('meta[name="theme-color"]')].find(meta => matchMedia(meta.media).matches)?.content,
      gradient:document.querySelector('.chart-fill-start') ? getComputedStyle(document.querySelector('.chart-fill-start')).stopColor : null,
      primary:chart ? getComputedStyle(chart).stroke : null,
      seriesContrast:[chart, secondary].filter(Boolean).map(el => ratio(rgb(getComputedStyle(el).stroke), background(el))),
      failures:failures.slice(0, 20)
    };
  })()`);
  async function setScheme(scheme) {
    await cdp("Emulation.setEmulatedMedia", {features:[{name:"prefers-color-scheme", value:scheme}]});
    await waitFor(`getComputedStyle(document.documentElement).colorScheme===${JSON.stringify(scheme)}`, `${scheme} color scheme`);
    await sleep(220); // Let existing control hover/color transitions settle.
  }
  const paletteNames = ["green", "blue", "purple", "amber", "teal", "rose"];
  async function checkAppearance(scheme, label, palette = "green") {
    const value = await appearance();
    const backgrounds = {
      green:{light:["rgb(245, 247, 245)", "#f5f7f5"], dark:["rgb(16, 25, 20)", "#101914"]},
      blue:{light:["rgb(244, 247, 251)", "#f4f7fb"], dark:["rgb(17, 24, 39)", "#111827"]},
      purple:{light:["rgb(247, 245, 251)", "#f7f5fb"], dark:["rgb(25, 21, 32)", "#191520"]},
      amber:{light:["rgb(250, 247, 240)", "#faf7f0"], dark:["rgb(30, 25, 17)", "#1e1911"]},
      teal:{light:["rgb(241, 248, 247)", "#f1f8f7"], dark:["rgb(17, 29, 28)", "#111d1c"]},
      rose:{light:["rgb(251, 245, 247)", "#fbf5f7"], dark:["rgb(33, 21, 28)", "#21151c"]},
    };
    assert.equal(value.scheme, scheme, label);
    assert.equal(value.palette, palette, label);
    assert.equal(value.body, backgrounds[palette][scheme][0], label);
    assert.equal(value.themeColor, backgrounds[palette][scheme][1], `${label}: browser chrome follows theme`);
    assert.deepEqual(value.failures, [], `${label}: text contrast`);
    assert.ok(value.seriesContrast.every(ratio => ratio >= 3), `${label}: chart series contrast`);
    if (value.gradient) assert.equal(value.gradient, value.primary, `${label}: SVG gradient follows line color`);
  }
  await cdp("Emulation.setDeviceMetricsOverride", {width:1440, height:1100, deviceScaleFactor:1, mobile:false});
  await cdp("Emulation.setEmulatedMedia", {features:[{name:"prefers-color-scheme", value:"light"}]});
  await cdp("Page.navigate", {url:base});
  await waitFor("document.querySelector('#auth-dialog')?.open", "authentication dialog");
  await setInput("#access-token", "wrong-token"); await evaluate("document.querySelector('#auth-form').requestSubmit()");
  await waitFor("!document.querySelector('#auth-error').hidden", "invalid-token error");
  for (const scheme of ["dark", "light"]) {
    await setScheme(scheme); await checkAppearance(scheme, `${scheme} authentication`);
    await screenshot(`auth-${scheme}.png`);
  }
  await setInput("#access-token", token); await evaluate("document.querySelector('#auth-form').requestSubmit()");
  await waitFor("!document.querySelector('#auth-dialog').open && document.querySelectorAll('#node-grid .node-card').length===8 && !!document.querySelector('.chart-primary')", "authenticated dashboard");
  assert.equal(await evaluate("document.querySelector('#demo-banner').hidden"), false);
  assert.match(await evaluate("document.querySelector('#summary').textContent"), /7\s*\/ 8 台/);
  assert.equal(await evaluate("document.querySelectorAll('#usage-rows tr').length"), 8);
  await screenshot("desktop.png");
  await click("#auto-refresh");
  assert.match(await evaluate("document.querySelector('#live-badge').textContent"), /暂停/);
  assert.match(await evaluate("(() => {const saved=Date.now;Date.now=()=>saved()+120000;updateFreshness();const label=document.querySelector('#live-badge').textContent;Date.now=saved;updateFreshness();return label;})()"), /暂停/, "a paused page must not falsely report a disconnected service");
  await checkWorkspaceNavigation({cdp, evaluate, click, setInput, waitFor, screenshot, token});
  const priorityHost = "demo-v100-02", offlineHost = "demo-offline-08";
  const pinSelector = (area, name) => `${area} [data-pin-node="${name}"]`;
  assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), "0");
  assert.equal(await evaluate("!!(document.querySelector('#pinned-servers').compareDocumentPosition(document.querySelector('#summary')) & Node.DOCUMENT_POSITION_FOLLOWING)"), true, "pinned servers precede the cluster summary");
  await click(pinSelector("#node-grid", priorityHost));
  assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), "1");
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(pinSelector("#node-grid", priorityHost))}).getAttribute('aria-pressed')`), "true");
  assert.deepEqual(await evaluate("JSON.parse(localStorage.getItem(PIN_STORAGE_KEY))"), [priorityHost]);
  assert.equal(await evaluate("document.querySelector('#node-dialog').open"), false, "pinning must not open the inspector");
  assert.match(await evaluate("document.querySelector('#summary').textContent"), /7\s*\/ 8 台/, "pins do not double-count cluster metrics");

  await evaluate(`document.querySelector(${JSON.stringify(pinSelector("#node-grid", priorityHost))}).focus({preventScroll:true})`);
  for (const expected of ["0", "1"]) {
    await cdp("Input.dispatchKeyEvent", {type:"keyDown", key:" ", code:"Space", windowsVirtualKeyCode:32});
    await cdp("Input.dispatchKeyEvent", {type:"keyUp", key:" ", code:"Space", windowsVirtualKeyCode:32});
    assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), expected, "keyboard pin toggle");
    assert.equal(await evaluate("document.activeElement.dataset.pinNode"), priorityHost, "pin toggle preserves keyboard focus");
  }
  await click(pinSelector("#node-grid", offlineHost));
  assert.deepEqual(await evaluate("[...document.querySelectorAll('#pinned-grid .node-card')].map(el=>el.dataset.node)"), [priorityHost, offlineHost], "multiple pins retain pin order");
  assert.equal(await evaluate(`document.querySelector('#pinned-grid [data-node="${offlineHost}"] .status-badge').textContent`), "离线");
  assert.deepEqual(await evaluate(`Array.from(document.querySelectorAll('#pinned-grid [data-node="${offlineHost}"] .resource-label strong'),el=>el.textContent)`), ["—", "—", "—"], "offline pins never invent live metrics");
  await evaluate(`document.querySelector(${JSON.stringify(pinSelector("#pinned-grid", offlineHost))}).focus({preventScroll:true})`);
  await click(pinSelector("#pinned-grid", offlineHost));
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(pinSelector("#node-grid", offlineHost))}).getAttribute('aria-pressed')`), "false", "unpin syncs the inventory button");
  assert.equal(await evaluate("document.activeElement.dataset.pinNode"), priorityHost, "unpinning moves focus to a remaining pinned card");
  await cdp("Input.dispatchKeyEvent", {type:"keyDown", key:" ", code:"Space", windowsVirtualKeyCode:32});
  await cdp("Input.dispatchKeyEvent", {type:"keyUp", key:" ", code:"Space", windowsVirtualKeyCode:32});
  assert.equal(await evaluate("document.activeElement.id"), "choose-pinned", "removing the last pinned card keeps keyboard focus in the section");
  await click(pinSelector("#node-grid", priorityHost));
  await click(`#pinned-grid [data-open-node="${priorityHost}"]`);
  assert.match(await evaluate("document.querySelector('#detail-title').textContent"), /demo-v100-02/);
  for (const expected of ["0", "1"]) {
    await click(pinSelector("#node-detail", priorityHost));
    assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), expected, "inspector can pin and unpin");
    assert.equal(await evaluate("document.querySelector('#node-dialog').open"), true, "pinning preserves the open inspector");
  }
  await evaluate(`document.querySelector(${JSON.stringify(pinSelector("#node-detail", priorityHost))}).focus({preventScroll:true});renderOverview();`);
  assert.equal(await evaluate("!!document.activeElement.closest('#node-detail [data-pin-node]')"), true, "overview refresh preserves the inspector pin focus");
  await click("#close-detail");

  assert.deepEqual(await evaluate(`(() => {
    const saved=localStorage.getItem(PIN_STORAGE_KEY), results=[];
    try {for(const raw of ['broken', 'null', '{}', JSON.stringify([null, ${JSON.stringify(priorityHost)}, ${JSON.stringify(priorityHost)}, 42, '', 'x'.repeat(256)])]) {
      localStorage.setItem(PIN_STORAGE_KEY,raw); results.push(readPinnedHosts());
    }} finally {localStorage.setItem(PIN_STORAGE_KEY,saved);}
    return results;
  })()`), [[], [], [], [priorityHost]], "corrupt preferences are ignored and pins deduplicated");
  await evaluate(`localStorage.setItem(PIN_STORAGE_KEY,JSON.stringify([${JSON.stringify(priorityHost)},'not-a-managed-host']));window.dispatchEvent(new StorageEvent('storage',{key:PIN_STORAGE_KEY,storageArea:localStorage}));`);
  assert.equal(await evaluate("document.querySelectorAll('#pinned-grid .node-card').length"), 1, "pins only render managed hosts");
  await evaluate("localStorage.removeItem(PIN_STORAGE_KEY);window.dispatchEvent(new StorageEvent('storage',{key:null,storageArea:localStorage}));");
  assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), "0", "cross-tab preference reset");
  await evaluate(`localStorage.setItem(PIN_STORAGE_KEY,JSON.stringify([${JSON.stringify(priorityHost)}]));window.dispatchEvent(new StorageEvent('storage',{key:PIN_STORAGE_KEY,storageArea:localStorage}));`);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(pinSelector("#node-grid", priorityHost))}).getAttribute('aria-pressed')`), "true", "cross-tab changes synchronize all pin buttons");

  const originTime = await evaluate("performance.timeOrigin");
  await cdp("Page.reload"); await sleep(300);
  await waitFor(`performance.timeOrigin!==${originTime} && document.querySelectorAll('#node-grid .node-card').length===8 && document.querySelectorAll('#pinned-grid .node-card').length===1 && !!document.querySelector('.chart-primary')`, "pins and session authentication survive reload");
  assert.equal(await evaluate("document.querySelector('#auth-dialog').open"), false);
  assert.deepEqual(await evaluate("state.pinned"), [priorityHost]);
  await click("#auto-refresh");
  for (const area of ["#node-grid", "#pinned-grid"]) {
    await evaluate(`document.querySelector(${JSON.stringify(pinSelector(area, priorityHost))}).focus({preventScroll:true});renderOverview();`);
    assert.equal(await evaluate(`!!document.activeElement.closest(${JSON.stringify(area)})`), true, `${area}: auto refresh preserves pin focus`);
  }
  await evaluate("document.activeElement.blur()");
  const originalStatus = await evaluate(`state.overview.hosts.find(host=>host.name===${JSON.stringify(priorityHost)}).status`);
  for (const status of ["pending", "offline"]) {
    await evaluate(`state.overview.hosts.find(host=>host.name===${JSON.stringify(priorityHost)}).status=${JSON.stringify(status)};renderOverview();`);
    assert.equal(await evaluate("document.querySelector('#pinned-grid .status-badge').textContent"), status === "pending" ? "待上报" : "离线");
    assert.equal(await evaluate("document.querySelectorAll('#pinned-grid .gpu-tile').length"), 0, "live pin status follows the overview without retaining old GPU values");
    assert.deepEqual(await evaluate("Array.from(document.querySelectorAll('#pinned-grid .resource-label strong'),el=>el.textContent)"), ["—", "—", "—"]);
  }
  await evaluate(`state.overview.hosts.find(host=>host.name===${JSON.stringify(priorityHost)}).status=${JSON.stringify(originalStatus)};renderOverview();`);
  assert.ok(await evaluate("document.querySelectorAll('#pinned-grid .gpu-tile').length") > 0, "pinned node recovers on the next overview");
  await sleep(250);
  const chartPath = await evaluate("document.querySelector('.chart-primary').getAttribute('d')");
  for (const scheme of ["light", "dark"]) {
    await setScheme(scheme); await checkAppearance(scheme, `${scheme} dashboard`);
    assert.equal(await evaluate("document.querySelector('#auth-dialog').open"), false, "theme switch preserves authentication");
    assert.equal(await evaluate("document.querySelector('.chart-primary').getAttribute('d')"), chartPath, "theme switch preserves chart data");
    await evaluate("{const hit=document.querySelector('#chart-hit'), r=hit.getBoundingClientRect();hit.dispatchEvent(new PointerEvent('pointermove',{clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}");
    await waitFor("!document.querySelector('.chart-tooltip').hidden", `${scheme} chart tooltip`);
    await checkAppearance(scheme, `${scheme} tooltip`);
    await screenshot(`desktop-${scheme}.png`);
    await evaluate("document.querySelector('#chart-hit').dispatchEvent(new PointerEvent('pointerleave'))");
  }

  await setInput("#status-filter", "offline", "change");
  assert.equal(await evaluate("document.querySelectorAll('#node-grid .node-card').length"), 1);
  assert.equal(await evaluate("document.querySelector('#pinned-grid .node-name').textContent"), priorityHost, "status filter does not hide pinned hosts");
  await click("#node-grid .node-name");
  assert.match(await evaluate("document.querySelector('#node-detail').textContent"), /节点当前离线/);
  assert.match(await evaluate("document.querySelector('#node-detail').textContent"), /未收到节点采集脚本上报/);
  await cdp("Input.dispatchKeyEvent", {type:"keyDown", key:"Escape", code:"Escape", windowsVirtualKeyCode:27});
  await cdp("Input.dispatchKeyEvent", {type:"keyUp", key:"Escape", code:"Escape", windowsVirtualKeyCode:27});
  await waitFor("!document.querySelector('#node-dialog').open", "Escape closes inspector");
  await setInput("#status-filter", "all", "change");
  await setInput("#server-search", "no-such-server");
  assert.equal(await evaluate("document.querySelector('#nodes-empty').hidden"), false);
  assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), "1", "search does not hide pinned hosts");
  await click("#clear-filters");
  assert.equal(await evaluate("document.querySelectorAll('#node-grid .node-card').length"), 8);
  await click(".gpu-tile[data-gpu='1']");
  assert.equal(await evaluate("document.querySelector('#detail-gpu-filter').value"), "1");
  assert.ok(await evaluate("document.querySelectorAll('.detail-gpu').length") > 0);
  for (const scheme of ["light", "dark"]) {
    await setScheme(scheme); await checkAppearance(scheme, `${scheme} inspector`);
    await screenshot(`node-inspector-${scheme}.png`);
  }
  await screenshot("node-inspector.png"); await click("#close-detail");

  await click("[data-metric='memory']");
  assert.equal(await evaluate("document.querySelector('#primary-legend').textContent"), "GPU 显存");
  await setInput("#history-host", "demo-offline-08", "change");
  await waitFor("document.querySelector('#chart-container').getAttribute('aria-busy')==='false' && !!document.querySelector('.chart-empty')", "offline history is an empty chart, not zero utilization");
  await checkAppearance("dark", "dark empty history");
  await setInput("#history-host", "demo-p100-01", "change");
  await waitFor("document.querySelector('#chart-container').getAttribute('aria-busy')==='false' && document.querySelectorAll('#usage-rows tr').length===1", "per-host statistics");
  await click("[data-hours='24']");
  await waitFor("document.querySelector('#chart-container').getAttribute('aria-busy')==='false'", "24-hour history");
  assert.ok(await evaluate("document.querySelector('.chart-primary').getAttribute('d').length") > 100);
  await click("#custom-range-toggle");
  const end = new Date(), start = new Date(end.getTime() - 2 * 3600000);
  const local = date => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  await setInput("#range-start", local(start)); await setInput("#range-end", local(end));
  for (const scheme of ["light", "dark"]) {
    await setScheme(scheme); await checkAppearance(scheme, `${scheme} date / select controls`);
    assert.equal(await evaluate("getComputedStyle(document.querySelector('#range-start')).colorScheme"), scheme);
    assert.equal(await evaluate("getComputedStyle(document.querySelector('#history-host')).colorScheme"), scheme);
  }
  await evaluate("document.querySelector('#custom-range').requestSubmit()");
  await waitFor("document.querySelector('#custom-range-toggle').classList.contains('selected') && document.querySelector('#chart-container').getAttribute('aria-busy')==='false'", "custom range");
  await click("#export-button");
  for (let attempt = 0; attempt < 100 && !readdirSync(artifacts).some(p => p.endsWith(".csv")); attempt++) await sleep(100);
  const csv = readdirSync(artifacts).find(p => p.endsWith(".csv"));
  assert.ok(csv, "CSV was downloaded");
  assert.match(readFileSync(join(artifacts, csv), "utf8"), /GPU平均使用率/);

  await click("#user-strip [data-user]:nth-child(2)");
  const selectedUser = await evaluate("document.querySelector('#user-strip .selected').dataset.user");
  assert.ok(selectedUser);
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('#process-rows .user-cell>span:last-child')).every(el=>el.textContent===${JSON.stringify(selectedUser)})`), true);
  await setInput("#process-search", "no-such-process");
  assert.match(await evaluate("document.querySelector('#process-rows').textContent"), /没有匹配/);
  assert.equal(await evaluate("document.querySelector('#process-page-info').textContent"), "0 / 0");
  assert.equal(await evaluate("document.querySelector('#process-next').disabled"), true);
  await setInput("#process-search", ""); await click("#user-strip [data-user='']");
  assert.equal(await evaluate("document.querySelectorAll('#process-rows tr').length"), 25);
  await click("#process-next");
  assert.equal(await evaluate("state.processPage"), 2);
  assert.ok(await evaluate("document.querySelectorAll('#process-rows tr').length") < 25);
  await evaluate("renderOverview()");
  assert.equal(await evaluate("state.processPage"), 2, "overview refresh preserves the current process page");

  await evaluate(`{
    window.paginationOverview=state.overview;
    const host=structuredClone(state.overview.hosts[0]), template=allProcesses()[0];
    host.status='online'; host.gpus.forEach(gpu=>{gpu.processes=[];});
    host.gpus[0].processes=Array.from({length:137},(_,i)=>({...template,pid:50000+i,user:'pager',name:'/usr/bin/python',memory_mb:1000-i}));
    host.gpus[1].processes=[{...host.gpus[0].processes[0],memory_mb:1}];
    state.overview={...state.overview,hosts:[host]};state.processPage=1;renderProcesses();
  }`);
  try {
    assert.match(await evaluate("document.querySelector('#process-footer').textContent"), /137 个进程 · 138 条 GPU 关联 · 第 1–25 条/);
    assert.equal(await evaluate("document.querySelector('#process-page-info').textContent"), "1 / 6");
    assert.equal(await evaluate("document.querySelector('#process-first').disabled && document.querySelector('#process-previous').disabled"), true);
    await click("#process-last");
    assert.equal(await evaluate("document.querySelector('#process-page-info').textContent"), "6 / 6");
    assert.equal(await evaluate("document.querySelectorAll('#process-rows tr').length"), 13);
    assert.equal(await evaluate("document.querySelector('#process-next').disabled && document.querySelector('#process-last').disabled"), true);
    await click("#process-previous"); assert.equal(await evaluate("state.processPage"), 5);
    await click("#process-first"); await click("#process-next");
    const pageRows = await evaluate("document.querySelector('#process-rows').textContent");
    await evaluate("renderOverview()");
    assert.equal(await evaluate("state.processPage"), 2);
    assert.equal(await evaluate("document.querySelector('#process-rows').textContent"), pageRows, "refresh does not reset or duplicate the displayed page");
    for (const [size, pages, lastRows] of [[50, 3, 38], [100, 2, 38]]) {
      await setInput("#process-page-size", String(size), "change");
      assert.equal(await evaluate("state.processPage"), 1, "changing page size resets the page");
      assert.equal(await evaluate("document.querySelectorAll('#process-rows tr').length"), size);
      assert.equal(await evaluate("document.querySelector('#process-page-info').textContent"), `1 / ${pages}`);
      await click("#process-last");
      assert.equal(await evaluate("document.querySelectorAll('#process-rows tr').length"), lastRows);
    }
    await evaluate("renderOverview()");
    assert.equal(await evaluate("state.processPageSize"), 100);
    assert.equal(await evaluate("state.processPage"), 2);
    await setInput("#process-page-size", "13", "change");
    assert.equal(await evaluate("document.querySelector('#process-page-size').value"), "100", "invalid page sizes are rejected");
    await setInput("#process-search", "50136");
    assert.equal(await evaluate("state.processPage"), 1, "search resets the page and covers records beyond the displayed page");
    assert.match(await evaluate("document.querySelector('#process-rows').textContent"), /50136/);
    assert.equal(await evaluate("document.querySelectorAll('#process-rows tr').length"), 1);
    assert.equal(await evaluate("state.processPageSize"), 100, "search preserves the selected page size");
    await setInput("#process-search", ""); await setInput("#process-page-size", "25", "change");
    await click("#process-last"); await click("#user-strip [data-user='pager']");
    assert.equal(await evaluate("state.processPage"), 1, "user filtering resets the page");
    assert.match(await evaluate("document.querySelector('#process-footer').textContent"), /137 个进程 · 138 条 GPU 关联/);
    await click("#user-strip [data-user='']"); await click("#process-last");
    await evaluate("state.overview.hosts[0].gpus[0].processes=state.overview.hosts[0].gpus[0].processes.slice(0,10);renderOverview();");
    assert.equal(await evaluate("document.querySelector('#process-page-info').textContent"), "1 / 1", "a shrinking live dataset clamps the current page");
    assert.equal(await evaluate("document.querySelectorAll('#process-rows tr').length"), 11);
    await evaluate("state.overview.hosts[0].gpus.forEach(gpu=>{gpu.processes=[];});renderProcesses();");
    assert.equal(await evaluate("document.querySelector('#process-page-info').textContent"), "0 / 0");
    assert.equal(await evaluate("Array.from(document.querySelectorAll('#process-pagination button')).every(button=>button.disabled)"), true);
    assert.match(await evaluate("document.querySelector('#process-rows').textContent"), /暂无/);
  } finally {
    await evaluate("state.overview=window.paginationOverview;delete window.paginationOverview;state.processPage=1;state.processPageSize=25;state.processSearch='';state.user='';document.querySelector('#process-search').value='';renderOverview();");
  }

  await setInput("#history-host", "", "change"); await click("[data-metric='compute']"); await click("[data-hours='1']");
  await waitFor("document.querySelector('#chart-container').getAttribute('aria-busy')==='false'", "reset history");
  assert.deepEqual(await evaluate("Array.from(document.querySelector('#appearance-palette').options, option=>option.value)"), paletteNames);
  await evaluate("window.scrollTo({top:0,behavior:'instant'})");
  assert.equal(await evaluate("document.querySelector('#appearance-panel').hidden"), true);
  assert.equal(await evaluate("document.querySelector('#appearance-toggle').getAttribute('aria-expanded')"), "false");
  const appearancePoint = await evaluate("(()=>{const r=document.querySelector('#appearance-toggle').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};})()");
  await cdp("Input.dispatchMouseEvent", {type:"mousePressed",...appearancePoint,button:"left",clickCount:1});
  await cdp("Input.dispatchMouseEvent", {type:"mouseReleased",...appearancePoint,button:"left",clickCount:1});
  assert.equal(await evaluate("document.querySelector('#appearance-panel').hidden"), false);
  assert.equal(await evaluate("document.activeElement.id"), "appearance-toggle", "pointer opening does not automatically focus a native mobile picker");
  await evaluate("document.body.click()");
  await click("#appearance-toggle");
  assert.equal(await evaluate("document.activeElement.id"), "appearance-theme");
  await cdp("Input.dispatchKeyEvent", {type:"keyDown",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
  await cdp("Input.dispatchKeyEvent", {type:"keyUp",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
  assert.equal(await evaluate("document.activeElement.id"), "appearance-palette");
  await cdp("Input.dispatchKeyEvent", {type:"keyDown",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
  await cdp("Input.dispatchKeyEvent", {type:"keyUp",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
  assert.equal(await evaluate("document.querySelector('#appearance-panel').hidden"), true, "Tab outside dismisses appearance panel");
  await evaluate("document.querySelector('#appearance-toggle').focus()");
  await cdp("Input.dispatchKeyEvent", {type:"keyDown",key:"Enter",code:"Enter",windowsVirtualKeyCode:13,text:"\r",unmodifiedText:"\r"});
  await cdp("Input.dispatchKeyEvent", {type:"keyUp",key:"Enter",code:"Enter",windowsVirtualKeyCode:13});
  assert.equal(await evaluate("document.querySelector('#appearance-panel').hidden"), false, "Enter opens appearance panel");
  await cdp("Input.dispatchKeyEvent", {type:"keyDown",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});
  await cdp("Input.dispatchKeyEvent", {type:"keyUp",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});
  assert.equal(await evaluate("document.querySelector('#appearance-panel').hidden"), true);
  assert.equal(await evaluate("document.activeElement.id"), "appearance-toggle", "Escape restores trigger focus");
  await cdp("Input.dispatchKeyEvent", {type:"keyDown",key:"ArrowDown",code:"ArrowDown",windowsVirtualKeyCode:40});
  await cdp("Input.dispatchKeyEvent", {type:"keyUp",key:"ArrowDown",code:"ArrowDown",windowsVirtualKeyCode:40});
  assert.equal(await evaluate("document.activeElement.id"), "appearance-theme", "ArrowDown opens appearance controls");
  await evaluate("document.body.click()");
  assert.equal(await evaluate("document.querySelector('#appearance-panel').hidden"), true, "outside click dismisses appearance panel");
  await click("#appearance-toggle"); await click("#appearance-toggle");
  assert.equal(await evaluate("document.querySelector('#appearance-panel').hidden"), true, "trigger toggles the panel closed");
  for (const palette of paletteNames) {
    await setInput("#appearance-palette", palette, "change");
    for (const scheme of ["light", "dark"]) {
      await setScheme(scheme);
      for (const width of [1440, 1024, 768, 390, 320]) {
        await cdp("Emulation.setDeviceMetricsOverride", {width, height:900, deviceScaleFactor:1, mobile:width < 500});
        await sleep(250);
        assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true, `${palette}/${scheme}: no horizontal overflow at ${width}px`);
        await evaluate("window.scrollTo({top:0,behavior:'instant'})");
        await click("#appearance-toggle");
        assert.equal(await evaluate("document.querySelector('#appearance-toggle').getAttribute('aria-expanded')"), "true");
        assert.equal(await evaluate("(()=>{const r=document.querySelector('#appearance-panel').getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight;})()"), true, "appearance dropdown stays inside the viewport");
        assert.equal(await evaluate("document.querySelector('#appearance-palette').value"), palette);
        await checkAppearance(scheme, `${palette}/${scheme} at ${width}px`, palette);
        if(width===390) await screenshot(`appearance-${palette}-${scheme}.png`);
        await click("#appearance-toggle");
        assert.equal(await evaluate("document.querySelector('#process-pagination').scrollWidth <= document.querySelector('#process-pagination').clientWidth"), true, `${scheme}: pagination fits at ${width}px`);
        assert.equal(await evaluate("(()=>{const table=document.querySelector('#processes>.table-scroll');return table.scrollHeight<=table.clientHeight+1;})()"), true, "process table has no independent vertical scroll");
        assert.equal(await evaluate("Array.from(document.querySelectorAll('.gpu-tile')).every(el=>el.scrollWidth<=el.clientWidth+1)"), true, "larger GPU text fits the tiles");
        assert.equal(await evaluate("(()=>{const gpu=document.querySelector('.gpu-util'),saved=gpu.innerHTML;try{gpu.innerHTML='100<small>%</small>';return gpu.scrollWidth<=gpu.parentElement.clientWidth;}finally{gpu.innerHTML=saved;}})()"), true, "100% GPU utilization fits at the larger size");
        assert.equal(await evaluate("(()=>{const label=document.querySelector('.node-resources>div:last-child .resource-label'),value=label.lastElementChild,saved=value.textContent;try{value.textContent='100%';return label.firstElementChild.scrollHeight<=parseFloat(getComputedStyle(label).lineHeight)+1;}finally{value.textContent=saved;}})()"), true, `${palette}/${scheme}/${width}px: disk label does not wrap with a three-digit percentage`);
        assert.equal(await evaluate("parseFloat(getComputedStyle(document.querySelector('#process-rows td')).fontSize)>=13 && parseFloat(getComputedStyle(document.querySelector('.node-name')).fontSize)>=13 && parseFloat(getComputedStyle(document.querySelector('.gpu-util')).fontSize)>=18"), true, "data text is larger on desktop and mobile");
      }
      await evaluate("document.querySelector('.process-table-footer').scrollIntoView({block:'end',behavior:'instant'})"); await screenshot(`process-pagination-${palette}-${scheme}.png`);
      await evaluate("window.scrollTo({top:0,behavior:'instant'})"); await screenshot(`mobile-${palette}-${scheme}.png`);
      await evaluate("document.querySelector('#servers').scrollIntoView({behavior:'instant'})"); await screenshot(`mobile-servers-${palette}-${scheme}.png`);
      await click(".node-name");
      assert.equal(await evaluate("document.querySelector('#node-dialog').scrollWidth <= window.innerWidth"), true, `${scheme}: mobile inspector does not overflow`);
      await checkAppearance(scheme, `${palette}/${scheme} mobile inspector`, palette);
      await click("#close-detail");
    }
  }
  await setInput("#appearance-palette", "green", "change"); await sleep(220);
  await screenshot("mobile-servers.png");
  await click("#refresh-button");
  await waitFor("!document.querySelector('#refresh-button').disabled", "manual refresh");
  await cdp("Network.enable");
  await cdp("Network.emulateNetworkConditions", {offline:true, latency:0, downloadThroughput:-1, uploadThroughput:-1});
  await click("#refresh-button");
  await waitFor("!document.querySelector('#connection-error').hidden", "service connection failure");
  assert.match(await evaluate("document.querySelector('#live-badge').textContent"), /连接中断/);
  assert.equal(await evaluate("document.querySelectorAll('#node-grid .node-card').length"), 8, "last known data remains visible");
  assert.equal(await evaluate("document.querySelectorAll('#pinned-grid .node-card').length"), 1, "connection failure preserves the pinned snapshot with the global error notice");
  for (const scheme of ["light", "dark"]) {
    await setScheme(scheme); await checkAppearance(scheme, `${scheme} connection error`);
  }
  await cdp("Network.emulateNetworkConditions", {offline:false, latency:0, downloadThroughput:-1, uploadThroughput:-1});
  await click("#refresh-button");
  await waitFor("document.querySelector('#connection-error').hidden && !document.querySelector('#refresh-button').disabled", "connection recovery");
  await cdp("Emulation.setDeviceMetricsOverride", {width:1440,height:1100,deviceScaleFactor:1,mobile:false}); await sleep(250);
  const displayedPath = await evaluate("document.querySelector('.chart-primary').getAttribute('d')");
  for (const palette of paletteNames) for (const theme of ["light", "dark"]) {
    await setInput("#appearance-palette", palette, "change"); await setInput("#appearance-theme", theme, "change");
    await cdp("Emulation.setEmulatedMedia", {features:[{name:"prefers-color-scheme",value:theme==="dark"?"light":"dark"}]}); await sleep(220);
    await checkAppearance(theme, `manual ${palette}/${theme} overrides system`, palette);
    assert.deepEqual(await evaluate("JSON.parse(localStorage.getItem('hqlab-monitor-appearance'))"), {theme,palette});
    assert.equal(await evaluate("document.querySelector('.chart-primary').getAttribute('d')"), displayedPath, "appearance changes preserve chart data");
    assert.equal(await evaluate("document.querySelector('#auth-dialog').open"), false);
    assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), "1");
    assert.match(await evaluate("document.querySelector('link[rel=icon]').href"), /^data:image\/svg\+xml,/);
    await evaluate("window.scrollTo({top:0,behavior:'instant'})"); await screenshot(`manual-${palette}-${theme}.png`);
  }
  const firstPaint = await cdp("Page.addScriptToEvaluateOnNewDocument", {source:"requestAnimationFrame(function inspect(){if(!document.querySelector('#appearance-theme')){requestAnimationFrame(inspect);return;}window.firstAppearance={theme:document.documentElement.dataset.theme,palette:document.documentElement.dataset.palette};});"});
  for (const palette of paletteNames) {
    await setInput("#appearance-palette", palette, "change");
    await setInput("#appearance-theme", "dark", "change");
    const appearanceOrigin = await evaluate("performance.timeOrigin"); await cdp("Page.reload"); await sleep(300);
    await waitFor(`performance.timeOrigin!==${appearanceOrigin} && document.querySelectorAll('#node-grid .node-card').length===8 && !!document.querySelector('.chart-primary') && !!window.firstAppearance`, "appearance reload");
    assert.deepEqual(await evaluate("window.firstAppearance"), {theme:"dark",palette}, "saved appearance is applied before first paint, even against system preference");
    await checkAppearance("dark", `persisted ${palette}/dark`, palette);
    await click("#auto-refresh");
  }
  await cdp("Page.removeScriptToEvaluateOnNewDocument", {identifier:firstPaint.identifier});
  await setInput("#appearance-palette", "blue", "change");
  await setInput("#appearance-theme", "system", "change");
  for (const scheme of ["light", "dark"]) {await setScheme(scheme); await checkAppearance(scheme, `blue follows ${scheme} system`, "blue");}
  await evaluate("localStorage.setItem('hqlab-monitor-appearance','broken');window.dispatchEvent(new StorageEvent('storage',{key:'hqlab-monitor-appearance',storageArea:localStorage}));");
  await sleep(220); await checkAppearance("dark", "corrupt appearance falls back to system / green");
  for (const palette of paletteNames) {
    await evaluate(`localStorage.setItem('hqlab-monitor-appearance',JSON.stringify({theme:'light',palette:${JSON.stringify(palette)}}));window.dispatchEvent(new StorageEvent('storage',{key:'hqlab-monitor-appearance',storageArea:localStorage}));`);
    await sleep(220); await checkAppearance("light", "cross-tab appearance synchronization", palette);
  }
  await setInput("#appearance-theme", "system", "change"); await setInput("#appearance-palette", "green", "change"); await sleep(220);
  await evaluate("window.appearanceSetItem=Storage.prototype.setItem;Storage.prototype.setItem=()=>{throw new DOMException('blocked','SecurityError');};");
  try {
    await setInput("#appearance-palette", "blue", "change"); await sleep(220);
    await checkAppearance("dark", "appearance works without persistence", "blue");
    assert.match(await evaluate("document.querySelector('#toast').textContent"), /仅本标签页生效/);
  } finally {await evaluate("Storage.prototype.setItem=window.appearanceSetItem;delete window.appearanceSetItem;");}
  await setInput("#appearance-palette", "green", "change"); await sleep(220);
  assert.deepEqual(await evaluate("(() => {const original=Storage.prototype.getItem;try{Storage.prototype.getItem=()=>{throw new DOMException('blocked','SecurityError');};return readPinnedHosts();}finally{Storage.prototype.getItem=original;}})()"), [], "blocked storage reads do not break startup");
  await evaluate("window.originalSetItem=Storage.prototype.setItem;Storage.prototype.setItem=()=>{throw new DOMException('quota exceeded','QuotaExceededError');};");
  try {
    await click(pinSelector("#node-grid", priorityHost));
    assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), "0", "pinning still works in memory if persistence is unavailable");
    assert.match(await evaluate("document.querySelector('#toast').textContent"), /仅本标签页生效/);
  } finally {await evaluate("Storage.prototype.setItem=window.originalSetItem;delete window.originalSetItem;");}
  await click(pinSelector("#node-grid", priorityHost));
  assert.equal(await evaluate("document.querySelector('#pinned-count').textContent"), "1");
  await cdp("Emulation.setEmulatedMedia", {features:[{name:"prefers-color-scheme", value:"dark"}, {name:"prefers-reduced-motion", value:"reduce"}]});
  assert.equal(await evaluate("getComputedStyle(document.documentElement).scrollBehavior"), "auto", "reduced motion is respected in dark mode");
  assert.deepEqual(exceptions, [], "no uncaught browser exceptions");
  console.log("PASS: workspace order / native anchors / mobile offsets / scrollspy / short-page selection / layout changes / Back-Forward / keyboard focus / authenticated fragment restoration, icon-triggered appearance dropdown, keyboard / Escape / outside dismissal, saved manual/system appearance, six palettes / twelve light-dark combinations, first-paint restoration, larger responsive data text, appearance storage/sync, persistent pinned servers, keyboard/inspector pin toggles, filter isolation, storage recovery/sync, live pin status, light/dark adaptation, text/chart contrast, native controls, authentication, 8 nodes, filters, GPU inspector, time ranges, metrics, empty/offline history, per-host stats, CSV, users, 25/50/100-row pagination, global filtering, live page retention/clamping, empty-page boundaries, pause/manual refresh, network recovery, reduced motion and responsive layouts in both themes.");
  console.log(`Screenshots and CSV: ${artifacts}`);
} finally {
  socket?.close(); chrome?.kill("SIGTERM"); server?.kill("SIGTERM");
}
