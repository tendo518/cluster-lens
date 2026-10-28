import assert from "node:assert/strict";
import test from "node:test";
import { createState } from "../../src/state.ts";
import {
  allProcesses,
  filteredHosts,
  pinnedHosts,
  processPage,
  historyParams,
  validRange,
} from "../../src/selectors.ts";
import { readPinnedHosts, savePinnedHosts } from "../../src/preferences.ts";
import { parseAppearance } from "../../src/appearance-preferences.ts";
import { chartSegments } from "../../src/charts.ts";
import { processRow } from "../../src/processes.ts";
import { escapeHTML, num, meter, bytes } from "../../src/format.ts";
const process = (pid, memory_mb = 1000 - pid) => ({
  pid,
  user: "pager",
  name: "/usr/bin/python",
  memory_mb,
});
function fixture() {
  const state = createState();
  const gpu = (index) => ({
    index,
    name: "GPU",
    memory_total_mb: 10000,
    processes: [],
  });
  const host = (name, status) => ({
    name,
    status,
    address: "127.0.0.1",
    groups: [],
    system: {},
    gpus: [gpu(0), gpu(1)],
  });
  state.overview = {
    hosts: [
      host("online", "online"),
      host("warning", "warning"),
      host("offline", "offline"),
      host("pending", "pending"),
    ],
  };
  for (const host of state.overview.hosts)
    host.gpus[0].processes = Array.from({ length: 137 }, (_, index) =>
      process(index),
    );
  state.overview.hosts[0].gpus[1].processes = [process(0, 1)];
  return state;
}
test("selectors omit offline processes and retain GPU associations separately from PIDs", () => {
  const rows = allProcesses(fixture().overview);
  assert.equal(rows.length, 275);
  assert.equal(new Set(rows.map((p) => `${p.host}:${p.pid}`)).size, 274);
  assert.equal(
    rows.filter((p) => p.host === "online" && p.pid === 0).length,
    2,
  );
  assert.ok(rows.every((p) => ["online", "warning"].includes(p.host)));
});
test("global filtering and memory ordering precede pagination; page clamps on live shrink", () => {
  const state = fixture();
  state.overview.hosts = state.overview.hosts.slice(0, 1);
  let page = processPage(state);
  assert.equal(page.pages, 6);
  assert.equal(page.visible.length, 25);
  assert.equal(page.rows.length, 138);
  state.processPage = 6;
  page = processPage(state);
  assert.equal(page.visible.length, 13);
  state.processSearch = "136";
  page = processPage(state);
  assert.equal(page.page, 1);
  assert.equal(page.visible[0].pid, 136);
  state.processSearch = "";
  state.processPageSize = 100;
  page = processPage(state);
  assert.equal(page.page, 2);
  assert.equal(page.visible.length, 38);
  state.overview.hosts[0].gpus[0].processes.length = 10;
  page = processPage(state);
  assert.equal(page.page, 1);
  assert.equal(page.visible.length, 11);
  state.user = "missing";
  page = processPage(state);
  assert.equal(page.pages, 0);
  assert.equal(page.page, 1);
  assert.deepEqual(page.visible, []);
});
test("pins retain order, only include managed hosts and ignore inventory filters", () => {
  const state = fixture();
  state.search = "no match";
  state.status = "offline";
  assert.deepEqual(filteredHosts(state), []);
  assert.deepEqual(
    pinnedHosts(state.overview, ["pending", "gone", "online"]).map(
      (h) => h.name,
    ),
    ["pending", "online"],
  );
  state.search = "127.0.0.1";
  assert.deepEqual(
    filteredHosts(state).map((h) => h.name),
    ["offline"],
  );
});
test("corrupt pin preferences, duplicate names, invalid types and blocked storage are safe", () => {
  for (const [raw, expected] of [
    ["broken", []],
    ["null", []],
    ["{}", []],
    [JSON.stringify([null, "one", "one", 42, "", "x".repeat(256)]), ["one"]],
  ]) {
    assert.deepEqual(
      readPinnedHosts(() => ({ getItem: () => raw })),
      expected,
    );
  }
  const blocked = () => {
    throw new Error("disabled storage getter");
  };
  assert.deepEqual(readPinnedHosts(blocked), []);
  assert.equal(savePinnedHosts(blocked, ["one"]), false);
});
test("appearance validation uses site defaults per field, including corrupt values", () => {
  const defaults = { theme: "dark", palette: "purple" };
  for (const raw of ["broken", "null", "{}", '"dark"'])
    assert.deepEqual(parseAppearance(raw, defaults), defaults);
  assert.deepEqual(
    parseAppearance('{"theme":"light","palette":"invalid"}', defaults),
    { theme: "light", palette: "purple" },
  );
  assert.deepEqual(
    parseAppearance('{"theme":"bogus","palette":"blue"}', defaults),
    { theme: "dark", palette: "blue" },
  );
  assert.deepEqual(
    parseAppearance('{"theme":"system","palette":"__proto__"}', defaults),
    { theme: "system", palette: "purple" },
  );
});
test("displayed history bounds are exported rather than drifting with the clock", () => {
  const state = createState();
  state.hours = 6;
  state.historyHost = "a & b";
  assert.deepEqual(Object.fromEntries(historyParams(state, 30000)), {
    start: "8400",
    end: "30000",
    host: "a & b",
  });
  state.history = { start: 100, end: 200 };
  state.custom = { start: 500, end: 1000 };
  assert.equal(historyParams(state, 30000, true).get("start"), "100");
  assert.equal(historyParams(state, 30000).get("start"), "500");
  assert.equal(validRange({ start: 100, end: 100 }, 1000), false);
  assert.equal(validRange({ start: 0, end: 32 * 86400 }, 40 * 86400), false);
  assert.equal(validRange({ start: NaN, end: 1000 }, 1000), false);
  assert.equal(validRange({ start: 0, end: 1061 }, 1000), false);
  assert.equal(validRange({ start: 0, end: 1060 }, 1000), true);
});
test("chart gaps stay gaps; genuine zero is still measured", () => {
  const points = [20, null, 0, 60, undefined, NaN, 100].map((gpu, ts) => ({
    ts,
    gpu,
  }));
  assert.deepEqual(
    chartSegments(
      points,
      "gpu",
      (n) => n,
      (n) => n,
    ),
    [
      [[0, 20]],
      [
        [2, 0],
        [3, 60],
      ],
      [[6, 100]],
    ],
  );
});
test("formatting distinguishes missing measurements from zero and escapes untrusted text", () => {
  assert.equal(num(null), "—");
  assert.equal(num(NaN), "—");
  assert.equal(num(0), "0");
  assert.equal(bytes(1024), "1.0 KiB");
  assert.match(meter(null), /aria-valuetext="无数据"/);
  assert.match(meter(120), /aria-valuenow="100"/);
  assert.equal(
    escapeHTML('<x a="b">&\''),
    "&lt;x a=&quot;b&quot;&gt;&amp;&#39;",
  );
  const row = processRow({
    ...process(1),
    host: "<img src=x onerror=alert(1)>",
    user: "<script>x</script>",
    gpu: 0,
    capacity: null,
  });
  assert.ok(!row.includes("<script>"));
  assert.ok(!row.includes("<img"));
  assert.match(row, /&lt;script&gt;/);
});
