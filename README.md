# ClusterLens

只读服务器与 NVIDIA GPU 监控。节点在本机采集并通过 HTTP(S) 推送，中心提供仪表盘、历史统计和 CSV 导出；运行时不使用 SSH，也不执行主机管理操作。

## 运行环境

| 组件 | 要求 |
| --- | --- |
| 中心 | Python 3.9+、SQLite 3.25+ |
| 采集端 | Linux、Python 3.6+；GPU 指标需要 `nvidia-smi` |
| 浏览器测试 | Node.js 22+、Chrome / Chromium |

中心与采集端仅使用 Python 标准库；前端是原生 JS / CSS / SVG，无需 pip、Node 构建或第三方运行依赖。本项目以源码和运行归档发行，不提供 `pip install .` 安装方式。

以下命令在应用仓库根目录执行。

## 本地体验

```bash
python3 -m monitor --demo
```

访问 `http://127.0.0.1:8080`。演示只生成模拟指标，数据库为 `data/monitor-demo.sqlite3`，不读取真实节点配置、不接收真实上报。

应用默认使用 ClusterLens 标题与字母图标。可以直接替换站点展示配置：

```bash
python3 -m monitor --demo --ui examples/ui.json
```

## 建立真实监控

1. 按 `examples/nodes.json` 编写公开节点清单，使用稳定的名称和展示地址。
2. 指定采集端可访问的中心上报地址，生成私密配置。
3. 中心只接收 `server.json`；每个节点只接收自己的 agent 配置。
4. 启动中心，再在节点执行采集器；周期调度和服务账户由部署方管理。

```bash
python3 -m project_tools configure \
  --nodes examples/nodes.json \
  --url http://127.0.0.1:8080/api/agent/report \
  --ui examples/ui.json --output config/local

python3 -m project_tools check-config config/local/server.json
python3 -m monitor --config config/local/server.json \
  --host 127.0.0.1 --port 8080 --db data/monitor.sqlite3
```

上面的 loopback 地址仅适合单机验证；多机环境应配置实际可达的 URL、监听地址、TLS 和防火墙。在另一终端本机验证其中一个示例节点：

```bash
python3 monitor/remote_probe.py --config config/local/agents/node-a.json
```

授权查看者通过生成的 `viewer-token` 登录；不要把令牌写入 URL、命令行或版本库。重复生成会保留已有 viewer / 同名节点令牌；不要删除配置目录来更新代码。

## 配置与职责

- **应用配置**：采样周期、离线阈值、保留期、节点身份与 UI 展示。
- **UI 配置**：标题、品牌名称、字母标记或 PNG 图标、工作区文字、页脚、初始明暗主题和配色。
- **部署配置**：中心地址、SSH、账户、文件路径、服务、调度和迁移，由部署工程管理；应用不读取 inventory 或父目录。

显式配置优先于应用默认值；浏览器保存的个人主题 / 配色优先于站点默认。UI 是公开信息，不应包含秘密。完整字段、优先级和图标配置见[配置参考](docs/configuration.md)。

## 构建与测试

```bash
python3 -B -S -m project_tools build --output dist
python3 -S -m unittest discover -s tests -v
node tests/browser-smoke.mjs
```

发布输出为 `server.tar.gz`、`agent.py`、`release.json`，不含配置、数据库、测试或部署工具。中心在归档解压根目录运行 `python3 -m monitor`，采集端执行独立 `agent.py`。集成方应使用[公开 CLI 和产物契约](docs/integration.md)，不导入应用内部模块。

可选 Just 入口：`just demo`、`just configure`、`just run <配置>`、`just build`、`just test`、`just check`、`just browser-test`。Ruff 用于 Python 静态检查；uv 仅是可选开发工具，不是运行要求。

## 使用与安全

支持服务器 / 进程过滤、25 / 50 / 100 条分页、置顶、节点详情、历史图表、统计、CSV、六种配色及明暗模式。缺失指标保留为未知，离线不伪装为 0%；统计定义见[使用指南](docs/user-guide.md)。

viewer 和逐节点令牌彼此隔离；页面会话令牌存 `sessionStorage`，外观与置顶存 `localStorage`。系统不提供公网多用户账户、作业计费或主机控制能力。HTTP 仅用于可信内网，其他网络需 HTTPS 和访问控制。
