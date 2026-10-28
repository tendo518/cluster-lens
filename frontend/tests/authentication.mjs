// Persistent viewer authentication regressions against the isolated demo only.
import assert from "node:assert/strict";
import {setTimeout as sleep} from "node:timers/promises";
import {refresh} from "./browser-fixtures.mjs";

export async function checkAuthentication({base, cdp, evaluate, click, setInput, waitFor, token}) {
  const key = "hqlab-monitor-token", revisionKey = "hqlab-monitor-auth-revision";
  const dashboard = "!document.querySelector('#auth-dialog')?.open && document.querySelectorAll('#node-grid .node-card').length===8 && !!document.querySelector('.chart-primary') && !document.querySelector('#refresh-button').disabled";
  const storage = () => evaluate(`({local:localStorage.getItem(${JSON.stringify(key)}),session:sessionStorage.getItem(${JSON.stringify(key)})})`);
  async function reload(label, condition = dashboard) {
    const origin = await evaluate("performance.timeOrigin");
    await cdp("Page.reload");
    await waitFor(`performance.timeOrigin!==${origin} && (${condition})`, label);
  }
  async function login() {
    await setInput("#access-token", token);
    await evaluate("document.querySelector('#auth-form').requestSubmit()");
    await waitFor(dashboard, "authenticated dashboard");
  }

  assert.deepEqual(await storage(), {local:token, session:null}, "validated login persists only in localStorage");
  assert.equal(await evaluate("document.querySelector('#logout-button').hidden"), false);
  await reload("persistent authentication survives reload");

  // Simulate a pre-revision legacy session. A genuine old session has neither
  // a shared change marker nor the marker attached to newer session fallbacks.
  await evaluate(`localStorage.removeItem(${JSON.stringify(key)});localStorage.removeItem(${JSON.stringify(revisionKey)});sessionStorage.removeItem(${JSON.stringify(revisionKey)});sessionStorage.setItem(${JSON.stringify(key)},${JSON.stringify(token)})`);
  const fault = await cdp("Page.addScriptToEvaluateOnNewDocument", {source:"window.originalFetch=window.fetch;window.fetch=async()=>{throw new TypeError('simulated network outage')};"});
  try {
    await reload("legacy session survives a network outage", "document.querySelector('#connection-error')?.hidden===false");
    assert.deepEqual(await storage(), {local:null, session:token});
    assert.equal(await evaluate("document.querySelector('#auth-dialog').open"), false);
    await evaluate("window.fetch=window.originalFetch");
    await click("#refresh-button");
    await waitFor(dashboard, "legacy authentication recovers");
    assert.deepEqual(await storage(), {local:token, session:null}, "validated legacy token migrates and is removed from sessionStorage");
  } finally {await cdp("Page.removeScriptToEvaluateOnNewDocument", {identifier:fault.identifier});}

  // Network errors and 5xx responses must not discard a valid saved token.
  for (const failure of ["throw new TypeError('offline')", "return new Response('',{status:503})"]) {
    await evaluate(`window.authFixtureFetch=window.fetch;window.fetch=async()=>{${failure}};`);
    try {
      await refresh(evaluate);
      assert.equal(await evaluate("document.querySelector('#connection-error').hidden"), false);
      assert.deepEqual(await storage(), {local:token, session:null});
      assert.equal(await evaluate("document.querySelector('#auth-dialog').open"), false);
    } finally {await evaluate("window.fetch=window.authFixtureFetch;delete window.authFixtureFetch");}
    await refresh(evaluate);
  }

  // Exercise a real same-token login while an earlier refresh waits for a 401.
  // A token string comparison alone cannot protect this race: the revision and
  // in-memory generation must also change. No application state is patched.
  assert.equal(await evaluate(`(async()=>{
    const original=window.fetch;let resolve, intercepted=false;
    const previous=localStorage.getItem(${JSON.stringify(revisionKey)});
    window.fetch=(...args)=>{
      if(!intercepted && String(args[0]).startsWith('/api/overview')) {intercepted=true;return new Promise(done=>{resolve=done;});}
      return original(...args);
    };
    try {
      document.querySelector('#refresh-button').click();
      document.querySelector('#access-token').value=${JSON.stringify(token)};
      document.querySelector('#auth-form').requestSubmit();
      const deadline=performance.now()+10000;
      while(localStorage.getItem(${JSON.stringify(revisionKey)})===previous) {
        if(performance.now()>deadline) throw new Error('new login did not validate');
        await new Promise(done=>setTimeout(done,10));
      }
      resolve(new Response('',{status:401}));
      while(document.querySelector('#refresh-button').disabled) await new Promise(done=>setTimeout(done,10));
      return localStorage.getItem(${JSON.stringify(key)})===${JSON.stringify(token)} && !document.querySelector('#auth-dialog').open;
    } finally {window.fetch=original;}
  })()`), true, "late 401 cannot invalidate a newer validated login");

  // Rejected credentials never become persistent, including legacy sessions.
  await evaluate(`localStorage.removeItem(${JSON.stringify(key)});localStorage.removeItem(${JSON.stringify(revisionKey)});sessionStorage.setItem(${JSON.stringify(key)},'invalid-legacy-token')`);
  await reload("invalid legacy credentials require login", "document.querySelector('#auth-dialog')?.open");
  assert.deepEqual(await storage(), {local:null, session:null});
  await login();
  await evaluate(`localStorage.setItem(${JSON.stringify(key)},'invalid-persistent-token')`);
  await reload("invalid persistent credentials require login", "document.querySelector('#auth-dialog')?.open");
  assert.deepEqual(await storage(), {local:null, session:null});
  assert.equal(await evaluate("document.querySelector('#logout-button').hidden"), true);
  await login();

  // Quota / disabled writes must not prevent a successful in-memory login.
  await click("#logout-button");
  await waitFor("document.querySelector('#auth-dialog')?.open", "logout before blocked-storage login");
  const blocked = await cdp("Page.addScriptToEvaluateOnNewDocument", {source:`{
    const original=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key,value){
      if(this===localStorage && key===${JSON.stringify(key)}) throw new DOMException('quota','QuotaExceededError');
      return original.call(this,key,value);
    };
  }`});
  try {
    await reload("storage failure still offers login", "document.querySelector('#auth-dialog')?.open");
    await login();
    assert.deepEqual(await storage(), {local:null, session:token});
    assert.match(await evaluate("document.querySelector('#toast').textContent"), /无法持久保存/);
  } finally {await cdp("Page.removeScriptToEvaluateOnNewDocument", {identifier:blocked.identifier});}
  await reload("fallback session migrates once storage recovers");
  assert.deepEqual(await storage(), {local:token, session:null});

  // Independent tabs have no sessionStorage credentials but share persistence.
  const tabs = [];
  async function openTab() {
    const {targetId} = await cdp("Target.createTarget", {url:"about:blank"}, null);
    tabs.push(targetId);
    const {sessionId} = await cdp("Target.attachToTarget", {targetId, flatten:true}, null);
    await cdp("Runtime.enable", {}, sessionId); await cdp("Page.enable", {}, sessionId);
    const run = async expression => {
      const response = await cdp("Runtime.evaluate", {expression, awaitPromise:true, returnByValue:true}, sessionId);
      if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
      return response.result.value;
    };
    const wait = async (expression, label) => {
      const deadline = Date.now()+15000;
      while (Date.now()<deadline) {if (await run(expression)) return; await sleep(100);}
      throw new Error(`Timed out: ${label}`);
    };
    await cdp("Page.navigate", {url:base}, sessionId);
    await wait(dashboard, "new independent tab automatically authenticates");
    assert.equal(await run(`sessionStorage.getItem(${JSON.stringify(key)})`), null);
    return {targetId, run, wait};
  }
  try {
    const second = await openTab();
    await cdp("Target.closeTarget", {targetId:second.targetId}, null);
    tabs.splice(tabs.indexOf(second.targetId),1);
    const reopened = await openTab();
    // Logging out preserves preferences but signs out other open tabs too.
    await evaluate("localStorage.setItem('auth-test-preference','keep')");
    await click("#logout-button");
    await waitFor("document.querySelector('#auth-dialog')?.open && !document.querySelector('#refresh-button').disabled", "logout reloads without saved credentials");
    await reopened.wait("document.querySelector('#auth-dialog')?.open", "logout propagates to another tab");
    assert.deepEqual(await storage(), {local:null, session:null});
    assert.ok(await evaluate(`localStorage.getItem(${JSON.stringify(revisionKey)})`), "logout leaves only a non-secret change marker, even for tabs with pending legacy migration");
    assert.equal(await evaluate(`sessionStorage.getItem(${JSON.stringify(revisionKey)})`), null);
    assert.equal(await evaluate("localStorage.getItem('auth-test-preference')"), "keep");
    assert.equal(await evaluate("document.querySelectorAll('#node-grid .node-card:not(.skeleton)').length"), 0, "logout removes the previous dashboard data");
    await login();
    await reopened.wait(dashboard, "a new login is shared with the signed-out tab");
    await evaluate("localStorage.removeItem('auth-test-preference')");
  } finally {
    for (const targetId of tabs) await cdp("Target.closeTarget", {targetId}, null);
  }
  await reload("restore a clean dashboard after authentication checks");
}
