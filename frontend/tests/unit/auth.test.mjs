// Deterministic production-module tests. No VM, DOM, timers or real network.
import assert from "node:assert/strict";
import { setImmediate as flush } from "node:timers/promises";
import test from "node:test";
import {
  createAuth,
  AuthError,
  TOKEN_STORAGE_KEY as TOKEN,
  AUTH_REVISION_KEY as REVISION,
} from "../../src/auth.ts";
import { createApi } from "../../src/api.ts";
const overview = { hosts: [], server_time: 1234, last_cycle: 1 };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function storage(values = new Map()) {
  return {
    values,
    writes: [],
    blockedReads: false,
    blockedWrites: false,
    getItem(key) {
      if (this.blockedReads) throw new Error("storage disabled");
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      this.writes.push(["set", key, String(value)]);
      if (this.blockedWrites) throw new Error("quota exceeded");
      values.set(key, String(value));
    },
    removeItem(key) {
      this.writes.push(["remove", key]);
      values.delete(key);
    },
  };
}
let revision = 0;
function tab({
  shared = new Map(),
  session = new Map(),
  localBlocked = false,
  sessionBlocked = false,
  readsBlocked = false,
} = {}) {
  const localStorage = storage(shared),
    sessionStorage = storage(session);
  localStorage.blockedWrites = localBlocked;
  localStorage.blockedReads = readsBlocked;
  sessionStorage.blockedWrites = sessionBlocked;
  sessionStorage.blockedReads = readsBlocked;
  const requests = [];
  let reloads = 0,
    renders = 0,
    required = false,
    persistent = true;
  const auth = createAuth({
    local: () => localStorage,
    session: () => sessionStorage,
    onSharedChange: () => reloads++,
    revision: () => String(++revision),
  });
  const api = createApi({
    auth,
    fetch(path, options) {
      const pending = deferred();
      requests.push({ path, options, ...pending });
      return pending.promise;
    },
    onUnauthorized: () => {
      required = true;
    },
  });
  function take() {
    assert.ok(requests.length, "expected actual fetch");
    return requests.shift();
  }
  function reply(pending = take(), status = 200, json = async () => overview) {
    pending.resolve({ status, ok: status >= 200 && status < 300, json });
  }
  async function acceptOverview() {
    const snapshot = auth.snapshot();
    try {
      await api.overview();
      auth.check(snapshot);
      auth.migrateLegacy(snapshot);
      renders++;
    } catch {
      /* UI handles the error. */
    }
  }
  async function ready(status = 200) {
    reply(take(), status);
    await startup;
  }
  async function poll(status = 200) {
    const pending = acceptOverview();
    reply(take(), status);
    await pending;
  }
  async function validateLogin(token) {
    const snapshot = auth.beginLogin(token);
    await api.overview();
    persistent = auth.completeLogin(snapshot);
    required = false;
    await acceptOverview();
  }
  async function login(token) {
    const pending = validateLogin(token),
      validation = take();
    assert.equal(validation.options.headers.Authorization, `Bearer ${token}`);
    reply(validation);
    await flush();
    reply();
    await pending;
  }
  const startup = acceptOverview();
  return {
    auth,
    api,
    localStorage,
    sessionStorage,
    requests,
    take,
    reply,
    ready,
    poll,
    login,
    validateLogin,
    acceptOverview,
    startup,
    get reloads() {
      return reloads;
    },
    get renders() {
      return renders;
    },
    get required() {
      return required;
    },
    get persistent() {
      return persistent;
    },
  };
}
for (const origin of ["persistent", "legacy"])
  for (const action of ["logout", "different login", "same-token login"])
    for (const completion of ["success", "401", "JSON body"]) {
      test(`${origin}: late ${completion} after another tab's ${action}, before storage events`, async () => {
        const shared = new Map([["preference", "keep"]]);
        if (origin === "persistent") shared.set(TOKEN, "old-token");
        const a = tab({
          shared,
          session:
            origin === "legacy" ? new Map([[TOKEN, "old-token"]]) : new Map(),
        });
        let pending = a.startup;
        if (origin === "persistent") {
          await a.ready();
          pending = a.acceptOverview();
        }
        const response = a.take(),
          body = deferred();
        if (completion === "JSON body") {
          a.reply(response, 200, () => body.promise);
          await flush();
        }
        const b = tab({ shared });
        await b.ready(origin === "persistent" ? 200 : 401);
        const expected =
          action === "logout"
            ? ""
            : action === "same-token login"
              ? "old-token"
              : "new-token";
        if (action === "logout") b.auth.clear();
        else await b.login(expected);
        const storedAfterAction = [...shared],
          beforeRender = a.renders;
        a.localStorage.writes.length = 0;
        if (completion === "JSON body") body.resolve(overview);
        else a.reply(response, completion === "401" ? 401 : 200);
        await pending;
        assert.deepEqual(
          [...shared],
          storedAfterAction,
          "late response cannot restore/delete credentials or change revision",
        );
        assert.deepEqual(
          a.localStorage.writes,
          [],
          "polling never writes credentials with delayed events",
        );
        assert.equal(a.auth.token, expected);
        assert.equal(a.renders, beforeRender, "discard data for old auth");
        assert.equal(a.reloads, 1, "notice shared change without its event");
        assert.equal(a.sessionStorage.getItem(TOKEN), null);
        a.auth.sync();
        a.auth.sync();
        a.auth.sync();
        assert.equal(
          a.reloads,
          1,
          "queued obsolete events do not reverse shared state",
        );
        assert.equal(a.auth.token, expected);
        assert.equal(shared.get("preference"), "keep");
      });
    }
test("persistent polling performs no storage writes", async () => {
  const a = tab({ shared: new Map([[TOKEN, "saved"]]) });
  await a.ready();
  await a.poll();
  await a.poll();
  assert.deepEqual(a.localStorage.writes, []);
});
test("poll cannot restore directly removed token even without revision/event", async () => {
  const shared = new Map([[TOKEN, "saved"]]),
    a = tab({ shared });
  await a.ready();
  const pending = a.acceptOverview();
  shared.delete(TOKEN);
  a.reply();
  await pending;
  assert.equal(shared.has(TOKEN), false);
  assert.deepEqual(a.localStorage.writes, []);
  assert.equal(a.auth.token, "");
  a.auth.sync();
  assert.equal(a.reloads, 1);
});
test("legacy migration waits through transient failures, validates once, then is read-only", async () => {
  const a = tab({ session: new Map([[TOKEN, "legacy"]]) });
  a.take().reject(new TypeError("offline"));
  await a.startup;
  assert.equal(a.sessionStorage.getItem(TOKEN), "legacy");
  assert.equal(a.localStorage.getItem(TOKEN), null);
  await a.poll(503);
  assert.equal(a.auth.token, "legacy");
  assert.deepEqual(a.localStorage.writes, []);
  await a.poll();
  assert.equal(a.localStorage.getItem(TOKEN), "legacy");
  assert.equal(a.sessionStorage.getItem(TOKEN), null);
  assert.equal(a.sessionStorage.getItem(REVISION), null);
  assert.ok(a.localStorage.getItem(REVISION));
  a.localStorage.writes.length = 0;
  await a.poll();
  await a.poll(503);
  assert.deepEqual(a.localStorage.writes, []);
});
test("only validated explicit login persists; polling cannot promote a failed login", async () => {
  const a = tab();
  await a.ready(401);
  assert.deepEqual(
    a.localStorage.writes,
    [],
    "anonymous 401 must not publish logout",
  );
  const failed = a.validateLogin("typed"),
    rejected = assert.rejects(failed);
  assert.equal(a.localStorage.getItem(TOKEN), null);
  a.reply(a.take(), 503);
  await rejected;
  await a.poll();
  assert.equal(a.auth.token, "typed");
  assert.deepEqual(a.localStorage.writes, []);
  await a.login("typed");
  assert.equal(a.localStorage.getItem(TOKEN), "typed");
  assert.equal(a.sessionStorage.getItem(TOKEN), null);
  assert.equal(
    a.localStorage.writes.filter(([op, key]) => op === "set" && key === TOKEN)
      .length,
    1,
  );
});
for (const token of ["original", "new-login"])
  test(`late 401 cannot invalidate newer explicit login (${token})`, async () => {
    const a = tab({ shared: new Map([[TOKEN, "original"]]) });
    await a.ready();
    const old = a.api.history(new URLSearchParams()),
      rejected = assert.rejects(old, AuthError),
      response = a.take();
    await a.login(token);
    const before = [...a.localStorage.values];
    a.localStorage.writes.length = 0;
    a.reply(response, 401);
    await rejected;
    assert.equal(a.auth.token, token);
    assert.deepEqual([...a.localStorage.values], before);
    assert.deepEqual(a.localStorage.writes, []);
    assert.equal(a.required, false);
  });
test("401 clears saved/legacy credentials; network/gateway failures retain them", async () => {
  for (const origin of ["persistent", "legacy"]) {
    const a = tab(
      origin === "persistent"
        ? { shared: new Map([[TOKEN, "bad"]]) }
        : { session: new Map([[TOKEN, "bad"]]) },
    );
    await a.ready(401);
    assert.equal(a.auth.token, "");
    assert.equal(a.localStorage.getItem(TOKEN), null);
    assert.equal(a.sessionStorage.getItem(TOKEN), null);
    assert.equal(a.required, true);
  }
  const a = tab({ shared: new Map([[TOKEN, "valid"]]) });
  await a.ready();
  await a.poll(503);
  const pending = a.acceptOverview();
  a.take().reject(new TypeError("offline"));
  await pending;
  assert.equal(a.auth.token, "valid");
  assert.equal(a.localStorage.getItem(TOKEN), "valid");
  assert.deepEqual(a.localStorage.writes, []);
});
test("failed migration attempted once per page, then session fallback until reload", async () => {
  const shared = new Map(),
    session = new Map([[TOKEN, "legacy"]]);
  const a = tab({ shared, session, localBlocked: true });
  await a.ready();
  assert.equal(session.get(TOKEN), "legacy");
  a.localStorage.writes.length = 0;
  await a.poll();
  await a.poll();
  assert.deepEqual(a.localStorage.writes, []);
  const reloaded = tab({ shared, session });
  await reloaded.ready();
  assert.equal(shared.get(TOKEN), "legacy");
  assert.equal(session.has(TOKEN), false);
});
test("explicit login keeps session/memory fallback without later poll promotion", async () => {
  for (const sessionBlocked of [false, true]) {
    const a = tab({ localBlocked: true, sessionBlocked });
    await a.ready(401);
    await a.login("fallback");
    assert.equal(a.auth.token, "fallback");
    assert.equal(a.localStorage.getItem(TOKEN), null);
    assert.equal(
      a.sessionStorage.getItem(TOKEN),
      sessionBlocked ? null : "fallback",
    );
    assert.equal(a.persistent, false);
    a.localStorage.blockedWrites = false;
    a.localStorage.writes.length = 0;
    await a.poll();
    assert.deepEqual(a.localStorage.writes, []);
  }
  const a = tab({
    localBlocked: true,
    sessionBlocked: true,
    readsBlocked: true,
  });
  await a.ready(401);
  await a.login("memory-only");
  await a.poll();
  assert.equal(a.auth.token, "memory-only");
});
test("pre-logout session fallback cannot migrate even on reload", async () => {
  const shared = new Map(),
    session = new Map();
  const a = tab({ shared, session, localBlocked: true });
  await a.ready(401);
  await a.login("fallback");
  const b = tab({ shared });
  await b.ready(401);
  b.auth.clear();
  const reloaded = tab({ shared, session });
  assert.equal(reloaded.auth.token, "");
  const pending = reloaded.take();
  assert.equal(pending.options.headers.Authorization, undefined);
  reloaded.reply(pending, 401);
  await reloaded.startup;
  assert.deepEqual(reloaded.localStorage.writes, []);
  assert.equal(session.has(TOKEN), false);
});
test("new fallback after logout can migrate until next shared change", async () => {
  const shared = new Map([[REVISION, "prior-logout"]]),
    session = new Map();
  const a = tab({ shared, session, localBlocked: true });
  await a.ready(401);
  await a.login("fresh-fallback");
  assert.equal(session.get(REVISION), "prior-logout");
  const reloaded = tab({ shared, session });
  await reloaded.ready();
  assert.equal(shared.get(TOKEN), "fresh-fallback");
  assert.equal(session.has(TOKEN), false);
});
test("shared synchronization of login/logout/clear never echoes writes", async () => {
  const shared = new Map([[TOKEN, "old"]]),
    a = tab({ shared });
  await a.ready();
  shared.set(TOKEN, "new");
  shared.set(REVISION, "new-login");
  a.auth.sync();
  assert.equal(a.auth.token, "new");
  shared.delete(TOKEN);
  shared.set(REVISION, "logout");
  a.auth.sync();
  assert.equal(a.auth.token, "");
  shared.clear();
  a.auth.sync();
  assert.equal(a.auth.token, "");
  assert.deepEqual(a.localStorage.writes, []);
});
test("storage property getters may throw without preventing memory-only authentication", () => {
  const auth = createAuth({
    local: () => {
      throw new Error("blocked");
    },
    session: () => {
      throw new Error("blocked");
    },
    onSharedChange: () => assert.fail(),
  });
  const snapshot = auth.beginLogin("memory");
  assert.equal(auth.completeLogin(snapshot), false);
  auth.check(snapshot);
  assert.equal(auth.token, "memory");
});
test("CSV body resolving after shared logout is rejected, not downloaded", async () => {
  const shared = new Map([[TOKEN, "valid"]]),
    a = tab({ shared });
  await a.ready();
  const body = deferred(),
    pending = a.api.exportCsv(new URLSearchParams()),
    rejected = assert.rejects(pending, AuthError);
  a.take().resolve({ ok: true, status: 200, blob: () => body.promise });
  await flush();
  shared.delete(TOKEN);
  shared.set(REVISION, "logout");
  body.resolve(new Blob(["sensitive"]));
  await rejected;
  assert.equal(a.reloads, 1);
  assert.deepEqual(a.localStorage.writes, []);
});
