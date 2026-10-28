# ClusterLens

基于认证 HTTP(S) 上报的只读服务器 / GPU 监控。中心不通过 SSH 轮询；前端、后端和 **Node Reporter / 节点上报程序** 独立组织，按明确发布契约交付。

节点（node）指机器，节点上报（node reporting）指行为；JavaScript 运行时写作 Node.js。旧接口、产物和部署标识保留兼容拼写，不是组件名，见[术语与兼容标识](docs/integration.md#术语与兼容标识)。

## 组件与运行条件

| 组件 | 当前实现 | 要求 |
| --- | --- | --- |
| 后端 | `backend/src/monitor`，FastAPI / Uvicorn / Pydantic，SQLite WAL | CPython **3.13**；当前离线发行目标 Linux x86_64 / glibc 2.17+，SQLite 3.25+ |
| 前端 | `frontend/src`，严格 TypeScript / Vite，原生 DOM | 构建 / 测试使用 Node.js **24+**；中心仅分发构建资源 |
| Python 节点上报程序 | `node_reporter/python/reporter.py` | Linux / Python **3.6+** / 标准库；GPU 默认 nvidia-smi，可显式选择本机 DCGM exporter |
| 项目工具 | `project_tools`，公开 CLI / JSON / 发布产物 | 独立应用 uv 环境；不复用 Ansible 环境 |

仅分发到节点的上报程序受单文件 / 标准库约束。后端 wheel 不包含节点上报程序、项目工具、测试、Ansible 或私密运行状态。Python 包入口 `python -m monitor`、发行标识 `hqlab-monitor` 和浏览器偏好键保持不变。

当前真实验证平台为 Linux x86_64、CPython 3.13.14；glibc 2.17 是所选依赖 wheel 声明的最低 ABI，不表示每个旧发行版都做过实机验证。musl、ARM、free-threaded CPython、其他 Python 小版本不是本版离线产物的支持目标。不会自动下载或升级中心 Python。

## 独立开发

```bash
# 在应用 checkout 根目录，显式准备开发依赖
just setup                       # uv sync --frozen --group build；npm ci
just demo                        # 构建前端、装配资源，使用模拟数据
```

不依赖父仓库的 inventory、生产配置或 Vault 密钥。等价的分步入口：

```bash
uv sync --frozen --group build
npm --prefix frontend ci
npm --prefix frontend run build
uv run --frozen --group build python -m project_tools assets
uv run --frozen --group build python -m monitor --demo --assets frontend/dist
```

直接运行后端时，默认状态位于 `$XDG_STATE_HOME/clusterlens`，否则 `~/.local/state/clusterlens`；demo / live 使用不同文件。`just demo` / `just run` 为方便已有开发工作区，仍显式指定 `data/` 文件。旧历史不会自动搬迁，已有部署应继续传入原来的 `--db`。

```bash
uv run --frozen --group build python -m monitor \
  --config /absolute/private/server.json --db /absolute/state/monitor.sqlite3
uv run --frozen --group build python -m monitor \
  --check-config /absolute/private/server.json
```

`--check-config` 仅检查配置、导入、资源与 SQLite 版本，不创建 Store、数据库或监听器，不打印凭据。安装后的 wheel 从任意 cwd 用 `python -I -m monitor` 运行，不依靠源码目录或 PYTHONPATH。

## 配置与发布

```bash
# 新中心与新版节点上报程序都已就绪时可用新地址
just configure examples/nodes.json https://monitor.example.org/api/node/report /absolute/private
# 只在构建机显式获取 uv.lock 锁定、校验摘要的官方 wheel
just prepare-wheels
# 前端构建 + 离线装配；缺失 wheel 时失败，不隐式联网补齐
just build /absolute/releases/clusterlens
```

新发布使用 **format_version=2**：`server.tar.gz`（后端 wheel、完整依赖、锁定 pip 和安装清单）、`agent.py`、`release.json`。包内包含最终前端资源；安装端不需要 Node.js、npm、uv 或源码 checkout。操作者须提供已批准、带 venv / ensurepip 的 CPython 3.13；安装器只在最终版本目录离线安装，不复制控制端 venv。

现有部署的默认上报地址仍为兼容旧中心的 `/api/agent/report`；新地址 `/api/node/report` 须在中心及对应节点上报程序升级后显式选择，回退前恢复旧地址。配置生成不下载依赖，也不隐式准备环境。`describe` 输出工具契约、配置 / 报告版本和支持的节点上报实现；`schemas` 导出由同一 Pydantic 模型生成的公开 JSON Schema。详细入口、兼容规则与 artifact 校验见[集成契约](docs/integration.md)。

## 功能与安全边界

- 系统 / GPU / 进程快照、节点详情、历史统计和 CSV；未知 / 离线指标保留 NULL。
- GPU 数据源逐节点可选，默认优先 NVIDIA-SMI；DCGM 节点额外展示受支持的活跃度、带宽、ECC / XID 等当前指标，见 [GPU 数据源](docs/gpu-metrics.md)。
- 六种配色、明暗 / 系统模式、首屏外观、个人偏好、置顶、筛选后分页、键盘与移动导航。
- viewer 与每个节点使用独立令牌；主机身份绑定、时间窗口、UUID 去重、请求体上限、持久化后确认。
- 采用可信内网下的简单 localStorage 登录持久化，支持跨标签退出、旧会话迁移、401 清除和网络故障保留。它不是服务端会话 / Cookie 系统；同源脚本和共享浏览器使用者可访问凭据。
- 登录前只有公共页面 / 资源 / 最小健康信息，没有真实指标或令牌。展示文本转义，品牌图片只接受受限 PNG / 应用生成图标。
- 文档 / OpenAPI HTTP 端点关闭；代理头不被默认信任。公网或不可信网络须先部署合适的 HTTPS / 访问控制。

## 验证入口

```bash
just test              # 后端、节点上报程序、跨组件/离线安装与前端模块单测
just check             # TypeScript、前端格式与脚本语法
just browser-test      # Chrome/Chromium，全量真实浏览器回归
just schemas           # 导出 contracts/*.schema.json
```

`just test` 的发布测试需要事先 `just prepare-wheels`；测试自身不下载 wheel。独立节点上报程序测试可以直接用开发 Python `-S -m unittest discover -s node_reporter/tests`，不加载后端包。浏览器回归可同时设置 `MONITOR_TEST_PYTHON=/absolute/installed/venv/bin/python` 与 `MONITOR_TEST_ASSETS=package`，从源码树外以 `-I` 启动并验证 wheel 内资源；默认使用开发构建资源。

## 文档

- [运行架构](docs/architecture.md) / [目录与构建](docs/project-layout-plan.md)
- [配置参考](docs/configuration.md) / [GPU 数据源](docs/gpu-metrics.md) / [使用指南](docs/user-guide.md)
- [通信契约](docs/protocols.md) / [发布与集成](docs/integration.md)
- [重构计划与完成决策](docs/architecture-plan.md) / [机器可读契约](contracts/README.md)

Ansible 位于独立管理仓库，通过固定 submodule 版本和公开接口集成。Node.js 节点上报程序与 Caddy 仍是按需求另行立项的可选扩展，本轮没有创建占位实现，也没有在生产部署任何重构代码。
