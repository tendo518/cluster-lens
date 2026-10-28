// Real-DOM inspector refresh regressions, also reused by browser-smoke.mjs.
// Standalone: node tests/frontend-detail.mjs (Chrome/Chromium; no server/network).
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtempSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

export async function checkDetailFocus({evaluate, refresh = "refreshOverview()"}) {
  const initial = await evaluate("({gpu:state.detailGpu,host:state.detail,scroll:document.querySelector('#node-dialog').scrollTop})");
  assert.equal(await evaluate("document.querySelector('#node-dialog').open"), true);
  async function refreshWithFixture() {
    await evaluate(`(async()=>{
      if(state.refreshing) throw new Error('focus fixture requires an idle refresh');
      const original=window.fetch;
      const overview=structuredClone(state.overview), history=structuredClone(state.history);
      window.fetch=async path=>new Response(JSON.stringify(String(path).startsWith('/api/overview')?overview:history),{status:200});
      window.previousDetailFilter=document.querySelector('#detail-gpu-filter');
      try {await ${refresh};} finally {window.fetch=original;}
    })()`);
    assert.equal(await evaluate("window.previousDetailFilter!==document.querySelector('#detail-gpu-filter')"), true, "refresh really replaced the inspector controls");
  }
  try {
    await evaluate("{const filter=document.querySelector('#detail-gpu-filter');filter.focus({preventScroll:true});filter.value='1';filter.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#node-dialog').scrollTop=160;}");
    const scroll = await evaluate("document.querySelector('#node-dialog').scrollTop");
    assert.ok(scroll > 0, "exercise preservation of a genuinely scrolled inspector");
    for (let count = 0; count < 2; count++) {
      await refreshWithFixture();
      assert.equal(await evaluate("document.activeElement.id"), "detail-gpu-filter", "overview refresh retains GPU filter keyboard focus");
      assert.equal(await evaluate("document.querySelector('#detail-gpu-filter').value"), "1", "refresh retains the selected GPU");
      assert.equal(await evaluate("state.detailGpu"), "1");
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
    await evaluate(`state.detailGpu=${JSON.stringify(initial.gpu)};renderDetail();document.querySelector('#node-dialog').scrollTop=${initial.scroll};delete window.previousDetailFilter;`);
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
    const asset = name => readFileSync(new URL(`../monitor/static/${name}`, import.meta.url), "utf8");
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
    await evaluate(asset("app.js"));
    await evaluate("openDetail('focus-fixture','1')");
    // Invoke the real registered five-second timer, awaiting its refresh without
    // wall-clock waits. The shared test above also runs in the full browser suite.
    for (const [width, height] of [[1440, 1100], [1024, 700], [390, 700]]) {
      await cdp("Emulation.setDeviceMetricsOverride", {width,height,deviceScaleFactor:1,mobile:width < 500});
      await checkDetailFocus({evaluate, refresh:"(async()=>{let pending;const original=refreshOverview;refreshOverview=(...args)=>(pending=original(...args));try{window.overviewTimer();await pending;}finally{refreshOverview=original;}})()"});
    }
    assert.deepEqual(exceptions, [], "no uncaught browser exceptions");
    console.log("PASS: timer-driven overview refresh preserves inspector filter/pin focus, selected GPU and scroll; no focus stealing.");
  } finally {
    for (const {timeout} of pending.values()) clearTimeout(timeout);
    socket?.close(); chrome?.kill("SIGTERM");
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await standalone();
