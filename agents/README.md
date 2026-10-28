# Host agents

agent 在被监控节点本机采集，再向中心上报；不导入后端包，不读取 inventory，不使用 SSH 拉取数据。

## Python（已实现）

源码为 `python/agent.py`，Linux / Python 3.6+，只使用标准库；NVIDIA 指标通过已有的 `nvidia-smi` 获取。无需在节点安装 uv 或 Python 包。

```bash
python3 agents/python/agent.py --config /path/to/agent.json
```

不带配置仅输出本机诊断；带配置执行一次采集与鉴权上报。周期执行、解释器路径、账户与防止重叠由部署方管理。

发布工具将该文件原样输出为 `agent.py` 并在 release.json 中提供摘要；中心归档不包含 agent。部署方消费产物，而不是读取此源码路径。测试仅通过专用加载器导入此文件，不把它重新包装成后端模块。

## Node（本版本明确排除）

本次交付明确只支持 Python agent，不提供 Node 占位产物，也不把 Node 设为受管节点前提；选择不支持的实现直接失败，不能回退。实现前应先完成[协议一致性](../docs/protocols.md)与发布能力声明；若实现，优先使用 Node 内置 API，并验证与 Python 版本相同的字段、单位、NULL、时钟、鉴权、TLS、重定向和错误行为。

Python / Node 选择及可执行文件位置由部署配置显式指定，不隐式探测后切换语言。新增实现不得改变已有节点的令牌或强制所有节点同时升级。
