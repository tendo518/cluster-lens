# 部署集成契约

## 术语与兼容标识

**Node Reporter / 节点上报程序**是采集并发送监控报告的组件；**节点（node）**是被监控机器，**节点上报（node reporting）**是行为。JavaScript 运行时统一称为 **Node.js**，不能与节点混用。本版本只有 Python 实现，Node.js 节点上报程序仍未实现。

源码入口为 `node_reporter/python/reporter.py`，独立测试位于 `node_reporter/tests/`。下列含 `agent` / `agents` 的历史标识仅保留为兼容契约，不用于称呼机器或组件，也不指编码助手：

| 兼容边界 | 保留拼写 | 含义 |
| --- | --- | --- |
| HTTP / 身份 | `/api/agent/report`、`agent_token` | 旧节点上报端点、逐节点令牌字段；新端点为 `/api/node/report` |
| 发布产物 / 能力 | `agent.py`、describe / release 的 `agents` | 节点上报程序的部署文件名及实现能力字段 |
| 生成 / 安装配置 | `agents/<node>.json`、`agent.json`、`agent-service.json` | 逐节点私密配置和服务描述；不是源码目录 |
| 安装角色 / 用户单元 | `--role agent`、`hqlab-monitor-agent-<node>.service`、`hqlab-monitor-agent-<node>.timer` | 已部署的角色参数及 systemd 单元名 |
| 部署变量 / 元数据 | `monitor_agent_python`、`monitor_agent_implementation`、`agent_implementations` | 解释器路径与实现选择 |
| Schema / fixture | `agent-*.json`（如 `agent-report.schema.json`、`agent-config-v1.json`） | 现有机器可读契约文件名 |

部署命令与上述标识必须保持原拼写；术语和源码目录调整不改变 release format 2、配置 / 报告 version=1 或 schema 字段及约束（schema 的展示标题已更新），也不表示完成了新部署。

新中心与 Node Reporter 同时支持 `/api/node/report` 和历史 `/api/agent/report`，二者使用同一鉴权、去重和持久化逻辑。现有控制器默认仍生成旧地址以兼容未升级中心；已保存地址不会自动改写。新地址必须在中心**和对应节点上报程序都已升级**后显式配置，并考虑回退：旧中心或旧上报程序不支持新地址；若需要回退到旧版本，先将对应配置恢复为旧地址。单独更新节点时切勿提前切换地址。

## 职责与支持范围

应用拥有中心 wheel、独立节点上报程序、前端、配置 / 报告规则与发布工具；部署方拥有 inventory、SSH、账户、实际配置、服务管理、安装路径和迁移。只通过公开 CLI、JSON 和产物集成，不导入应用内部模块，不注入 PYTHONPATH，不扫描运行源码布局。

新发布为 **format 2**；发行标识仍为 `hqlab-monitor`，Python 包入口仍为 `python -m monitor`。中心支持常规 CPython 3.13、Linux x86_64、glibc ≥2.17、SQLite ≥3.25，需 venv / ensurepip。已实际验证的平台为 CPython 3.13.14 / Linux x86_64；不是 ARM、Windows、musl、自由线程 Python 或其他 Python 小版本的兼容承诺。部署不得自动安装 Python 或提权补齐前提。

Python 节点上报程序仍是 3.6+ 标准库单文件。Node.js 节点上报程序和反向代理明确不在本版本交付范围，不能静默回退实现，也不自动修改 TLS、域名或网络拓扑。

## 工具环境与公开 CLI

从应用根目录运行。先显式准备依赖；配置和发布装配本身不获取依赖：

```bash
uv sync --frozen --group build
npm --prefix frontend ci
.venv/bin/python -m project_tools prepare-wheels
npm --prefix frontend run build
.venv/bin/python -m project_tools build --output /absolute/release

.venv/bin/python -m project_tools describe
.venv/bin/python -m project_tools configure \
  --nodes /absolute/nodes.json \
  --url https://monitor.example.org/api/node/report \
  --ui /absolute/site-ui.json --output /absolute/private-config
.venv/bin/python -m project_tools check-config /absolute/private-config/server.json
```

`uv sync`、`npm ci`、`prepare-wheels` 是显式依赖获取步骤。`build` 只消费已安装构建工具、有效前端 receipt 与经过 uv.lock 摘要核验的 wheel 缓存，缺失或损坏直接失败，不调用 npm / uv 下载。`just build` 会先执行本地前端构建，但不会准备依赖。`project_tools` 是源码侧工具，不打入运行 wheel。仅用 `uv run --project` 不会切换 cwd；需要 uv 入口时使用 `uv run --no-sync --directory /application ...`。

工具解释器保留其 venv 可执行路径，不对 Python 符号链接调用 resolve；现代工具不使用 `-S`。适配器清除 PYTHONPATH / PYTHONHOME。新 describe 契约：

```json
{"contract_version":2,"application":"hqlab-monitor","config_version":1,"report_version":1,"release_formats":[2],"agents":["python"],"gpu_providers":["nvidia-smi","dcgm"],"prepared_environment_required":true}
```

配置器只接收公开节点字段及 UI 输入；成功状态输出不含令牌。公开节点除 name / address / groups 外可包含应用拥有的 `gpu` 配置，仅分发到对应 Node Reporter；控制器不传入其他 SSH hostvars。配置规则及凭据保留见[配置参考](configuration.md)。

GPU 默认 NVIDIA-SMI，DCGM 需显式选择与外部前提，见 [GPU 数据源](gpu-metrics.md)。`agents.python.gpu_providers` 声明发布能力，describe 的 `gpu_providers` 声明配置工具能力；两者缺失时仅支持 NVIDIA-SMI。不兼容选择在私密配置写入前拒绝，不允许旧程序忽略 DCGM 设置后默默采集 NVIDIA-SMI。format / 配置 / 报告版本不变，旧格式清单继续可读；新增能力字段需要理解它的部署器。

## 离线产物

| 文件 | 内容 |
| --- | --- |
| `server.tar.gz` | `wheels/*.whl`、独立 `bootstrap/pip-*.whl`、两份精确版本 / SHA-256 requirements |
| `agent.py` | 与 `node_reporter/python/reporter.py` 字节一致，包括末尾换行；旧产物名仅为部署兼容 |
| `release.json` | format / application / version / config / report、来源摘要、产物大小和摘要、节点上报实现能力、中心目标与逐成员清单 |

`center` 声明 `installation=venv-ensurepip-pip`、`module=monitor`、目标 Python / ABI / libc / SQLite 和两份 requirements 路径。`source` 记录 Git revision（未提交工作区有 dirty 标记）、uv.lock 与前端 lock 的 SHA-256。运行 wheel 包含 `monitor` 和装配静态资源，不包含工具、测试、节点上报程序、控制端代码、实际配置或数据库。

构建检查前端来源 / 输出 receipt、源码链接、Python 3.6 节点上报程序语法、wheel 元数据 / 目标兼容性及锁定依赖闭包；归档顺序 / 时间戳固定，可重复构建。manifest 最后发布；消费者仍须核验整个文件集合，不能把文件存在视为构建成功。SHA-256 只证明一致性，不是签名，来源必须可信。

配置与发布分离：外部适配器使用显式 `--tool-python` / `--release-dir`，在生成配置前校验产物与 describe；`--build-release` 仅显式离线装配，不下载依赖。输入发布目录是只读资源，不能与私密输出重叠。控制器仍可适配历史 format 1；新应用不再构建 format 1，不把它当 wheel 处理。

## 安装与运行

1. 校验受信任渠道提供的 manifest 摘要、外层大小 / 摘要、完整归档成员及 wheel / RECORD / hash pins；拒绝路径穿越、链接、重复、额外和缺失成员。
2. 用已批准解释器验证目标、venv / ensurepip；预检用户 manager、新端口和私密路径。
3. 在最终 `releases/<archive-sha>/venv` 路径创建环境，不复制或移动 venv。ensurepip 后用随包 pip 离线安装，`--no-index --no-deps --only-binary=:all: --require-hashes`；离线完整依赖解析、pip check 和安装清单审计必须通过。
4. 使用候选配置执行无数据库 / bind 副作用的 `python -I -m monitor --check-config PATH`，再以临时 DB / 回环地址启动候选，检查 viewer 鉴权、运行身份、overview 和实际 JS / CSS。
5. 记录持久事务和旧托管输入 / 服务状态后才停旧服务；同端口此时再查。发布配置 / 单元 / 选择信息，reload / enable / start，通过真实服务 MainPID、发行摘要、版本和鉴权 readiness 后提交。

```bash
/absolute/releases/<archive-sha>/venv/bin/python -I -m monitor \
  --config /absolute/server.json --host 127.0.0.1 --port 8080 \
  --db /absolute/existing-history.sqlite3
```

部署设置 `CLUSTERLENS_RELEASE_ID=<archive-sha>`，并清除继承的 `CLUSTERLENS_VIEWER_TOKEN`。`/api/runtime` 始终需要 viewer 身份，返回 application / version / pid / release_id；公开 health 仍只有 `{"status":"ok"}`。一中心一进程一 worker，正常停止等待在途写入和响应，再关闭调度 / Store。

## 恢复、回退和保留策略

部署控制器负责实现事务，应用不控制 systemd。HQLab 适配器已实现每节点锁、持久 journal、自动失败恢复、显式 recover / rollback；详细操作见部署仓库运维文档。

- 异常激活恢复旧托管配置、单元、助手和 enable / running 状态；恢复无法验证则保留 journal 并报错，不输出成功标记。
- 中断事务阻止下一次安装 / 回退，必须先显式 recover。rollback 切换上一份成功输入；不是数据库备份恢复。
- **从不回退、复制覆盖、移动或合并 live SQLite / WAL。** 新版本已成功写入的历史也保留。未来不兼容数据库迁移必须另有显式设计，本版本没有这种迁移。
- 旧 v1 源码和已准备环境保留，以便恢复旧单元。未完成环境不会覆盖重用或递归删除，应在确认无引用后人工处理。
- 卸载先停用服务，清除托管凭据及事务中的凭据副本；默认保留数据库、linger、纯代码环境、恢复助手和未知文件。不做自动环境 GC。
- 单中心切换不是跨节点事务。节点上报程序更新失败或节点不可达，不会撤销其他节点已成功的操作；必须如实检查 recap 和实际报告。

更新源码、配置、账户、令牌与数据库是不同操作，不可隐式捆绑。正常更新保持凭据与历史路径；跨账户迁移另行授权并正确处理 WAL。
