# 部署集成契约

## 职责与支持范围

应用拥有中心 wheel、独立 agent、前端、配置 / 报告规则与发布工具；部署方拥有 inventory、SSH、账户、实际配置、服务管理、安装路径和迁移。只通过公开 CLI、JSON 和产物集成，不导入应用内部模块，不注入 PYTHONPATH，不扫描运行源码布局。

新发布为 **format 2**；发行标识仍为 `hqlab-monitor`，Python 包入口仍为 `python -m monitor`。中心支持常规 CPython 3.13、Linux x86_64、glibc ≥2.17、SQLite ≥3.25，需 venv / ensurepip。已实际验证的平台为 CPython 3.13.14 / Linux x86_64；不是 ARM、Windows、musl、自由线程 Python 或其他 Python 小版本的兼容承诺。部署不得自动安装 Python 或提权补齐前提。

Python agent 仍是 3.6+ 标准库单文件。Node agent 和反向代理明确不在本版本交付范围，不能静默回退实现，也不自动修改 TLS、域名或网络拓扑。

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
  --url https://monitor.example.org/api/agent/report \
  --ui /absolute/site-ui.json --output /absolute/private-config
.venv/bin/python -m project_tools check-config /absolute/private-config/server.json
```

`uv sync`、`npm ci`、`prepare-wheels` 是显式依赖获取步骤。`build` 只消费已安装构建工具、有效前端 receipt 与经过 uv.lock 摘要核验的 wheel 缓存，缺失或损坏直接失败，不调用 npm / uv 下载。`just build` 会先执行本地前端构建，但不会准备依赖。`project_tools` 是源码侧工具，不打入运行 wheel。仅用 `uv run --project` 不会切换 cwd；需要 uv 入口时使用 `uv run --no-sync --directory /application ...`。

工具解释器保留其 venv 可执行路径，不对 Python 符号链接调用 resolve；现代工具不使用 `-S`。适配器清除 PYTHONPATH / PYTHONHOME。新 describe 契约：

```json
{"contract_version":2,"application":"hqlab-monitor","config_version":1,"report_version":1,"release_formats":[2],"agents":["python"],"prepared_environment_required":true}
```

配置器只接收公开节点字段及 UI 输入；成功状态输出不含令牌。配置规则及凭据保留见[配置参考](configuration.md)。

## 离线产物

| 文件 | 内容 |
| --- | --- |
| `server.tar.gz` | `wheels/*.whl`、独立 `bootstrap/pip-*.whl`、两份精确版本 / SHA-256 requirements |
| `agent.py` | 与 `agents/python/agent.py` 字节一致，包括末尾换行 |
| `release.json` | format / application / version / config / report、来源摘要、产物大小和摘要、agent 能力、中心目标与逐成员清单 |

`center` 声明 `installation=venv-ensurepip-pip`、`module=monitor`、目标 Python / ABI / libc / SQLite 和两份 requirements 路径。`source` 记录 Git revision（未提交工作区有 dirty 标记）、uv.lock 与前端 lock 的 SHA-256。运行 wheel 包含 `monitor` 和装配静态资源，不包含工具、测试、agent、控制端代码、实际配置或数据库。

构建检查前端来源 / 输出 receipt、源码链接、Python 3.6 agent 语法、wheel 元数据 / 目标兼容性及锁定依赖闭包；归档顺序 / 时间戳固定，可重复构建。manifest 最后发布；消费者仍须核验整个文件集合，不能把文件存在视为构建成功。SHA-256 只证明一致性，不是签名，来源必须可信。

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
- 单中心切换不是跨节点事务。agent 更新失败 / 不可达不会撤销其他节点已成功的操作；必须如实检查 recap 和实际报告。

更新源码、配置、账户、令牌与数据库是不同操作，不可隐式捆绑。正常更新保持凭据与历史路径；跨账户迁移另行授权并正确处理 WAL。
