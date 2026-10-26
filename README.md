# ClusterLens

独立仓库：**https://github.com/tendo518/cluster-lens**（private）。项目名为 **ClusterLens**，原 HQLab Monitor 的 Python 运行包及兼容接口保持不变。

独立的只读服务器 / GPU 监控项目：节点本地采集并推送，中心接收、保存 SQLite 历史并提供 Web 页面。**整个目录可以单独复制或检出后使用，不需要父目录、Ansible inventory、SSH 配置或运维环境。**

## 环境要求

| 用途 | 要求 |
| --- | --- |
| 中心、项目工具 | Python 3.9+、SQLite 3.25+，仅标准库 |
| Linux 节点采集器 | Python 3.6+；GPU 指标需要 `nvidia-smi` |
| 浏览器回归测试 | Node 22+、Chrome/Chromium，仅开发机需要 |

前端是 HTML / JS / CSS / SVG，无构建和 npm 安装步骤。普通运行、配置生成和发布不要求 uv、pip、Node 或 Ansible。`pyproject.toml` / `uv.lock` 是本项目自己的零依赖环境描述，uv 用户可选择在此目录执行 `uv sync`；不是必须步骤，也不使用其他项目的虚拟环境。

## 获取源码

需要拥有私有仓库的读取权限：

```bash
gh repo clone tendo518/cluster-lens
cd cluster-lens
```

此仓库只保存监控应用及其历史，不包含集群 inventory、Ansible playbook、Vault 文件或真实凭据。部署方可通过 Git submodule 固定应用版本，也可独立检出后调用公开 CLI，见[集成契约](docs/integration.md)。

## 快速运行

以下命令均在**本项目目录**执行：

```bash
# 隔离演示，不连接任何真实节点
python3 -m monitor --demo

# 有配置后启动真实接收中心
python3 -m monitor --config /path/to/server.json --db /path/to/monitor.sqlite3
```

默认监听 `127.0.0.1:8080`。直接运行时默认数据库在本项目 `data/`；正式运行建议显式指定 `--db`。演示 / 真实模式不能共用数据库。演示使用 `demo-*` 节点和 `192.0.2.0/24` 文档地址，与真实集群无关；已有演示库不会被自动删除，想重新生成完整模拟历史时请显式使用新的演示数据库路径。

### 不依赖 Ansible 的配置生成

先复制并修改 [examples/nodes.json](examples/nodes.json)，其中仅有公开的节点名、展示地址和分组。`192.0.2.10` 是示例地址，不是实际节点。`--url` 应改为**所有节点都能访问的中心地址**；下面的 loopback 例子仅用于单机试运行。

```bash
python3 -m project_tools configure \
  --nodes examples/nodes.json \
  --url http://127.0.0.1:8080/api/agent/report \
  --output config/local

python3 -m project_tools check-config config/local/server.json
python3 -m monitor --config config/local/server.json --db data/monitor.sqlite3
```

生成独立 viewer / 逐节点令牌，配置目录 `0700`、文件 `0600`；重复执行保留已有令牌和未显式修改的采集参数，不打印令牌、不接触数据库、不连接节点。不要删除有效配置后重新生成，否则凭据会轮换。配置字段、分发范围及手动验证方式见 [配置契约](docs/configuration.md)。

采集器不传参数时仅打印本机诊断；传 `--config` 时采集并上报一次，**不是长期后台进程**。正式调度由部署方使用 systemd user service / timer + linger，不提供 cron / nohup 回退。

## 发布

```bash
python3 -m project_tools build --output dist
```

产物为 `server.tar.gz`、`agent.py`、`release.json`。中心包内部稳定为 `monitor/...`，解压后可直接 `python3 -m monitor`；agent 是独立单文件。发布不读取配置或历史，不包含测试、项目工具、SSH 凭据或部署脚本，不需要远端安装 Python 包。

这是源码 / 运行归档发行方式，不要求也不声明 `pip install .`。部署工具通过公开 CLI 和 JSON / 文件产物集成，而不是导入内部 Python 模块；详见 [项目与部署边界](docs/integration.md)。

## 开发与测试

```bash
python3 -S -m unittest discover -s tests -v
node tests/browser-smoke.mjs
```

浏览器测试使用本地隔离演示服务，不读取真实 inventory，不连接真实服务器；默认用 `python3`，可通过 `PYTHON`、`CHROME` 覆盖。`-S` 测试验证没有第三方 Python 运行依赖。

若安装了 Just，可使用独立命令：

```bash
just                       # 列出命令，不隐式启动服务
just demo
just configure             # 默认使用单机示例；真实接入请显式传节点清单和 URL
just run config/local/server.json
just build
just test
just check                 # JS 语法检查
just browser-test
```

## 资源归属

```text
monitor/              Python 运行包与 static/ 前端
project_tools/        通用配置生成、校验和确定性发布工具；不下发到运行中心
tests/                应用/API/发布/独立性/浏览器测试
examples/             无凭据的公开配置输入示例
docs/                 应用行为、配置与部署集成契约
pyproject.toml        本项目 Python 要求和零依赖声明
uv.lock               本项目独立锁文件（uv 可选）
Justfile              只引用本项目的开发命令
config/、data/、dist/  本地私密配置、历史和可重建产物；gitignored
```

## 名称与兼容性

仓库及 Python 项目元数据名称为 `cluster-lens`。为避免拆仓影响现有安装，下列兼容接口保持不变：

- 运行命令 `python3 -m monitor`、`python3 -m project_tools`。
- `monitor/...` 归档路径、version=1 JSON、发布标识 `application: "hqlab-monitor"`。
- `HQLAB_MONITOR_TOKEN` 环境变量、浏览器现有会话 / 置顶 / 外观存储键。
- 现有 HQLab 页面标识与 HTTP Server 标识；此次只拆分仓库，不进行界面品牌迁移。

更名不要求轮换令牌、重建数据库或变更部署方的服务名称；这些变更若需要应单独规划迁移。

应用不负责 SSH 连接、账户创建、sudo、linger 授权、跨账户迁移或集群卸载。这些属于使用本应用的运维工程；Ansible 是一种部署实现，不是应用的运行依赖。

## 页面与数据

工作空间菜单顺序：运行概览 → 使用率统计 → 服务器 → 进程与用户。原生即时锚点支持键盘、前进/后退及登录后定位；高亮随滚动和布局变化更新。

支持置顶关注、过滤与进程分页、历史图表、统计和 CSV、自动/手动明暗主题与六种配色。viewer 令牌通过 `Authorization` 传递，网页会话仅存于 `sessionStorage`；置顶和外观偏好存于 `localStorage`，不包含令牌。详细行为、安全限制和统计定义见 [使用说明](docs/user-guide.md)。
