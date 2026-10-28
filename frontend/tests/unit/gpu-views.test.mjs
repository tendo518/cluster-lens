import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parseHTML } from "linkedom";
import { createState } from "../../src/state.ts";
import { createDetailView } from "../../src/detail.ts";
import { createNodesView } from "../../src/nodes.ts";
import { createOverviewView } from "../../src/overview.ts";
import { createProcessesView } from "../../src/processes.ts";
import { allProcesses, processCoverage } from "../../src/selectors.ts";

const gpu = (index, extra = {}) => ({
  index,
  name: "GPU fixture",
  driver: "570.0",
  processes: [],
  ...extra,
});
const process = (pid = 123) => ({
  pid,
  user: "alice",
  name: "/usr/bin/python",
  memory_mb: 100,
});
function fixture(t, gpus = [gpu(0)]) {
  const original = globalThis.document;
  const { document, window } = parseHTML(
    readFileSync(new URL("../../public/index.html", import.meta.url), "utf8"),
  );
  // LinkeDOM parses real markup/selectors but does not implement assigning
  // select.value. Add only that DOM operation; real focus/layout use Chrome.
  const prototype = window.HTMLSelectElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  Object.defineProperty(prototype, "value", {
    configurable: true,
    get() {
      return (this.querySelector("option[selected]") || this.options[0])?.value;
    },
    set(value) {
      for (const option of this.options)
        option.selected = option.value === value;
    },
  });
  globalThis.document = document;
  t.after(() => {
    globalThis.document = original;
    Object.defineProperty(prototype, "value", descriptor);
  });
  const state = createState();
  const host = {
    name: "gpu-node",
    address: "127.0.0.1",
    groups: [],
    status: "online",
    system: {},
    gpus,
  };
  state.overview = {
    hosts: [host],
    server_time: 1000,
    interval: 15,
    stale_after: 60,
    retention_days: 30,
    mode: "push",
  };
  state.detail = host.name;
  const nodes = createNodesView(state, () => {});
  const detail = createDetailView(state, nodes.pinButton);
  const processes = createProcessesView(state);
  const overview = createOverviewView(state, () => "", {
    ...nodes,
    ...processes,
    ...detail,
  });
  return {
    state,
    host,
    document,
    ...nodes,
    ...detail,
    ...processes,
    ...overview,
    query: (selector) => document.querySelector(selector),
    text: (selector) => document.querySelector(selector).textContent,
  };
}
function metricValues(root) {
  return Object.fromEntries(
    [...root.querySelectorAll(".detail-dcgm-grid > div")].map((field) => [
      field.querySelector("dt").textContent,
      field.querySelector("dd").textContent,
    ]),
  );
}
const processSection = (f) => f.query("#detail-gpu-filter").closest("section");
const userSummary = (f) => f.document.querySelectorAll(".summary-card")[3];

test("DCGM detail renders the fixed snapshot fields, explicit units, zero and unknown without scaling ratios", (t) => {
  const f = fixture(t, [
    gpu(0, {
      dcgm: {
        sm_active_percent: 0,
        sm_occupancy_percent: 37.5,
        tensor_active_percent: null,
        dram_active_percent: 12.5,
        pcie_rx_bytes_s: 0,
        pcie_tx_bytes_s: 1200000,
        nvlink_rx_bytes_s: null,
        // nvlink_tx_bytes_s deliberately absent
        memory_temperature: 0,
        sm_clock_mhz: 1410,
        memory_clock_mhz: 0,
        ecc_sbe_volatile: 0,
        ecc_dbe_volatile: null,
        xid_last: 0,
        arbitrary_exporter_label: 99,
      },
    }),
  ]);
  f.host.gpu_provider = "dcgm";
  f.renderDetail();
  assert.deepEqual(metricValues(f.query(".detail-gpu")), {
    "SM 活跃率 (%)": "0.0",
    "SM 占用率 (%)": "37.5",
    "Tensor 活跃率 (%)": "—",
    "DRAM 活跃率 (%)": "12.5",
    "PCIe 接收 (B/s)": "0",
    "PCIe 发送 (B/s)": "1,200,000",
    "NVLink 接收 (B/s)": "—",
    "NVLink 发送 (B/s)": "—",
    "显存温度 (°C)": "0.0",
    "SM 时钟 (MHz)": "1,410",
    "显存时钟 (MHz)": "0",
    "ECC SBE（volatile 累计次数）": "0",
    "ECC DBE（volatile 累计次数）": "—",
    "最近 XID（错误码）": "0",
  });
  assert.match(f.text(".detail-facts"), /GPU 采集DCGM/);
  assert.match(f.text(".detail-facts"), /GPU 驱动570.0/);
  assert.match(f.text(".detail-dcgm-note"), /硬件、权限及 exporter 启用的字段/);
  assert.match(f.text(".detail-dcgm-note"), /当前快照，不纳入现有历史使用率/);
  assert.match(
    f.text(".detail-dcgm-note"),
    /最近 XID 为错误码，均非采样间隔内次数/,
  );
  assert.doesNotMatch(f.text("#node-detail"), /arbitrary_exporter_label/);
  f.renderNodes();
  assert.equal(f.text(".gpu-source-badge"), "DCGM");
  f.host.status = "offline";
  f.renderNodes();
  assert.equal(
    f.text(".gpu-source-badge"),
    "DCGM",
    "configured source is not a stale measurement",
  );
});

test("legacy and explicit NVIDIA-SMI omit advanced metrics even with a DCGM payload and preserve known-empty process states", (t) => {
  const f = fixture(t, [gpu(0, { dcgm: { sm_active_percent: 80 } })]);
  for (const provider of [undefined, "nvidia-smi"]) {
    f.host.gpu_provider = provider;
    f.renderOverview();
    f.renderDetail();
    assert.equal(f.query(".detail-dcgm"), null);
    assert.match(f.text(".detail-facts"), /GPU 采集NVIDIA-SMI/);
    assert.equal(f.query(".gpu-source-badge"), null);
    assert.equal(f.text(".detail-gpu-header small"), "0 条进程记录");
    assert.match(f.text(".node-users"), /0 个进程 · 0 位用户/);
    assert.equal(f.text("#process-count"), "0");
    assert.equal(f.query(".process-coverage-hint"), null);
    assert.match(userSummary(f).textContent, /0位/);
  }
  f.host.gpus = [];
  f.renderNodes();
  f.renderDetail();
  assert.match(f.text(".node-model"), /GPU 指标不可读取/);
  assert.doesNotMatch(f.text("#node-detail"), /安装 NVIDIA 驱动及 nvidia-smi/);
});

test("DCGM absent/null/invalid measurements render unknown fields instead of measured zero", (t) => {
  const f = fixture(t, [gpu(0)]);
  f.host.gpu_provider = "dcgm";
  for (const metrics of [
    undefined,
    null,
    {},
    { sm_active_percent: NaN, pcie_rx_bytes_s: Infinity },
  ]) {
    f.host.gpus[0].dcgm = metrics;
    f.renderDetail();
    const values = Object.values(metricValues(f.query(".detail-gpu")));
    assert.equal(values.length, 14);
    assert.ok(values.every((value) => value === "—"));
  }
});

test("offline and pending inspectors hide stale DCGM, base metrics and processes", (t) => {
  const f = fixture(t, [
    gpu(0, { dcgm: { sm_active_percent: 73 }, processes: [process()] }),
  ]);
  f.host.gpu_provider = "dcgm";
  f.host.system.cpu_percent = 99;
  f.renderDetail();
  assert.ok(f.query(".detail-dcgm"));
  for (const status of ["offline", "pending"]) {
    f.host.status = status;
    f.renderOverview();
    f.renderDetail();
    assert.ok(f.query(".detail-offline"));
    assert.equal(f.query(".detail-section"), null);
    assert.equal(f.query(".detail-gpu"), null);
    assert.equal(f.query(".detail-dcgm"), null);
    assert.equal(f.query("#detail-gpu-filter"), null);
    assert.doesNotMatch(f.text("#node-detail"), /SM 活跃率|python|99/);
    assert.equal(f.query(".process-coverage-hint"), null);
    assert.deepEqual(allProcesses(f.state.overview), []);
  }
});

test("process availability follows the selected GPUs, keeps legacy readability, and labels mixed known records as partial", (t) => {
  const f = fixture(t, [
    gpu(0, { processes_available: false, processes: [process(999)] }),
    gpu(1, { processes: [process()] }),
    gpu(2, { processes_available: true }),
  ]);
  f.host.gpu_provider = "dcgm";
  assert.deepEqual(processCoverage(f.host.gpus), {
    readable: 2,
    unavailable: 1,
  });
  assert.deepEqual(
    allProcesses(f.state.overview).map((p) => p.pid),
    [123],
  );
  f.renderDetail();
  assert.equal(f.text(".detail-gpu-header small"), "进程信息不可读取");
  assert.match(
    f.text(".detail-process-coverage"),
    /已知 1 条进程记录（部分可读）/,
  );
  assert.match(processSection(f).textContent, /python/);
  assert.doesNotMatch(processSection(f).textContent, /999/);
  for (const [selected, expected] of [
    ["0", /进程信息不可读取/],
    ["1", /python/],
    ["2", /暂无可读取的 GPU 进程/],
  ]) {
    f.state.detailGpu = selected;
    f.renderDetail();
    assert.match(processSection(f).textContent, expected);
    assert.equal(f.query(".detail-process-coverage"), null);
    if (selected !== "0")
      assert.doesNotMatch(processSection(f).textContent, /进程信息不可读取/);
  }
});

test("summary, cards and processes do not imply zero activity without process coverage, and keep filters/pagination on partial data", (t) => {
  const f = fixture(t, [gpu(0, { processes_available: false })]);
  f.renderOverview();
  assert.match(f.text(".node-users"), /进程信息不可读取/);
  assert.doesNotMatch(f.text(".node-users"), /0 个进程|0 位用户/);
  assert.match(userSummary(f).textContent, /—位.*进程信息不可读取/);
  assert.equal(f.text("#process-count"), "—");
  assert.equal(f.text("#user-strip b"), "—");
  assert.match(f.text("#process-rows"), /进程信息不可读取/);
  assert.doesNotMatch(f.text("#process-rows"), /当前暂无/);
  assert.equal(f.text("#process-footer"), "进程信息不可读取");
  assert.ok(f.query("#process-next").disabled);

  f.host.gpus.push(gpu(1));
  f.renderOverview();
  assert.match(userSummary(f).textContent, /—位.*部分 GPU 进程信息不可读取/);
  f.host.gpus[1].processes = [process()];
  f.host.gpus.push(gpu(2, { processes: [process()] }));
  f.renderOverview();
  assert.match(f.text(".node-users"), /已知 1 个进程 · 1 位用户（部分可读）/);
  assert.match(
    userSummary(f).textContent,
    /1位（部分可读）.*已知 1 个进程 · 2 条 GPU 关联/,
  );
  assert.equal(f.text("#process-count"), "1（部分可读）");
  assert.equal(f.document.querySelectorAll("#process-rows tr").length, 2);
  assert.match(
    f.text("#process-footer"),
    /已知 1 个进程 · 2 条 GPU 关联.*部分可读/,
  );
  for (const filter of [
    { user: "missing" },
    { user: "", processSearch: "not-found" },
  ]) {
    Object.assign(f.state, filter, { processPage: 5 });
    f.renderProcesses();
    assert.match(f.text("#process-rows"), /没有匹配的已知进程/);
    assert.match(f.text(".process-coverage-hint"), /部分 GPU 进程信息不可读取/);
    assert.match(f.text("#process-footer"), /已知 0 个进程.*部分可读/);
    assert.equal(f.state.processPage, 1);
  }
});

test("an unavailable DCGM source with no devices is not proof of zero processes", (t) => {
  const f = fixture(t, []);
  f.host.gpu_provider = "dcgm";
  f.renderOverview();
  f.renderDetail();
  assert.match(f.text(".node-users"), /进程信息不可读取/);
  assert.doesNotMatch(f.text(".node-users"), /0 个进程|0 位用户/);
  assert.match(userSummary(f).textContent, /—位.*进程信息不可读取/);
  assert.equal(f.text("#process-count"), "—");
  assert.match(processSection(f).textContent, /进程信息不可读取/);
});

test("detail, cards and process views escape untrusted text and ignore nonnumeric DCGM fields", (t) => {
  const unsafe = '<img src=x onerror="alert(1)">';
  const f = fixture(t, [
    gpu(unsafe, {
      name: unsafe,
      uuid: unsafe,
      driver: unsafe,
      dcgm: { sm_active_percent: unsafe, [unsafe]: 1 },
      processes: [
        { ...process(unsafe), user: unsafe, name: unsafe, type: unsafe },
      ],
    }),
  ]);
  Object.assign(f.host, {
    name: unsafe,
    address: unsafe,
    groups: [unsafe],
    warnings: [unsafe],
    gpu_provider: "dcgm",
  });
  f.host.system.os = unsafe;
  f.state.detail = unsafe;
  f.renderOverview();
  f.renderDetail();
  for (const selector of [
    "#node-detail",
    "#node-grid",
    "#processes",
    "#summary",
  ]) {
    assert.equal(f.query(selector).querySelector("img,script"), null, selector);
  }
  assert.match(f.text("#node-detail"), /<img src=x/);
  assert.equal(f.query("#node-detail [data-pin-node]").dataset.pinNode, unsafe);
  assert.equal(f.query("#node-grid [data-open-node]").dataset.openNode, unsafe);
  assert.equal(
    f.query("#detail-gpu-filter option[value]").nextElementSibling.value,
    unsafe,
  );
  assert.equal(metricValues(f.query(".detail-gpu"))["SM 活跃率 (%)"], "—");
  f.host.status = "offline";
  f.host.error = unsafe;
  f.renderDetail();
  assert.equal(f.query(".detail-offline img"), null);
  assert.match(f.text(".detail-offline"), /<img src=x/);
});
