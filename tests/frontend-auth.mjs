// Deterministic auth regressions: run the shipped app.js, with manually ordered
// fetch completions and storage events. No server, network, browser timers or sleeps.
// Run: node --test tests/frontend-auth.mjs
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {setImmediate as flush} from "node:timers/promises";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../monitor/static/app.js", import.meta.url), "utf8");
const TOKEN = "hqlab-monitor-token", REVISION = "hqlab-monitor-auth-revision";
const overview = {hosts:[], server_time:1234, last_cycle:1};
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
function storage(values = new Map()) {
  return {
    values, writes:[], blockedReads:false, blockedWrites:false,
    getItem(key) {if (this.blockedReads) throw new Error("storage disabled"); return values.get(key) ?? null;},
    setItem(key, value) {
      this.writes.push(["set", key, String(value)]);
      if (this.blockedWrites) throw new Error("quota exceeded");
      values.set(key, String(value));
    },
    removeItem(key) {this.writes.push(["remove", key]); values.delete(key);},
  };
}
function tab({shared = new Map(), session = new Map(), localBlocked = false, sessionBlocked = false, readsBlocked = false} = {}) {
  const localStorage = storage(shared), sessionStorage = storage(session);
  localStorage.blockedWrites = localBlocked; localStorage.blockedReads = readsBlocked;
  sessionStorage.blockedWrites = sessionBlocked; sessionStorage.blockedReads = readsBlocked;
  const nodes = new Map(), events = new Map(), requests = [];
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      open:false, hidden:true, value:"", dataset:{}, listeners:new Map(),
      classList:{add() {}, remove() {}, toggle() {}},
      addEventListener(type, fn) {this.listeners.set(type, fn);},
      setAttribute() {}, showModal() {this.open = true;}, close() {this.open = false;},
    });
    return nodes.get(selector);
  }
  const context = vm.createContext({
    localStorage, sessionStorage, URLSearchParams, console,
    document:{querySelector:node, querySelectorAll:() => [], addEventListener() {}},
    window:{addEventListener(type, fn) {if (!events.has(type)) events.set(type, []); events.get(type).push(fn);}},
    location:{reload() {context.reloads++;}}, reloads:0, renders:0,
    AbortSignal:{timeout:() => undefined},
    fetch(path, options) {const pending = deferred(); requests.push({path, options, ...pending}); return pending.promise;},
    ResizeObserver:class {observe() {}},
    setTimeout() {}, clearTimeout() {}, setInterval() {}, requestAnimationFrame() {},
  });
  const run = expression => vm.runInContext(expression, context);
  // The final expression in app.js is its real startup refresh promise.
  const startup = vm.runInContext(source, context);
  // Only unrelated rendering/history UI is stubbed; request, api, polling, login,
  // migration, logout and storage handlers all execute the actual shipped code.
  run("renderOverview=()=>{renders++;};loadHistory=async()=>{};updateFreshness=()=>{};restoreWorkspaceAnchor=()=>{};");
  function take() {assert.ok(requests.length, "expected an actual fetch"); return requests.shift();}
  function reply(pending = take(), status = 200, json = async () => overview) {
    pending.resolve({status, ok:status >= 200 && status < 300, json});
  }
  async function ready(status = 200) {reply(take(), status); await startup;}
  async function poll(status = 200) {const promise = run("refreshOverview()"); reply(take(), status); await promise;}
  async function login(token) {
    node("#access-token").value = token;
    const promise = node("#auth-form").listeners.get("submit")({preventDefault() {}});
    const validation = take();
    assert.equal(validation.options.headers.Authorization, `Bearer ${token}`);
    reply(validation);
    await flush(); // Drain promises; the successful login starts its normal refresh.
    reply(); await promise;
  }
  function deliver(key = TOKEN, newValue = null) {
    for (const fn of events.get("storage") || []) fn({key, newValue, storageArea:localStorage});
  }
  return {run, node, localStorage, sessionStorage, startup, requests, take, reply, ready, poll, login, deliver};
}

for (const origin of ["persistent", "legacy"]) {
  for (const action of ["logout", "different login", "same-token login"]) {
    for (const completion of ["success", "401", "JSON body"]) {
      test(`${origin}: late ${completion} after another tab's ${action}, before storage events`, async () => {
        const shared = new Map([["preference", "keep"]]);
        if (origin === "persistent") shared.set(TOKEN, "old-token");
        const a = tab({shared, session:origin === "legacy" ? new Map([[TOKEN, "old-token"]]) : new Map()});
        let pending = a.startup;
        if (origin === "persistent") {await a.ready(); pending = a.run("refreshOverview()");}
        const response = a.take(), body = deferred();
        if (completion === "JSON body") {a.reply(response, 200, () => body.promise); await flush();}
        const b = tab({shared}); await b.ready(origin === "persistent" ? 200 : 401);
        const expected = action === "logout" ? "" : action === "same-token login" ? "old-token" : "new-token";
        if (action === "logout") b.run("clearToken()"); else await b.login(expected);
        const storedAfterAction = [...shared], beforeRender = a.run("renders");
        a.localStorage.writes.length = 0;
        // No storage event has been delivered to A yet.
        if (completion === "JSON body") body.resolve(overview);
        else a.reply(response, completion === "401" ? 401 : 200);
        await pending;
        assert.deepEqual([...shared], storedAfterAction, "late response cannot restore/delete credentials or change the revision");
        assert.deepEqual(a.localStorage.writes, [], "polling never writes credentials, including when events are delayed");
        assert.equal(a.run("state.token"), expected);
        assert.equal(a.run("renders"), beforeRender, "discard data belonging to the old auth state");
        assert.equal(a.run("reloads"), 1, "notice the shared change without waiting for its event");
        assert.equal(a.sessionStorage.getItem(TOKEN), null, "discard the superseded legacy/fallback session");
        a.deliver(REVISION, "obsolete-revision"); a.deliver(TOKEN, "obsolete-token"); a.deliver(null);
        assert.equal(a.run("reloads"), 1, "queued obsolete events do not reverse the latest shared state");
        assert.equal(a.run("state.token"), expected);
        assert.equal(shared.get("preference"), "keep", "logout retains preferences");
      });
    }
  }
}

test("successful polls of an existing persistent token perform no storage writes", async () => {
  const a = tab({shared:new Map([[TOKEN, "saved"]])}); await a.ready();
  await a.poll(); await a.poll();
  assert.deepEqual(a.localStorage.writes, []);
});

test("a poll cannot restore a directly removed token, even without a revision/event", async () => {
  const shared = new Map([[TOKEN, "saved"]]), a = tab({shared}); await a.ready();
  const pending = a.run("refreshOverview()"); shared.delete(TOKEN);
  a.reply(); await pending;
  assert.equal(shared.has(TOKEN), false);
  assert.deepEqual(a.localStorage.writes, []);
  assert.equal(a.run("state.token"), "");
  a.deliver();
  assert.equal(a.run("reloads"), 1);
});

test("legacy migration waits through transient errors, validates once, then becomes read-only", async () => {
  const a = tab({session:new Map([[TOKEN, "legacy"]])});
  a.take().reject(new TypeError("offline")); await a.startup;
  assert.equal(a.sessionStorage.getItem(TOKEN), "legacy");
  assert.equal(a.localStorage.getItem(TOKEN), null);
  await a.poll(503);
  assert.equal(a.run("state.token"), "legacy");
  assert.deepEqual(a.localStorage.writes, []);
  await a.poll();
  assert.equal(a.localStorage.getItem(TOKEN), "legacy");
  assert.equal(a.sessionStorage.getItem(TOKEN), null);
  assert.equal(a.sessionStorage.getItem(REVISION), null);
  assert.ok(a.localStorage.getItem(REVISION));
  a.localStorage.writes.length = 0;
  await a.poll(); await a.poll(503);
  assert.deepEqual(a.localStorage.writes, []);
});

test("only a successful explicit login persists a typed token; refresh cannot promote a failed login", async () => {
  const a = tab(); await a.ready(401);
  assert.deepEqual(a.localStorage.writes, [], "anonymous 401 must not publish logout events to other tabs");
  a.node("#access-token").value = "typed";
  const failed = a.node("#auth-form").listeners.get("submit")({preventDefault() {}});
  assert.equal(a.localStorage.getItem(TOKEN), null, "not saved before validation");
  a.reply(a.take(), 503); await failed;
  await a.poll();
  assert.equal(a.run("state.token"), "typed");
  assert.deepEqual(a.localStorage.writes, [], "a later poll is not an explicit validated login");
  await a.login("typed");
  assert.equal(a.localStorage.getItem(TOKEN), "typed");
  assert.equal(a.sessionStorage.getItem(TOKEN), null);
  assert.equal(a.localStorage.writes.filter(([op, key]) => op === "set" && key === TOKEN).length, 1);
});

for (const token of ["original", "new-login"]) {
  test(`late 401 cannot invalidate a newer explicit login (${token})`, async () => {
    const a = tab({shared:new Map([[TOKEN, "original"]])}); await a.ready();
    const old = a.run("request('/api/history')");
    const rejected = assert.rejects(old, a.run("AuthError")), response = a.take();
    await a.login(token);
    const before = [...a.localStorage.values]; a.localStorage.writes.length = 0;
    a.reply(response, 401); await rejected;
    assert.equal(a.run("state.token"), token);
    assert.deepEqual([...a.localStorage.values], before);
    assert.deepEqual(a.localStorage.writes, []);
    assert.equal(a.node("#auth-dialog").open, false);
  });
}

test("401 rejects saved/legacy credentials, but network and gateway failures retain them", async () => {
  for (const origin of ["persistent", "legacy"]) {
    const a = tab(origin === "persistent" ? {shared:new Map([[TOKEN, "bad"]])} : {session:new Map([[TOKEN, "bad"]])});
    await a.ready(401);
    assert.equal(a.run("state.token"), "");
    assert.equal(a.localStorage.getItem(TOKEN), null);
    assert.equal(a.sessionStorage.getItem(TOKEN), null);
    assert.equal(a.node("#auth-dialog").open, true);
  }
  const a = tab({shared:new Map([[TOKEN, "valid"]])}); await a.ready();
  await a.poll(503);
  const pending = a.run("refreshOverview()"); a.take().reject(new TypeError("offline")); await pending;
  assert.equal(a.run("state.token"), "valid");
  assert.equal(a.localStorage.getItem(TOKEN), "valid");
  assert.deepEqual(a.localStorage.writes, []);
});

test("a failed migration is attempted once per page, with session fallback until reload", async () => {
  const shared = new Map(), session = new Map([[TOKEN, "legacy"]]);
  const a = tab({shared, session, localBlocked:true}); await a.ready();
  assert.equal(session.get(TOKEN), "legacy");
  a.localStorage.writes.length = 0; await a.poll(); await a.poll();
  assert.deepEqual(a.localStorage.writes, [], "do not retry credential writes on each poll");
  const reloaded = tab({shared, session}); await reloaded.ready();
  assert.equal(shared.get(TOKEN), "legacy"); assert.equal(session.has(TOKEN), false);
});

test("explicit login retains session/in-memory fallback and does not persist it via polling", async () => {
  for (const sessionBlocked of [false, true]) {
    const a = tab({localBlocked:true, sessionBlocked}); await a.ready(401);
    await a.login("fallback");
    assert.equal(a.run("state.token"), "fallback");
    assert.equal(a.localStorage.getItem(TOKEN), null);
    assert.equal(a.sessionStorage.getItem(TOKEN), sessionBlocked ? null : "fallback");
    assert.match(a.node("#toast").textContent, /无法持久保存/);
    a.localStorage.blockedWrites = false; a.localStorage.writes.length = 0;
    await a.poll();
    assert.deepEqual(a.localStorage.writes, [], "ordinary polling cannot promote an in-memory or session login");
  }
  const a = tab({localBlocked:true, sessionBlocked:true, readsBlocked:true}); await a.ready(401);
  await a.login("memory-only"); await a.poll();
  assert.equal(a.run("state.token"), "memory-only", "blocked reads/writes do not prevent in-memory use");
});

test("a fallback from before another tab's logout cannot migrate even after reload", async () => {
  const shared = new Map(), session = new Map();
  const a = tab({shared, session, localBlocked:true}); await a.ready(401); await a.login("fallback");
  const b = tab({shared}); await b.ready(401); b.run("clearToken()");
  const reloaded = tab({shared, session});
  assert.equal(reloaded.run("state.token"), "");
  const pending = reloaded.take();
  assert.equal(pending.options.headers.Authorization, undefined);
  reloaded.reply(pending, 401); await reloaded.startup;
  assert.deepEqual(reloaded.localStorage.writes, [], "an anonymous reload does not republish logout");
  assert.equal(session.has(TOKEN), false);
});

test("a new fallback login after a logout can migrate, until the next shared change", async () => {
  const shared = new Map([[REVISION, "prior-logout"]]), session = new Map();
  const a = tab({shared, session, localBlocked:true}); await a.ready(401); await a.login("fresh-fallback");
  assert.equal(session.get(REVISION), "prior-logout");
  const reloaded = tab({shared, session}); await reloaded.ready();
  assert.equal(shared.get(TOKEN), "fresh-fallback");
  assert.equal(session.has(TOKEN), false);
});

test("storage handlers synchronize logins, logout and clear, without echoing writes", async () => {
  const shared = new Map([[TOKEN, "old"]]), a = tab({shared}); await a.ready();
  shared.set(TOKEN, "new"); shared.set(REVISION, "new-login"); a.deliver(TOKEN, "old");
  assert.equal(a.run("state.token"), "new");
  shared.delete(TOKEN); shared.set(REVISION, "logout"); a.deliver(REVISION);
  assert.equal(a.run("state.token"), "");
  shared.clear(); a.deliver(null);
  assert.equal(a.run("state.token"), "");
  assert.deepEqual(a.localStorage.writes, []);
});
