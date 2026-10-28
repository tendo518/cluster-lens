# ClusterLens

只读服务器与 NVIDIA GPU 监控。节点在本机采集并通过 HTTP(S) 推送，中心提供仪表盘、历史统计和 CSV 导出；运行时不使用 SSH，也不执行主机管理操作。

## 运行环境

| 组件 | 要求 |
| --- | --- |
| 中心 | Python 3.9+、SQLite 3.25+ |
| 采集端 | Linux、Python 3.6+；GPU 指标需要 `nvidia-smi` |
| 前端单测 | Node.js 22+，不需要服务器或浏览器 |
| 浏览器测试 | Node.js 22+、Chrome / Chromium |

当前版本的中心与采集端仅使用 Python 标准库；前端是原生 JS / CSS / SVG，无需 pip、Node 构建或第三方运行依赖。本项目以源码和运行归档发行，不提供 `pip install .` 安装方式。

纯标准库是节点 agent 的约束，不是中心的长期限制。中心已分离业务服务、存储、HTTP 适配与后台调度，见[当前架构](docs/architecture.md)。[架构演进计划](docs/architecture-plan.md)记录分阶段进度；FastAPI 等框架替换尚未实施，不改变本页的当前运行方式。

开发环境由应用自己的 `pyproject.toml`、`uv.lock` 和 `.python-version` 管理，与 Ansible 的环境分离。开发解释器固定为 Python 3.13，当前发布包仍支持表中的中心 / agent 运行版本。以下命令在应用仓库根目录执行：

```bash
uv sync --frozen
```

## 本地体验

```bash
uv run --frozen python -m monitor --demo
```

访问 `http://127.0.0.1:8080`。演示只生成模拟指标，数据库为 `data/monitor-demo.sqlite3`，不读取真实节点配置、不接收真实上报。

应用默认使用 ClusterLens 标题与字母图标。可以直接替换站点展示配置：

```bash
uv run --frozen python -m monitor --demo --ui examples/ui.json
```

## 建立真实监控

1. 按 `examples/nodes.json` 编写公开节点清单，使用稳定的名称和展示地址。
2. 指定采集端可访问的中心上报地址，生成私密配置。
3. 中心只接收 `server.json`；每个节点只接收自己的 agent 配置。
4. 启动中心，再在节点执行采集器；周期调度和服务账户由部署方管理。

```bash
uv run --frozen python -m project_tools configure \
  --nodes examples/nodes.json \
  --url http://127.0.0.1:8080/api/agent/report \
  --ui examples/ui.json --output config/local

uv run --frozen python -m project_tools check-config config/local/server.json
uv run --frozen python -m monitor --config config/local/server.json \
  --host 127.0.0.1 --port 8080 --db data/monitor.sqlite3
```

上面的 loopback 地址仅适合单机验证；多机环境应配置实际可达的 URL、监听地址、TLS 和防火墙。在另一终端本机验证其中一个示例节点：

```bash
python3 agents/python/agent.py --config config/local/agents/node-a.json
```

授权查看者通过生成的 `viewer-token` 登录；不要把令牌写入 URL、命令行或版本库。重复生成会保留已有 viewer / 同名节点令牌；不要删除配置目录来更新代码。

## 配置与职责

- **应用配置**：采样周期、离线阈值、保留期、节点身份与 UI 展示。
- **UI 配置**：标题、品牌名称、字母标记或 PNG 图标、工作区文字、页脚、初始明暗主题和配色。
- **部署配置**：中心地址、SSH、账户、文件路径、服务、调度和迁移，由部署工程管理；应用不读取 inventory 或父目录。

显式配置优先于应用默认值；浏览器保存的个人主题 / 配色优先于站点默认。UI 是公开信息，不应包含秘密。完整字段、优先级和图标配置见[配置参考](docs/configuration.md)。

## 构建与测试

```bash
uv run --frozen python -B -S -m project_tools build --output dist
uv run --frozen python -S -m unittest discover -s tests -v
node --test tests/frontend-auth.mjs
uv run --frozen node tests/browser-smoke.mjs
```

Python host 脚本源码位于 `agents/python/agent.py`，独立于后端包，不导入后端模块。发布输出为 `server.tar.gz`、`agent.py`、`release.json`，不含配置、数据库、测试或部署工具。中心在归档解压根目录运行 `python3 -m monitor`，采集端执行独立 `agent.py`。集成方应使用[公开 CLI 和产物契约](docs/integration.md)，不导入应用内部模块。

可选 Just 入口：`just demo`、`just configure`、`just run <配置>`、`just build`、`just test`（Python 与前端单测）、`just frontend-test`、`just check`、`just browser-test`。Python 命令默认使用应用的 uv 环境，可用 `PYTHON` 显式覆盖；Ruff 用于静态检查。当前中心归档和单文件 agent 仍可直接由 Python 运行，受管节点不因开发工具变化而需要 uv。

## 架构资料

- [当前架构](docs/architecture.md)：已经实现的业务、存储、HTTP 与生命周期边界。
- [通信契约](docs/protocols.md)：浏览器 / agent API、内部调用、发布与部署的版本边界。
- [目录与构建设计](docs/project-layout-plan.md)：frontend / backend / agents 的目标布局、uv 与现代工具、离线交付。
- [重构进度](docs/architecture-plan.md)：已完成项与下一阶段门槛，不等同于线上部署状态。

## 使用与安全

支持服务器 / 进程过滤、25 / 50 / 100 条分页、置顶、节点详情、历史图表、统计、CSV、六种配色及明暗模式。缺失指标保留为未知，离线不伪装为 0%；统计定义见[使用指南](docs/user-guide.md)。

viewer 和逐节点令牌彼此隔离；页面验证成功后将 viewer 令牌持久存入 `localStorage`，提供退出登录，兼容旧会话迁移。外观与置顶也存 `localStorage`。共享浏览器的使用者可能继续访问，使用后应退出。系统不提供公网多用户账户、作业计费或主机控制能力。HTTP 仅用于可信内网，其他网络需 HTTPS 和访问控制。
