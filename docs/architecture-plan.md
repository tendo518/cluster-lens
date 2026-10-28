# 架构重构计划与完成状态

本轮计划已实现为可运行代码和本地验收，不再保留待替换的旧中心 / 前端入口。生产部署是独立授权操作，不能由此状态推断线上版本。

## 完成矩阵

| 阶段 | 已完成内容 | 主要验证 |
| --- | --- | --- |
| 业务分层 | MonitoringService、Store、history / exports、MonitorRuntime | 并发写入、持久化后确认、快照、调度与 shutdown |
| 正确性补强 | 严格整数协议版本、业务层唯一采样周期、跨标签登录修订、焦点恢复 | 协议异常、历史兼容、浏览器竞态和真实交互 |
| 组件分离 | backend/src/monitor、frontend、agents、contracts | 独立工具环境、标准库 agent、共享 fixtures |
| 前端构建 | strict TypeScript 模块 + Vite + npm lock + 来源 / 输出 receipt | 类型、格式、47 项前端单测与完整浏览器回归 |
| 现代中心 | FastAPI 显式路由 / Depends、Pydantic、Uvicorn / h11 单 worker | 原 HTTP 合同、框架路由、线程执行、慢请求与在途写入排空 |
| 可安装运行时 | Hatchling wheel、包资源、源码外 `-I` 运行、XDG state | 离线安装、无源码阴影、无安装目录写数据库、预检无副作用 |
| 发布格式 2 | uv.lock 目标 wheel 集、独立 pip bootstrap、确定性离线装配 | 目标 / 闭包 / 哈希 / 元数据、损坏与链接、重复构建 |
| 部署衔接 | 公开工具 / 产物契约、版本化最终路径、readiness、回退 / 恢复 | 控制器坏包测试、真实离线环境 + 假 manager 故障注入 |
| 运维收尾 | 明确 recover / rollback、受锁保护的凭据卸载、保守代码保留 | 不删未知文件、不回退 DB、未完成事务阻止破坏操作 |

详细当前结构见[架构](architecture.md)、[三组件实施结果](project-layout-plan.md)和[集成契约](integration.md)。

## 不改变的约束

- `monitor` / `python -m monitor` / `hqlab-monitor`、配置 / 报告 v1 和浏览器存储键保持稳定。
- SQLite schema / WAL、已有历史、sample_seconds、NULL 未知值、加权统计与 CSV 语义保留；只有一进程一 worker 写中心。
- 配置持有者控制身份、凭据和路径；应用不发现 inventory，不接收 SSH 私钥，不切换账户。
- 公开页面 / 静态资源 / health 无凭据或指标；浏览器继续使用用户选择的 localStorage token，不引入服务端会话。
- 正常部署不下载运行依赖、不安装系统 Python、不提权、不杀无关监听者、不移动数据库。

## 明确排除，而非待补占位

1. **Node agent**：Python agent 已覆盖现有节点；本版本只声明 python，其他选择拒绝。将来需独立实现和共同协议验证后再增加。
2. **Caddy / reverse proxy**：当前网络、域名和 TLS 由部署方决定，本次不引入代理或新认证会话。
3. **其他中心平台**：只声明 CPython 3.13 / Linux x86_64 / glibc 基线，不声称在最低 glibc 主机或其他 OS / ABI 上完成了实际验收。
4. **自动版本 GC / 数据库 schema 回退 / 跨节点事务**：不是本版本功能。保留代码环境供人工检查，自动恢复仅涉及托管输入和服务状态。

## 兼容边界

格式良好的 API、鉴权、错误、HEAD / CSV 行为保留。h11 对非法 HTTP framing 的处理不模拟旧自制解析器；精确区别见[协议文档](protocols.md)。中心 Python 要求提高不影响 Python 3.6 agent。历史 format 1 由外部控制器兼容，新构建只产生 format 2。

代码发布、gitlink 更新与生产部署分别验收。完成本轮重构不授权迁移生产账户、升级远端 Python、变更令牌或恢复数据库。
