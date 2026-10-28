# 运行架构

## 组件边界

```text
frontend/src (TypeScript) ── Vite ── frontend/dist
                                         │ 装配 / wheel 包资源
                                         ▼
浏览器 ─ 同源 HTTP ─ FastAPI + Uvicorn ─ MonitoringService ─ Store / SQLite WAL
                        ▲                   │
                        │              MonitorRuntime（lifespan）
Python 节点上报程序 ─ 认证 HTTP(S) 上报       │
                                          └─ 离线判定 / demo / 保留期清理
```

后端代码位于 `backend/src/monitor`；前端构建和运行期不读取 inventory 或私密 server.json。部署层调用公开工具 CLI / JSON / artifact，不导入应用模块。

## 后端

- `api/app.py`：显式 FastAPI 路由、viewer 认证依赖、异常映射、安全响应、报告流读取。保留原始路径匹配语义，不通过手写 HTTP 解析器绕过 h11。
- `server.py`：单进程 Uvicorn 配置和小型同步嵌入接口 `MonitorServer`；没有旧 ThreadingHTTPServer / Handler。关闭代理头信任、自动 slash 重定向、公开文档、请求目标访问日志和多 worker。
- `schemas/`：Pydantic 配置、UI 和报告模型，同时提供运行校验与离线 JSON Schema。严格版本 / 类型不靠强制转换。节点上报程序不导入这些模型。
- `service.py`：HTTP 无关的认证、身份、时序、去重、状态、overview / history 用例；写入成功后才返回确认并发布内存状态。返回嵌套快照副本，避免调用者修改共享状态。
- `store.py`：连接、WAL、串行写事务、历史窗口查询与保留期操作。历史响应的多条查询处于同一个读事务，不会拼接不同 WAL 快照。
- `history.py` / `exports.py`：时间范围、桶与统计响应、纯 CSV 序列化。未知 / 离线仍为 NULL，CSV BOM / 列 / 公式保护不变。
- `lifecycle.py`：维护线程的启动、停止、采样 / offline / demo / pruning 调度；不参与 HTTP 路由或直接服务身份认证。
- `assets.py` / `ui.py`：安装包资源或显式开发资源目录、安全静态路径、一次转义的品牌页面、受限 PNG 与生成图标。
- `paths.py`：XDG / 用户状态目录；不计算源码根，也不将默认数据库写进 site-packages。部署的显式 `--db` 不变。

### 并发与关闭

同步用例由 FastAPI / Starlette 线程池执行；报告体异步读取有总计 15 秒 deadline 和 1 MiB 上限，解析、验证与 SQLite 写入在线程执行。不能因为使用 async 就让同步 SQLite 阻塞事件循环，也不能提前发送未持久化的成功确认。

FastAPI lifespan 拥有维护线程。Uvicorn 先停止接受新请求、等待已有请求及写入结束，再退出 lifespan 和停止维护线程。SIGTERM 不主动取消已经提交给线程池的写入。systemd 仍有最终停止上限（v2 单元 90 秒）；进程强杀、主机断电和永久 I/O 卡死不属于“保证完成”的范围，SQLite 事务和客户端重试仍是必要保护。

仅支持一个 worker：节点状态、去重热状态和维护线程属于该进程。扩展到多进程前必须重新定义共享状态和维护归属，不通过配置多个 worker 假装已经支持。

### 数据语义

业务服务是采样周期的唯一来源，每次 `Store.record(..., interval=...)` / `record_many(..., interval=...)` 显式传入周期。持久化 `sample_seconds` 后，新配置不会重新解释旧样本时长。

保留原 SQLite 表结构和历史含义：卡样本加权 GPU 使用率、容量加权显存、被采样周期 / 下一条样本 / 查询终点共同约束的卡时，以及独立的覆盖率 / 可用率。此重构不搬迁数据库、不合并历史、不改账户或时钟。

## 前端

`frontend/src/app.ts` 仅组成模块并绑定入口。认证和 API、状态 / selectors、偏好、节点 / 进程 / 详情、历史 / 图表、工作区导航及控制器各有职责和类型；没有生产环境测试全局对象或巨型无类型脚本。

Vite 输出固定的 `index.html`、`app.js`、`appearance.js`、`styles.css`、`palettes.css`。外观初始化是 CSS 之前的阻塞 classic script；品牌占位符保留给后端安全渲染，个人偏好优先于站点默认值。构建不读取 .env 或任意 VITE_* 环境值，收据绑定源码 / 锁 / 构建配置与最终资源摘要，防止陈旧产物装包。

认证沿用本地保存 viewer token 的可信内网模型；普通轮询不保存凭据。登录、一次性旧会话迁移、跨标签变更和迟到响应分别处理。详情刷新保留焦点；控件因离线而消失时，有明确的可访问焦点退路。

## 发布与部署

uv 锁定应用环境和依赖；npm 锁定前端工具。format2 交付完整离线 wheel 集和锁定 pip，中心在最终版本目录创建自己的 venv。Ansible 管理账户 / SSH / 目录 / systemd / 凭据 / 切换恢复，不成为应用配置模型的第二份实现。

安装预检不打开生产数据库；候选服务在 scratch SQLite 和 loopback 上验证。正式激活后还需认证检查所选 release / 进程身份，失败恢复原配置、单元、解释器选择和服务状态，不恢复或覆盖数据库 / WAL。旧 v1 代码在首次 v2 升级中保留，`-I` 防止其遮蔽安装包。

新中心最低条件与旧零依赖 v1 不同，不能把 wheel 归档伪装为 v1。旧 artifact 读取兼容由控制端显式分支负责；新构建器仅输出 v2。
