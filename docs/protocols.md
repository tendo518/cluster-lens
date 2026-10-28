# 模块通信与集成契约

本文区分**当前已实现的协议**与**后续目录 / 依赖重构的约束**。当前 HTTP、agent 配置与报告、发布清单均保持兼容；本文中的后续项不表示已提供 Node agent、FastAPI 或新版发布格式。

## 1. 通信总览

| 调用方 → 被调用方 | 当前通道 | 身份 / 数据边界 |
| --- | --- | --- |
| 浏览器 → 中心 | 同源 HTTP GET，JSON / CSV | viewer Bearer；只读监控接口 |
| host agent → 中心 | HTTP(S) POST JSON | 每节点独立 Bearer，只能上报配置中的自身身份 |
| HTTP 适配器 → 业务服务 | 进程内 Python 调用 | 已解析的参数 / JSON；业务不依赖请求对象 |
| 业务服务 → Store | 进程内调用，SQLite 事务 | 规范化快照与聚合查询，不传 HTTP 状态码 |
| 生命周期调度器 → 业务服务 | 单进程后台调用 | 超时判定、演示更新、保留期维护 |
| 部署工程 → 配置 / 发布工具 | 显式 CLI、JSON 与文件产物 | 公开节点信息和站点配置；不传 SSH 变量到运行模型 |
| 部署工程 → 受管节点 | SSH / 文件分发 / 服务管理 | 这是部署通道，不是运行期监控通道 |

外部稳定接口是 CLI、配置、HTTP 和发布产物，不是内部 Python 类名。前端、中心和 agent 可以有不同依赖，但必须遵守对应协议。

## 2. 浏览器 API（当前）

- 页面、受限静态资源和公开 UI / 图标无需登录，不包含真实指标或凭据。
- 浏览器验证成功后在 localStorage 保存 viewer token，后续请求使用 `Authorization: Bearer <token>`；不把 token 放 URL 或 Cookie。
- viewer 令牌不能上报数据，agent 令牌不能读取监控数据。
- `GET /healthz` 仅返回 `{"status":"ok"}`，不证明所有节点在线。
- `GET /api/overview` 返回 hosts、mode、demo、interval、stale_after、retention_days、collecting、last_cycle、server_time。
- `GET /api/history` 返回 start、end、bucket_seconds、points、stats、aggregate、busy_threshold、sample_interval。
- `GET /api/export.csv` 使用同样的查询与统计，UTF-8 BOM、固定列顺序和文件名 `clusterlens-usage.csv`；缺失值为空，危险的表格公式前缀转义。

历史查询参数为 start / end（Unix 秒）和可选 host（中心节点名称）。默认最近一小时，区间 `[start, end)`，最长 31 天，end 不超过中心当前时间 60 秒；必须是有限数值，start 不为负，end 大于 start。重复参数取第一个非空值，空值视为省略；这一行为在替换框架时不能无意改成取最后一个值。

HEAD 对相同资源返回 GET 的状态和头部，但不发送响应体。当前响应包含 `Cache-Control: no-store`、nosniff、no-referrer、CSP 与禁止嵌入头。JSON 错误统一为 `{"error":"说明"}`：viewer 鉴权失败为 401，参数无效为 400，接口 / 页面不存在为 404，内部错误为 500。`/api/` 下的 GET 先检查 viewer 鉴权，再判断接口是否存在。

浏览器 UI 行为见[使用指南](user-guide.md)。后续现代前端仍通过这些 API 通信，不直接读取 SQLite、server.json 或后端源码。

## 3. agent 配置与报告（当前 version=1）

### 节点配置

部署方仅把对应节点的私密 JSON 发送给该节点：version、host、url、token、interval、timeout，可选 ca_file。字段限制见[配置参考](configuration.md)。配置文件不含 viewer token、其他节点令牌或 SSH 私钥。

Python agent 本机采集后发送一次，退出状态表示本次采集 / 上报是否成功；周期调度和防止重叠由部署方负责。agent 忽略代理环境变量、拒绝重定向并验证 TLS。错误日志不得包含令牌或 Authorization。

### 请求

`POST /api/agent/report`：

- `Authorization: Bearer <该节点令牌>`。
- `Content-Type: application/json`，支持附带 charset 参数。
- 提供正整数 Content-Length；最大 1 MiB；不接受 Transfer-Encoding 或压缩正文。
- body 是 version=1 对象，至少包含 host、report_id、collected_at、interval；成功采集含非空 system 与 gpus 数组。collection_ms、warnings、error 按协议处理。
- report_id 为 UUID 字符串，agent 每次新采样生成新 ID；重试同一报告应保留 ID，不能用旧数据构造新时间冒充新采样。
- collected_at 为采样端 Unix 时间，与中心接收时间之差不超过 `max(60, 2 × interval)` 秒。interval 必须与中心配置一致。
- 指标允许未知值 null，但拒绝 NaN、Infinity、越界数字、重复 GPU index、无效 PID 或超过限制的数组 / 字符串。完整规范化规则当前位于 `monitor/protocol.py`，通过协议测试固定。
- error 为非空错误说明时视为采集失败，中心清空当前指标并标记 offline，不伪造 0。

中心依据认证后的 host 绑定名称、地址和分组，不接受报告覆盖配置身份或自行宣称 online。历史时间使用中心接收时间，采样时间单独保存。

### 确认与拒绝

新报告持久化后返回 200：

```json
{"accepted": true, "duplicate": false, "server_time": 1000.0}
```

重复 ID 返回 200、accepted=false、duplicate=true；不重复写历史、不刷新活跃时间。相同或更旧的 collected_at（非重复 ID）被拒绝，节点需重新采样。

| 状态码 | 含义 |
| --- | --- |
| 400 | 分块请求、无效 JSON、正文不完整或报告校验 / 顺序错误 |
| 401 | 节点未知、身份与令牌不匹配 |
| 403 | 演示模式拒绝真实上报 |
| 405 | 非上报端点的 POST |
| 408 | 读取正文超时 |
| 411 | 缺少有效 Content-Length |
| 413 | 报告超过 1 MiB |
| 415 | 不支持的内容类型或压缩编码 |
| 500 | 保存失败，不能当作成功上报 |

错误优先级也是基线的一部分：路由 → 演示策略 → 请求分帧 / 编码 → JSON 解析 → 节点鉴权 → 业务校验 / 写入。agent 不需要知道中心使用什么 Web 框架或 ORM。

### 多语言 agent 的后续要求

Python 与可选 Node 实现必须共用同一份配置 / 报告协议和一致性测试。schema 可从后端模型导出供构建和测试使用，但 agent 运行时不因此引入 schema 库或后端包。时间窗口、身份、去重、NULL、单位和累计计数差分等语义仍需测试，不能仅靠 JSON Schema 保证。

Node 版尚未实现；不能仅改解释器名称就把 Python agent 当成 Node agent，也不能假定所有节点已安装 Node。

## 4. 中心内部调用（当前，非对外兼容承诺）

- `MonitoringService.ingest(payload, authorization, now=None)`：完成鉴权、协议规范化、去重、持久化与发布。异常类型区分身份、演示策略、无效数据和存储失败；HTTP 适配器负责状态码。
- `overview()` 返回独立的当前快照；`history(start, end, host)` 统一验证范围与节点选择，再请求 Store 的聚合事实并组装响应。
- `Store.record()` / `record_many()` 在一个事务中写 samples 和 latest；`query_history()` 用一个读事务获得趋势、逐节点统计与合计；连接在操作完成后关闭。
- `MonitorRuntime.step()` 调用业务维护；start / stop 只负责后台执行与结束，不实现 HTTP 路由。
- `history_csv()` 对历史结果做纯格式转换，不访问数据库。

这条依赖方向不随目录调整或框架替换而反转。数据库不是前端或 agent 可访问的集成接口。

## 5. 构建、配置和 Ansible 边界

当前公开 CLI 与发布清单详见[集成契约](integration.md)：配置工具生成私密 JSON，构建工具生成 server.tar.gz、agent.py、release.json。release.format_version、运行配置 version、报告 version 是不同的兼容维度，不能混用。

后续物理目录分离与中心依赖交付遵循以下约束：

1. 构建工具拥有源码布局、前端构建和资源装配知识；Ansible 只消费清单和公开产物。
2. 配置工具继续由应用拥有，schema 不在部署工程重复实现；同一节点重新生成配置不轮换令牌。
3. 多种 agent 的选择与解释器路径属于部署配置，可逐节点覆盖；清单声明受支持的实现、运行版本、入口与摘要，应用负责验证产物能力，安装器负责验证目标机器。
4. 选择不存在的实现、缺少解释器或不兼容的版本时，在替换现有服务前失败，不自动切换语言、安装全局依赖或调整账户。
5. 中心运行依赖只交付到中心。控制端、中心、Python agent 与 Node agent 不是同一个环境；仓库内 Python 依赖由 uv 锁定管理。
6. 依赖交付、产物或启动约定发生不兼容变化时，先定义新的 release 格式并让控制端支持，再更新固定应用引用。旧 agent 报告协议可继续保持 version=1。

当前 Ansible 安装器仍只支持 Python agent。新增 Node 选择时还要检查用户服务管理助手的运行要求，不能把“Node agent 无 Python 依赖”误写为“整套部署工具不再需要 Python”。
