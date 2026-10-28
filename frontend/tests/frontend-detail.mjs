// Real-DOM inspector refresh regressions, also reused by browser-smoke.mjs.
// Standalone: npm run test:detail (Chrome/Chromium; no server/network).
// MONITOR_TEST_SOURCE=1 renders a write:false in-memory test bundle and public
// markup/CSS, leaving production dist and its build receipt untouched.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtempSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {refresh, readOverview, setOverviewFixture, restoreOverviewFixture} from "./browser-fixtures.mjs";

export async function checkDetailFocus({evaluate, trigger}) {
  const initial = await evaluate("({gpu:document.querySelector('#detail-gpu-filter').value,host:document.querySelector('#node-detail [data-pin-node]').dataset.pinNode,scroll:document.querySelector('#node-dialog').scrollTop})");
  assert.equal(await evaluate("document.querySelector('#node-dialog').open"), true);
  async function refreshWithFixture() {
    await evaluate("window.previousDetailFilter=document.querySelector('#detail-gpu-filter')");
    await refresh(evaluate, trigger);
    const result = await evaluate("({replaced:window.previousDetailFilter!==document.querySelector('#detail-gpu-filter'),dialog:document.querySelector('#node-dialog').open,error:document.querySelector('#connection-error').hidden?null:document.querySelector('#connection-error').textContent,fixtureHosts:window.overviewFixture?.hosts?.length})");
    assert.equal(result.replaced, true, `refresh really replaced the inspector controls: ${JSON.stringify(result)}`);
  }
  try {
    await evaluate("{const filter=document.querySelector('#detail-gpu-filter');filter.focus({preventScroll:true});filter.value='1';filter.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#node-dialog').scrollTop=160;}");
    const scroll = await evaluate("document.querySelector('#node-dialog').scrollTop");
    assert.ok(scroll > 0, "exercise preservation of a genuinely scrolled inspector");
    for (let count = 0; count < 2; count++) {
      await refreshWithFixture();
      assert.equal(await evaluate("document.activeElement.id"), "detail-gpu-filter", "overview refresh retains GPU filter keyboard focus");
      assert.equal(await evaluate("document.querySelector('#detail-gpu-filter').value"), "1", "refresh retains the selected GPU");
      assert.equal(await evaluate("Array.from(document.querySelectorAll('#node-detail tbody:first-of-type tr td:nth-child(2)')).filter(td=>td.textContent.startsWith('GPU ')).every(td=>td.textContent==='GPU 1')"), true, "selected GPU controls the rendered processes");
      assert.equal(await evaluate("document.querySelector('#node-dialog').scrollTop"), scroll, "refresh retains inspector scroll without scrolling to focus");
    }
    await evaluate("document.querySelector('#node-detail [data-pin-node]').focus({preventScroll:true})");
    await refreshWithFixture();
    assert.equal(await evaluate("document.activeElement.dataset.pinNode"), initial.host, "refresh retains the same inspector pin identity");
    assert.equal(await evaluate("document.querySelector('#node-dialog').scrollTop"), scroll);
    await evaluate("document.querySelector('#close-detail').focus({preventScroll:true})");
    await refreshWithFixture();
    assert.equal(await evaluate("document.activeElement.id"), "close-detail", "refresh does not steal focus from outside the replaced detail content");
    await evaluate("{const filter=document.querySelector('#detail-gpu-filter');filter.value='';filter.dispatchEvent(new Event('change',{bubbles:true}));}");
    assert.equal(await evaluate("document.activeElement.id"), "close-detail", "an unfocused programmatic filter change does not steal focus either");
    await evaluate("document.querySelector('#detail-gpu-filter').focus({preventScroll:true})");
    await refreshWithFixture();
    assert.equal(await evaluate("document.activeElement.id"), "detail-gpu-filter");
    assert.equal(await evaluate("document.querySelector('#detail-gpu-filter').value"), "", "all-GPU selection also survives refresh");
  } finally {
    await evaluate(`{const filter=document.querySelector('#detail-gpu-filter');filter.value=${JSON.stringify(initial.gpu)};filter.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#node-dialog').scrollTop=${initial.scroll};delete window.previousDetailFilter;}`);
  }
}

export async function checkDetailDCGM({evaluate, trigger, overview, capture}) {
  const original = overview || await readOverview(evaluate);
  const initial = await evaluate("({host:document.querySelector('#node-detail [data-pin-node]').dataset.pinNode,gpu:document.querySelector('#detail-gpu-filter').value,scroll:document.querySelector('#node-dialog').scrollTop})");
  const base = original.hosts.find(host => host.name === initial.host);
  const host = {...base, status:"online", gpu_provider:"dcgm", gpus:[
    {...base.gpus[0], index:0, name:'GPU <img src=x onerror="throw 1">', utilization:0, processes_available:false, processes:[], dcgm:{
      sm_active_percent:0, sm_occupancy_percent:37.5, tensor_active_percent:null, dram_active_percent:12.5,
      pcie_rx_bytes_s:0, pcie_tx_bytes_s:1200000, nvlink_rx_bytes_s:null,
      memory_temperature:0, sm_clock_mhz:1410, memory_clock_mhz:0,
      ecc_sbe_volatile:0, ecc_dbe_volatile:null, xid_last:0,
    }},
    {...base.gpus[1], index:1, processes_available:true, processes:[{pid:4242,user:'fixture<&>"',name:'/usr/bin/python',memory_mb:0}], dcgm:null},
  ]};
  const apply = async next => {
    await setOverviewFixture(evaluate, {...original, hosts:[next]});
    await refresh(evaluate, trigger);
  };
  const select = async gpu => evaluate(`{const filter=document.querySelector('#detail-gpu-filter');filter.value=${JSON.stringify(gpu)};filter.dispatchEvent(new Event('change',{bubbles:true}));}`);
  const processText = () => evaluate("document.querySelector('#detail-gpu-filter').closest('section').textContent");
  try {
    await apply(host);
    await select("");
    assert.match(await evaluate("document.querySelector('.detail-facts').textContent"), /GPU 采集DCGM/);
    assert.equal(await evaluate("document.querySelectorAll('.detail-dcgm').length"), 2);
    assert.equal(await evaluate("document.querySelector('#node-grid .gpu-source-badge').textContent"), "DCGM");
    assert.deepEqual(await evaluate("Object.fromEntries(Array.from(document.querySelector('.detail-dcgm-grid').children).map(el=>[el.querySelector('dt').textContent,el.querySelector('dd').textContent]))"), {
      "SM 活跃率 (%)":"0.0", "SM 占用率 (%)":"37.5", "Tensor 活跃率 (%)":"—", "DRAM 活跃率 (%)":"12.5",
      "PCIe 接收 (B/s)":"0", "PCIe 发送 (B/s)":"1,200,000", "NVLink 接收 (B/s)":"—", "NVLink 发送 (B/s)":"—",
      "显存温度 (°C)":"0.0", "SM 时钟 (MHz)":"1,410", "显存时钟 (MHz)":"0", "ECC SBE（volatile 累计次数）":"0", "ECC DBE（volatile 累计次数）":"—", "最近 XID（错误码）":"0",
    });
    assert.equal(await evaluate("Array.from(document.querySelectorAll('.detail-dcgm-grid')[1].querySelectorAll('dd')).every(el=>el.textContent==='—')"), true, "null metrics remain unknown, never zero");
    assert.match(await evaluate("document.querySelector('.detail-dcgm-note').textContent"), /当前快照，不纳入现有历史使用率统计/);
    assert.match(await evaluate("document.querySelector('.detail-dcgm-note').textContent"), /volatile 累计计数.*错误码.*非采样间隔内次数/);
    assert.equal(await evaluate("document.querySelector('#node-detail img, #node-grid img')===null"), true, "GPU names stay escaped in real DOM rendering");
    assert.equal(await evaluate("Array.from(document.querySelectorAll('.detail-dcgm')).every(el=>el.scrollWidth<=el.clientWidth)"), true, "advanced metrics fit the inspector at the current viewport");
    const layout = await evaluate("({viewport:innerWidth,page:document.documentElement.scrollWidth,summary:document.querySelector('#summary').scrollWidth,summaryWidth:document.querySelector('#summary').clientWidth})");
    assert.ok(layout.page<=layout.viewport && layout.summary<=layout.summaryWidth, `DCGM partial-coverage summary must not overflow: ${JSON.stringify(layout)}`);
    assert.equal(await evaluate("document.querySelector('.detail-gpu-header small').textContent"), "进程信息不可读取");
    assert.match(await processText(), /已知 1 条进程记录（部分可读）/);
    assert.match(await evaluate("document.querySelector('#node-grid .node-users').textContent"), /已知 1 个进程 · 1 位用户（部分可读）/);
    assert.match(await evaluate("document.querySelectorAll('.summary-card')[3].textContent"), /1位（部分可读）/);
    assert.equal(await evaluate("document.querySelector('#process-count').textContent"), "1（部分可读）");
    await select("0");
    assert.match(await processText(), /进程信息不可读取/);
    assert.doesNotMatch(await processText(), /暂无可读取|python/);
    await select("1");
    assert.match(await processText(), /python/);
    assert.doesNotMatch(await processText(), /进程信息不可读取/);
    if (capture) {
      await evaluate("document.querySelector('#node-dialog').scrollTop=0");
      await capture();
    }
    // Refresh the expanded DCGM layout too, not only the legacy layout.
    await checkDetailFocus({evaluate, trigger});
    await select("");
    await apply({...host, gpus:host.gpus.map(gpu=>({...gpu,processes_available:false}))});
    assert.equal(await evaluate("document.querySelector('#process-count').textContent"), "—");
    assert.match(await evaluate("document.querySelector('#process-rows').textContent"), /进程信息不可读取/);
    assert.match(await evaluate("document.querySelectorAll('.summary-card')[3].textContent"), /—位.*进程信息不可读取/);
    assert.equal(await evaluate("document.querySelector('#node-grid .node-users').textContent"), "进程信息不可读取");
    for (const provider of ["nvidia-smi", undefined]) {
      const legacy = {...host};
      if (provider) legacy.gpu_provider = provider; else delete legacy.gpu_provider;
      await apply(legacy);
      assert.equal(await evaluate("document.querySelector('.detail-dcgm')===null && document.querySelector('#node-grid .gpu-source-badge')===null"), true);
      assert.match(await evaluate("document.querySelector('.detail-facts').textContent"), /GPU 采集NVIDIA-SMI/);
    }
    for (const status of ["offline", "pending"]) {
      await apply(host);
      await evaluate("document.querySelector('#detail-gpu-filter').focus({preventScroll:true})");
      await apply({...host, status});
      assert.equal(await evaluate("document.querySelector('#node-dialog').open && document.querySelector('.detail-offline')!==null"), true);
      assert.equal(await evaluate("document.querySelector('.detail-dcgm, .detail-gpu, .detail-system-grid, #detail-gpu-filter')===null"), true, "stale advanced and base metrics are hidden offline/pending");
      assert.equal(await evaluate("document.querySelector('#connection-error').hidden"), true);
    }
  } finally {
    await restoreOverviewFixture(evaluate);
    await refresh(evaluate, trigger);
    await select(initial.gpu);
    await evaluate(`document.querySelector('#node-dialog').scrollTop=${initial.scroll}`);
  }
}

async function standalone() {
  let chrome, socket;
  const pending = new Map();
  try {
    chrome = spawn(process.env.CHROME || "google-chrome", ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-first-run", "--no-proxy-server", "--remote-debugging-port=0", `--user-data-dir=${mkdtempSync(join(tmpdir(), "hqlab-detail-"))}`, "about:blank"], {stdio:["ignore", "pipe", "pipe"]});
    const debuggerURL = await new Promise((resolve, reject) => {
      let output = "";
      const timeout = setTimeout(() => reject(new Error(`Chrome startup timed out: ${output}`)), 15000);
      chrome.on("error", error => {clearTimeout(timeout); reject(error);});
      chrome.on("exit", code => {clearTimeout(timeout); reject(new Error(`Chrome exited: ${code}`));});
      chrome.stderr.on("data", chunk => {
        output += chunk;
        const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) {clearTimeout(timeout); resolve(match[1]);}
      });
    });
    socket = new WebSocket(debuggerURL);
    await new Promise((resolve, reject) => {socket.addEventListener("open", resolve, {once:true}); socket.addEventListener("error", reject, {once:true});});
    let nextID = 0, session;
    const exceptions = [];
    socket.addEventListener("message", event => {
      const message = JSON.parse(event.data);
      if (pending.has(message.id)) {
        const {resolve, reject, timeout} = pending.get(message.id); pending.delete(message.id); clearTimeout(timeout);
        if (message.error) reject(new Error(JSON.stringify(message.error))); else resolve(message.result);
      }
      if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails);
    });
    function cdp(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++nextID;
        const timeout = setTimeout(() => {pending.delete(id); reject(new Error(`CDP timeout: ${method}`));}, 15000);
        pending.set(id, {resolve, reject, timeout});
        socket.send(JSON.stringify({id, method, params, ...(session ? {sessionId:session} : {})}));
      });
    }
    const {targetId} = await cdp("Target.createTarget", {url:"about:blank"});
    ({sessionId:session} = await cdp("Target.attachToTarget", {targetId, flatten:true}));
    await cdp("Runtime.enable"); await cdp("Page.enable");
    await cdp("Emulation.setDeviceMetricsOverride", {width:1024,height:700,deviceScaleFactor:1,mobile:false});
    const evaluate = async expression => {
      const response = await cdp("Runtime.evaluate", {expression, awaitPromise:true, returnByValue:true});
      if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
      return response.result.value;
    };
    const source = process.env.MONITOR_TEST_SOURCE === "1";
    const asset = name => readFileSync(new URL(`../${source ? "public" : "dist"}/${name}`, import.meta.url), "utf8");
    let app;
    if (source) {
      const {build} = await import("vite");
      const bundles = await build({configFile:false, envDir:false, envPrefix:[], publicDir:false, logLevel:"silent", build:{write:false, minify:false, target:"es2022", lib:{entry:fileURLToPath(new URL("../src/app.ts", import.meta.url)), formats:["iife"], name:"Monitor"}}});
      app = bundles[0].output.find(output => output.type === "chunk").code;
    } else app = asset("app.js");
    // Load the actual markup and CSS without resolving any URLs or starting a server.
    const html = asset("index.html").replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, "").replace(/<link\b[^>]*>/g, "")
      .replace("</head>", `<style>${asset("styles.css")}\n${asset("palettes.css")}</style></head>`);
    const {frameTree} = await cdp("Page.getFrameTree");
    await cdp("Page.setDocumentContent", {frameId:frameTree.frame.id, html});
    const now = Date.now() / 1000;
    const host = {name:"focus-fixture", address:"127.0.0.1", groups:[], status:"online", system:{}, checked_at:now, gpus:[0, 1].map(index => ({index,name:"GPU fixture",utilization:20,memory_total_mb:10000,memory_used_mb:2000,processes:[{pid:100+index,user:"fixture",name:"python",memory_mb:20}]}))};
    const overview = {hosts:[host], demo:true, interval:15, retention_days:30, server_time:now, last_cycle:1};
    const history = {aggregate:{}, points:[], stats:[], start:now-3600, end:now};
    await evaluate(`window.setInterval=(fn,ms)=>{if(ms===5000) window.overviewTimer=fn;return 0;};window.fetch=async path=>new Response(JSON.stringify(String(path).startsWith('/api/overview')?${JSON.stringify(overview)}:${JSON.stringify(history)}),{status:200});`);
    await evaluate(app);
    await evaluate("(async()=>{while(document.querySelector('#refresh-button').disabled) await new Promise(resolve=>setTimeout(resolve,10));document.querySelector('.gpu-tile[data-gpu=\"1\"]').click();})()");
    // Native dialog.close queues its event. Reopening in the same task must not
    // let that old event clear the newly selected node (no application globals).
    await evaluate(`(async()=>{
      const dialog=document.querySelector('#node-dialog');
      const closed=new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true}));
      dialog.close();
      document.querySelector('.gpu-tile[data-gpu="1"]').click();
      await closed;
      if(!dialog.open) throw new Error('Inspector did not reopen');
    })()`);
    await checkDetailFocus({evaluate, trigger:"window.overviewTimer()"});
    // Invoke the real registered five-second timer, awaiting its refresh without
    // wall-clock waits. The shared test above also runs in the full browser suite.
    for (const [width, height] of [[1440, 1100], [1024, 700], [390, 700]]) {
      await cdp("Emulation.setDeviceMetricsOverride", {width,height,deviceScaleFactor:1,mobile:width < 500});
      await checkDetailFocus({evaluate, trigger:"window.overviewTimer()"});
      await checkDetailDCGM({evaluate, trigger:"window.overviewTimer()", overview});
    }
    for (const status of ['offline', 'pending']) {
      await evaluate("document.querySelector('#detail-gpu-filter').focus({preventScroll:true})");
      await setOverviewFixture(evaluate, {...overview, hosts:[{...host,status}]});
      try {
        await refresh(evaluate, "window.overviewTimer()");
        assert.equal(await evaluate("document.querySelector('#node-dialog').open"), true);
        assert.equal(await evaluate("document.querySelector('#connection-error').hidden"), true, 'removing a focused filter on offline transition is not a rendering error');
        assert.equal(await evaluate("document.querySelector('#detail-gpu-filter')===null && document.querySelectorAll('.detail-gpu').length===0"), true);
        assert.match(await evaluate("document.querySelector('.detail-offline').textContent"), status==='offline' ? /节点当前离线/ : /正在等待采集/);
      } finally {await restoreOverviewFixture(evaluate);}
      await refresh(evaluate, "window.overviewTimer()");
      assert.equal(await evaluate("document.querySelector('#detail-gpu-filter').value"), '1', 'GPU selection returns on online recovery');
    }
    assert.deepEqual(exceptions, [], "no uncaught browser exceptions");
    console.log("PASS: inspector filter/pin focus and scroll survive refresh and queued close/reopen; DCGM snapshot fields preserve zero/unknown, escaping and responsive layout; process coverage/filter states stay truthful; NVIDIA-SMI omits advanced fields; offline/pending hide stale metrics.");
  } finally {
    for (const {timeout} of pending.values()) clearTimeout(timeout);
    socket?.close(); chrome?.kill("SIGTERM");
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await standalone();
