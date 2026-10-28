# 共享协议契约

- `report-v1.json`：固定的合法报告，保留 GPU / system 的 NULL 未知值、单位和进程结构。时间和 UUID 是测试值，发送到真实回环测试中心前由测试更新。
- `agent-config-v1.json`：独立 agent 配置示例，身份 / token 明确为合成 fixture，不是运行凭据。
- `server-config.schema.json`、`agent-report.schema.json`、`ui-config.schema.json`：由 backend 的 Pydantic 模型导出的 JSON Schema。

在已准备的应用环境中运行 `.venv/bin/python -m project_tools schemas` 重新导出。`tests/test_contracts.py` 检查提交的 schema 与模型导出一致，并让独立标准库 agent 和中心校验共享报告。修改协议时同步 fixtures / 测试，破坏性变化必须升级对应版本；release format 2 不改变配置 / 报告 v1。

Schema 不足以描述全部运行约束：身份绑定、时间新鲜度、报告去重、GPU / process 唯一性、采样周期一致性等仍由语义校验与跨组件测试覆盖。agent 运行时不加载这些 schema、不导入后端或第三方校验库。

本版本没有公开 OpenAPI / docs 端点，也没有 Node agent；共享契约不是对未实现组件的支持声明。完整 HTTP 说明见[协议文档](../docs/protocols.md)。
