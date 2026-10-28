# Node Reporter

Node Reporter / 节点上报程序在被监控节点本机采集，再向中心上报；不导入后端包，不读取 inventory，不使用 SSH 拉取数据。节点（node）是机器，节点上报（node reporting）是行为；Node.js 专指 JavaScript 运行时。旧标识的保留规则见[术语与兼容标识](../docs/integration.md#术语与兼容标识)。

## Python（已实现）

源码为 `python/reporter.py`，Linux / Python 3.6+，只使用标准库；GPU 默认优先使用已有的 `nvidia-smi`，可逐节点显式选择本机已准备的 DCGM exporter。无需在节点安装 uv 或后端 Python 包；DCGM 模式需要部署方另行准备额外服务，不自动安装 / 启动 / 提权。配置、进程补充与额外指标见 [GPU 数据源](../docs/gpu-metrics.md)。

```bash
python3 node_reporter/python/reporter.py --config /path/to/node.json
```

不带配置仅输出本机诊断；带配置执行一次采集与鉴权上报。周期执行、解释器路径、账户与防止重叠由部署方管理。

发布工具将该文件原样输出为 `agent.py` 并在 release.json 中提供摘要；部署产物保留旧文件名仅为兼容已有安装，不代表组件名称。中心归档不包含节点上报程序。部署方消费产物，而不是读取此源码路径。测试仅通过专用加载器导入此文件，不把它重新包装成后端模块。

## Node.js（本版本明确排除）

本次交付明确只支持 Python 节点上报程序，不提供 Node.js 占位产物，也不把 Node.js 设为受管节点前提；选择不支持的实现直接失败，不能回退。实现前应先完成[协议一致性](../docs/protocols.md)与发布能力声明；若实现，优先使用 Node.js 内置 API，并验证与 Python 版本相同的字段、单位、NULL、时钟、鉴权、TLS、重定向和错误行为。

节点上报实现及可执行文件位置由部署配置显式指定；当前仅支持 Python，未来若增加 Node.js 实现也遵循此规则，不隐式探测后切换语言。新增实现不得改变已有节点的令牌或强制所有节点同时升级。
