# GPU 数据源：默认 NVIDIA-SMI，可选 DCGM

Node Reporter 的 GPU 数据源是**逐节点显式选择**的可选配置。省略 `gpu` 或使用 `{}` 均优先 / 默认采用 `nvidia-smi`，不会探测 DCGM 后自动切换；选择 DCGM 后也不会因故障把基础指标静默回退到 NVIDIA-SMI。

## 配置

应用公开节点 JSON 可带 `gpu`：

```json
[
  {"name": "node-a", "address": "192.0.2.11"},
  {"name": "node-b", "address": "192.0.2.12", "gpu": {"provider": "dcgm"}}
]
```

生成器只把 `gpu` 写到对应节点的私密上报配置，不写到中心配置；重复生成不轮换已有令牌。输入清单是 GPU 配置的权威来源：移除 `gpu` 恢复 NVIDIA-SMI 默认，不保留旧私密文件中的 DCGM 选择。

手工配置 Node Reporter 时，把同样的 `gpu` 对象放在 `version`、`host`、`url`、`token` 等字段旁边。完整 DCGM 配置如下：

```json
{
  "provider": "dcgm",
  "url": "http://127.0.0.1:9400/metrics",
  "timeout": 3,
  "processes": "nvidia-smi"
}
```

| 字段 | 规则 |
| --- | --- |
| `provider` | `nvidia-smi`（默认）或 `dcgm`；不存在 `auto` |
| `url` | 仅 DCGM；默认如上；必须是 HTTP、字面回环地址 `127.0.0.1` / `[::1]`、精确 `/metrics`，有效端口；不接受凭据、查询、片段、远端地址或重定向 |
| `timeout` | 仅 DCGM；整数 1—8 秒，默认 3；读取最多 2 MiB |
| `processes` | 仅 DCGM；`nvidia-smi`（默认）或 `none`；不是基础指标的回退开关 |

NVIDIA-SMI 配置只接受 `provider`，不接受 DCGM 专属选项。未知字段、错误类型、未知数据源在写入配置前拒绝。公开离线模型为 `contracts/gpu-config.schema.json`；URL、数据源专属字段等跨字段条件还由运行验证器检查。

## DCGM 的前提和边界

此实现通过**已经准备并持续运行的本机 `dcgm-exporter`** 的 Prometheus 文本接口读取 DCGM，而不是解析 `dcgmi` 的终端表格。Reporter 本身仍是 Python 3.6+ 标准库单文件，无须导入 libdcgm / Python 绑定，也无须 Prometheus 服务端。

- DCGM、驱动、exporter、监听地址、字段选择及其服务生命周期由部署方另行准备；本应用不下载或启动它们，不拉取容器，也不修改 GPU 配置。
- exporter 应只监听回环地址，限制本机访问。Reporter 不发送中心令牌给 exporter，忽略代理环境变量，不跟随重定向。
- 普通遥测不等于 profiling 支持。非数据中心 GPU 只有有限功能；profiling 字段依硬件、驱动、权限、计数器可用性而定，可能与 Nsight 冲突。应用不自动提权、改变驱动权限、执行诊断 / 压力测试或启用额外计数器。
- 本实现以**物理 GPU UUID** 标识设备，拒绝冲突身份 / 指标，不把 DCGM 内部编号当 UUID。MIG 实例不会与整卡混算，实例指标跳过并提示；当前没有 MIG 实例视图。
- 每次重新读取，不缓存或拿上一次结果构造新报告。带 Prometheus 毫秒时间戳的过期 / 未来字段作未知处理；多数 exporter 输出**不带字段时间戳**，此时报告时间表示读取 exporter 的时间，不能证明底层 DCGM 样本同样新鲜。部署方必须让 exporter / DCGM 的 watch 周期与上报周期匹配并监控服务健康。
- DCGM 读取失败时保留系统采集，GPU 列表为空并给出警告；这不等同于整台节点离线。没有成功报告达到 stale-after 后，中心仍按原规则判定离线。

基础 / 非 profiling 字段示例在 [dcgm-counters.csv](../examples/dcgm-counters.csv)。高级 profiling 字段默认注释；只在管理员已确认相应设备 / 权限支持后显式启用。这个文件仅供外部 exporter 配置参考，不会由应用部署或自动加载。

## 逐进程信息

DCGM exporter 的整卡指标不是逐进程列表。`processes: "nvidia-smi"` 会额外运行现有只读 `nvidia-smi` 采集，**仅按 UUID 合并进程**，不复制它的整卡利用率、显存或功耗，也不在 DCGM 失败时创建替代设备。来源和用途是明确的，不是隐式回退。

`processes: "none"` 完全不运行 NVIDIA-SMI。未安装 NVIDIA-SMI、读取失败或找不到相同 UUID 时，相应 GPU 的 `processes_available=false`；页面显示未知 / 部分覆盖，而不是“0 个进程、0 位用户”。已有 NVIDIA-SMI 报告可省略该字段，保持旧行为。

## 页面与指标口径

节点卡片标注 DCGM，详情标注 GPU 采集来源。DCGM 节点详情额外显示受支持的 SM 活跃度 / 占用率、Tensor 活跃度、DRAM 活跃度、PCIe / NVLink 收发速率、显存温度、时钟、volatile ECC 累计计数与最近 XID。未导出、不支持、权限不足、NaN / Infinity、DCGM blank / 哨兵或越界数值显示 `—` / `null`；真实零值保留为零。

- 基础“计算使用率”只取 `DCGM_FI_DEV_GPU_UTIL`（百分数），**不拿 GR engine / SM 活跃度替代**。
- profiling 活跃度由 0—1 比率换算为 0—100%；带宽以 B/s 上报；FB 显存字段是 MiB，与既有历史字段口径相同。
- 显存容量优先取 `DCGM_FI_DEV_FB_TOTAL`；缺少时，只有 used / free / reserved 全部有效才求和。缺少容量时百分比未知，不假设 reserved=0。
- volatile ECC 是设备计数器的当前累计值，XID 是最近错误码，均不是“本周期新增错误次数”。
- 高级指标只进入受认证的**当前快照 / latest JSON**，不扩充历史表、图表或 CSV，不改变已有 GPU 卡时、sample_seconds 和缺失值口径。离线后清除实时指标，不展示为当前读数。

## 升级与验证

先升级中心和对应 Node Reporter，再选择 DCGM。旧版本可能忽略新增配置 / 报告字段，不能直接使用；发布清单 `agents.python.gpu_providers` 与工具 `describe.gpu_providers` 显式声明能力，缺失表示仅支持 NVIDIA-SMI，控制器在生成私密配置前拒绝不兼容选择。回退旧版本前恢复 `gpu: {"provider": "nvidia-smi"}` 或移除 `gpu`。

自动化验证使用隔离回环 exporter fixture，覆盖字段 / 单位、哨兵、故障、身份、权限未知和真实 HTTP 上报 / 页面；这验证适配契约，不等于已在所有型号或真实 DCGM 服务上验收。上线前应在授权试点节点核对 UUID、字段支持、采样周期和 NVIDIA-SMI 对照值，再逐节点启用。

参考：[NVIDIA 支持范围](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/getting-started.html)、[Profiling](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html)、[DCGM Exporter](https://github.com/NVIDIA/dcgm-exporter)。
