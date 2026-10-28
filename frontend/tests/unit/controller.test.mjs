import assert from "node:assert/strict";
import test from "node:test";
import { createController } from "../../src/controller.ts";
import { createAuth, AuthError } from "../../src/auth.ts";
import { createState } from "../../src/state.ts";
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function fixture() {
  const nodes = new Map();
  const original = globalThis.document;
  globalThis.document = {
    querySelector(selector) {
      if (!nodes.has(selector))
        nodes.set(selector, {
          disabled: false,
          hidden: true,
          attributes: {},
          classList: { add() {}, remove() {} },
          setAttribute(name, value) {
            this.attributes[name] = value;
          },
        });
      return nodes.get(selector);
    },
  };
  const state = createState(),
    data = new Map();
  const storage = {
    getItem: (key) => data.get(key) || null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
  };
  const auth = createAuth({
    local: () => storage,
    session: () => storage,
    onSharedChange() {},
  });
  const history = [],
    overview = [],
    renders = [];
  const api = {
    overview() {
      const request = deferred();
      overview.push(request);
      return request.promise;
    },
    history(params) {
      const request = { ...deferred(), params };
      history.push(request);
      return request.promise;
    },
  };
  const controller = createController(state, auth, api, {
    renderOverview: () => renders.push("overview"),
    renderHistory: () => renders.push("history"),
    updateFreshness() {},
    restoreWorkspaceAnchor() {},
  });
  return {
    state,
    auth,
    history,
    overview,
    controller,
    nodes,
    renders,
    restore() {
      globalThis.document = original;
    },
  };
}
test("history responses, failures and busy state are owned by the newest request", async () => {
  const f = fixture();
  try {
    const first = f.controller.loadHistory();
    f.state.historyHost = "new";
    const second = f.controller.loadHistory();
    assert.equal(f.history[1].params.get("host"), "new");
    const newest = { start: 10, end: 20 };
    f.history[1].resolve(newest);
    await second;
    f.history[0].resolve({ start: 0, end: 10 });
    await first;
    assert.equal(f.state.history, newest);
    assert.deepEqual(f.renders, ["history"]);
    const stale = f.controller.loadHistory(),
      current = f.controller.loadHistory();
    f.history[2].reject(new Error("obsolete failure"));
    await stale;
    assert.equal(
      f.nodes.get("#chart-container").attributes["aria-busy"],
      "true",
    );
    assert.equal(f.nodes.get("#history-error").hidden, true);
    f.history[3].resolve(newest);
    await current;
    assert.equal(f.nodes.get("#export-button").disabled, false);
    assert.equal(
      f.nodes.get("#chart-container").attributes["aria-busy"],
      "false",
    );
  } finally {
    f.restore();
  }
});
test("history errors clear stale statistics; auth supersession does not publish error UI", async () => {
  const f = fixture();
  try {
    f.state.history = { start: 0, end: 10 };
    const failed = f.controller.loadHistory();
    f.history[0].reject(new Error("unavailable"));
    await failed;
    assert.equal(f.state.history, null);
    assert.equal(f.nodes.get("#history-error").hidden, false);
    assert.match(f.nodes.get("#history-error").textContent, /unavailable/);
    assert.equal(f.nodes.get("#export-button").disabled, true);
    const stale = f.controller.loadHistory();
    f.history[1].reject(new AuthError());
    await stale;
    assert.equal(f.nodes.get("#history-error").hidden, true);
    assert.deepEqual(f.renders, ["history"]);
  } finally {
    f.restore();
  }
});
test("overview single-flight and auth generation protect rendering and release controls", async () => {
  const f = fixture();
  try {
    const pending = f.controller.refreshOverview();
    await f.controller.refreshOverview();
    assert.equal(f.overview.length, 1);
    f.auth.beginLogin("new-login");
    f.overview[0].resolve({ hosts: [], server_time: 10, last_cycle: 1 });
    await pending;
    assert.deepEqual(f.renders, []);
    assert.equal(f.state.overview, null);
    assert.equal(f.state.refreshing, false);
    assert.equal(f.nodes.get("#refresh-button").disabled, false);
    const snapshot = { hosts: [], server_time: 10, last_cycle: 1 };
    f.state.overview = snapshot;
    const failed = f.controller.refreshOverview();
    f.overview[1].reject(new Error("offline"));
    await failed;
    assert.equal(f.state.overview, snapshot);
    assert.equal(f.state.connected, false);
    assert.match(f.nodes.get("#connection-error").textContent, /offline/);
  } finally {
    f.restore();
  }
});
