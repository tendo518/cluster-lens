# 当前运行架构

ClusterLens 是一个模块化单体：中心提供静态页面和 API，独立 agent 在各节点本机采集，通过 HTTP(S) 上报。运行期不使用 SSH。当前 HTTP 适配器仍为 Python 标准库实现，FastAPI / Uvicorn 尚未引入；后续安排见[架构演进计划](architecture-plan.md)。

模块间的请求、鉴权、错误和版本规则见[通信契约](protocols.md)；frontend / backend / agents 的后续物理布局与 uv / 发行设计见[目录计划](project-layout-plan.md)。

## 组件与依赖方向

```text
python -m monitor（组合与启动入口）
  ├── Store                     SQLite 持久化
  ├── MonitoringService         监控用例与状态
  ├── MonitorRuntime ──────────→ MonitoringService
  │   离线维护、演示调度、保留期清理
  └── MonitorServer / Handler ─→ MonitoringService
      HTTP、viewer 鉴权、JSON / CSV / 静态响应

MonitoringService
  ├── protocol.py               报告结构、范围与采样时间校验
  ├── history.py                查询范围、分桶、覆盖率与响应组装
  └── Store                     原子写入与 SQL 聚合
```

| 文件 | 职责 |
| --- | --- |
| `monitor/__main__.py` | 解析启动参数与配置，组装组件，处理启动 / 关闭 |
| `monitor/server.py` | HTTP 路由、viewer 鉴权、请求体限制、安全响应头、错误到状态码的映射 |
| `monitor/service.py` | 节点身份、报告处理、去重 / 顺序、状态转换、实时视图、历史查询用例 |
| `monitor/lifecycle.py` | 后台维护循环与启停；业务对象不自行创建线程 |
| `monitor/protocol.py` | agent JSON 协议的校验与规范化 |
| `monitor/history.py` | 纯计算的查询规则与历史响应组装，不读数据库、不接触 HTTP |
| `monitor/store.py` | SQLite 连接 / 事务、样本投影、latest、SQL 聚合与保留期删除 |
| `monitor/exports.py` | 历史结果转 CSV，保留空值并防止表格公式注入 |
| `monitor/ui.py` | 公开 UI 配置、安全渲染与图标 |
| `agents/python/agent.py` | 标准库单文件 agent，源码与中心包分离，不导入中心模块 |

HTTP 层只调用业务服务，不持有 Store、不直接调用 SQL。业务服务不依赖 Handler、请求对象或运行调度器。SQL 聚合靠近数据库执行，覆盖率、缺失时段和对外结构由历史模块统一处理；不为每一层机械引入接口类。

## 报告接收与状态

HTTP 层先检查路由、演示模式、请求长度 / 编码，再解析 JSON。`MonitoringService.ingest()` 负责身份校验和报告处理：

1. 拒绝演示模式接收真实上报；校验节点名与其独立 Bearer 令牌。
2. 规范化报告字段，验证 UUID、采样时间、interval 和指标范围。
3. 在同一业务锁内检查重复与乱序，绑定中心配置的名称、地址和分组。
4. Store 在同一写事务中更新 samples 和 latest。
5. 事务成功后才发布实时状态、更新最近上报时间并返回确认。

业务异常不含 HTTP 状态码；HTTP 适配器把身份错误映射为 401、演示拒绝映射为 403、输入错误映射为 400。存储错误不伪装为接收成功。重复报告返回 duplicate，不延长节点活跃时间，也不重复写历史。

`accept()` / `accept_report()` 是受信任的内部入口，供演示与业务测试使用；外部传输入口必须调用 `ingest()`。返回的 overview 是独立快照，调用方修改嵌套字段不会影响中心状态。

启动时从 latest 恢复最近成功时间、报告 ID 等元数据，但不把旧指标重新标成在线。节点从 pending 开始，收到新报告后更新；超时转 offline，指标留空，仍保留最近成功记录。

## 历史与持久化

数据库仍为 metadata、latest、samples 三张表与已有索引，路径与字段不变，使用 WAL。连接按操作创建并关闭，写入串行化；失败事务不留下仅写入样本或仅更新 latest 的半份结果。

历史查询使用半开区间 `[start, end)`，只包含当前配置的节点。一个显式读事务同时获取趋势、逐节点统计与总计，避免并发上报使同一响应的不同部分读取到不同版本。

GPU 使用率按有效卡样本加权，显存按有效容量加权；观测时长受采样间隔、下一次样本与查询终点限制。未知值与缺失时段不补 0，不把停机间隔算成 GPU 卡时。完整统计定义见[使用指南](user-guide.md)。

## 生命周期

`MonitorRuntime.step()` 是不启动线程的单次维护，可直接测试：真实模式执行超时判定，演示模式按 interval 生成本地数据，保留期清理每小时检查一次。失败的周期被记录，后续仍可重试。

入口在 HTTP 服务准备好后启动维护线程，在退出时发出停止信号并等待当前维护操作结束，再关闭服务器。重复 start 不产生多个维护线程；停止后的实例不能重新启动，应创建新实例。

等待维护线程结束不代表已排空全部 HTTP 请求：当前 HTTP 适配器仍使用 daemon 请求线程。替换 Web 框架时需要单独验收在途请求的优雅关闭。

当前为单进程服务；增加 Web worker 前必须设计共享状态和后台任务归属，不能把进程内节点状态当成跨进程状态。

## 验证与外部契约

- `tests/test_services.py`：无 HTTP 的鉴权 / 报告用例、并发去重、失败重试、统计与 SQL 事务一致性。
- `tests/test_lifecycle.py`、`tests/test_startup.py`：调度、异常恢复和关闭。
- `tests/test_storage_compatibility.py`：既有 schema / WAL 历史的读取与追加。
- `tests/test_release_runtime.py`：脱离源码树启动中心归档，并用单文件 agent 完成真实 loopback 上报。
- `tests/test_api_contract.py`：HTTP 状态码、错误优先级、鉴权、响应结构和 CSV 基线。
- `tests/browser-smoke.mjs`：隔离演示环境中的完整浏览器行为，包括持久登录。

agent 协议、CLI、发布清单 format_version=1、`python -m monitor`、UI / 运行配置和 SQLite 格式均保持现有契约。独立的 agent.py 产物内容不变，中心归档不再包含不参与中心运行的 agent 源码。集成方使用[公开 CLI 与产物](integration.md)，不依赖本页内部 Python 类名。
