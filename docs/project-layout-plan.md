# 三组件目录与构建：实施结果

本轮目录 / 运行时迁移已实现，不再是占位目标。当前模块关系见[架构说明](architecture.md)，公开交付规则见[集成契约](integration.md)。实现和本地验证不代表生产部署。

```text
pyproject.toml / uv.lock       # 应用独立 Python 环境和锁
backend/src/monitor/          # 可安装中心包；保留 python -m monitor
  api/                        # FastAPI 路由、依赖鉴权、异常适配
  schemas/                    # Pydantic 配置 / 报告 / UI 模型
  service.py / lifecycle.py   # 业务、单进程生命周期与后台调度
  store.py / history.py / exports.py
  static/                     # receipt 校验后装配，生成物不入库
backend/tests/
frontend/                     # 独立 npm lock、strict TypeScript、Vite
  src/                        # API / auth / state / selectors / views / navigation
  public/ / scripts/ / tests/
  dist/                       # 构建输出，不入库
agents/python/agent.py        # 3.6+、标准库、单文件
agents/tests/
contracts/                    # 共享 JSON fixtures 与导出 schema
project_tools/                # configure / check-config / describe / assets / build
                              # prepare-wheels / schemas
hatch_build.py                # wheel 静态资源装配门禁
tests/                        # 跨组件与已安装发行包验收
```

只有一个应用 Python 项目；没有为目录分组制造互相依赖的内部发行包。前端无框架重写，视图和状态按职责模块化。业务模块不导入 HTTP 框架；数据库仍是 SQLite，未引入 ORM、队列或新存储。

## 已落实的设计选择

| 事项 | 结果 |
| --- | --- |
| 中心版本 / 平台 | CPython 3.13、Linux x86_64 / glibc ≥2.17，非自由线程；明确 wheel ABI |
| 框架 | FastAPI + Uvicorn / h11 + Pydantic；显式路由与鉴权依赖，非 catch-all 手写路由包装 |
| 前端 | strict TypeScript + Vite；保留 DOM / 品牌 / 浏览器存储及阻塞首屏外观行为 |
| 运行资源 | wheel 包资源，开发可显式 `--assets`；不寻找父仓库或源码目录 |
| 状态 | XDG / home state 默认路径；部署仍显式传原有 `--db` |
| 依赖 | 应用 uv.lock / 前端 npm lock；显式获取，离线装配和安装 |
| 安装工具 | 标准 venv / ensurepip + 随包锁定 pip；不额外分发 uv，不复制 venv |
| 发布格式 | format 2；保留三个产物文件名，明确不兼容 v1 安装语义 |
| Python agent | 独立脚本 / 配置 / 报告 fixtures，节点无需 Node、uv 或后端依赖 |
| Node agent | 本版本明确不实现；能力只声明 python，未知选择失败而非回退 |
| Caddy / proxy | 本版本明确不引入；沿用直达 HTTP(S) 上报，不自动改 TLS / 域名 |

## 验收门槛

- [x] 应用独立环境下构建、测试；前端独立锁和构建。
- [x] 离线安装后的中心在源码树外以 `-I` 启动，使用 wheel 内资源；完整浏览器回归。
- [x] 格式、目标、摘要、缺失依赖、链接、坏包、嵌套模块和资源 receipt 测试。
- [x] 独立标准库 agent 向已安装中心真实回环上报。
- [x] API / 配置 / 数据库 / NULL / 历史时长 / CSV 与 UI 兼容回归。
- [x] 外部控制器使用公开 CLI / 格式契约；实际离线 venv、假服务管理器驱动激活、回退、中断恢复和历史保留。

停止边界：未执行生产升级、账户迁移、系统 Python 安装或反向代理变更。其他平台与 Python 版本、Node agent、自动环境垃圾回收均不是本版本承诺。
