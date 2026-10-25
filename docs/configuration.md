# 运行配置契约（version=1）

应用只认识 JSON 配置，不读取 inventory、SSH 用户、端口、私钥或部署目录。配置生成入口为：

```bash
python3 -m project_tools configure --nodes examples/nodes.json \
  --url http://127.0.0.1:8080/api/agent/report --output config/local
```

`--nodes` 是非空 JSON 数组，每项仅允许 `name`、`address`、可选 `groups`：

- `name`：稳定的节点标识，用于报告鉴权、历史关联及 agent 配置文件名；生成器要求 1—128 个字母、数字、点、横线或下划线，不能为 `.` / `..`，不能重复。
- `address`：公开展示信息，1—255 个字符；**不作为中心 SSH 采集目标**。
- `groups`：字符串数组，默认空。无需采用任何 Ansible 分组规则。
- 不接受令牌、私钥、SSH 变量等额外字段。生成器负责产生独立令牌，不从节点输入复制凭据。

## 输出与分发

| 文件 | 接收方 / 用途 |
| --- | --- |
| `server.json` | 只给中心，含全部节点上报令牌及 viewer 令牌 |
| `agents/<name>.json` | 只给对应节点，不能把整个目录分发给所有节点 |
| `viewer-token` | 只给授权网页查看者，不放入 URL 或公开产物 |

所有文件均属于私密配置。不要提交到版本库，不要打印或附在部署日志中。重复生成保留已有 viewer 及同名节点令牌，新增节点获得新令牌；删除节点只删除本地对应 agent 配置，**不卸载远端服务，也不删除历史**。

保留原配置目录，修改输入清单后再生成。先让中心接受新的节点身份，再启动新 agent。不要用删除配置的方式“清理缓存”；删除后重新生成会轮换凭据，旧 agent 随即无法鉴权。

## 中心 JSON

生成器维护以下字段；下面只描述结构，不提供可误用于生产的固定令牌：

| 字段 | 说明 |
| --- | --- |
| `version` | `1` |
| `interval` | 5—3600 秒，默认 30 |
| `stale_after` | 至少 30 秒且至少两倍 interval，最大 86400；首次默认至少 `3 × interval + 10` |
| `retention_days` | 1—365 天，默认 30 |
| `viewer_token` | 16—256 位无空白的可打印 ASCII 字符，由安全随机生成器生成 |
| `hosts` | 非空节点列表，每项含 `name`、`address`、`groups`、`agent_token` |

所有 agent 令牌必须彼此不同，并与 viewer 令牌不同。现有配置无效时拒绝覆盖，不静默重置凭据。可用 `python3 -m project_tools check-config /path/to/server.json` 只校验配置；成功退出码 0，失败非零，不启动服务或修改数据库。

```bash
python3 -m project_tools configure --nodes examples/nodes.json \
  --url http://127.0.0.1:8080/api/agent/report --output config/local \
  --interval 60 --stale-after 190 --retention-days 90
```

再次运行时，未显式指定的参数从原 `server.json` 恢复；`--url` 每次必须明确给出。改变 interval 且未指定离线阈值时，会按新周期提高阈值。

中心启动示例：

```bash
python3 -m monitor --config config/local/server.json \
  --host 127.0.0.1 --port 8080 --db data/monitor.sqlite3
```

- `--host` / `--port` / `--db` 是启动参数，不是配置文件字段；正式运行建议使用数据库绝对路径。
- 默认只监听本机。跨机器部署需正确设置监听地址、防火墙和 agent 上报 URL。
- `HQLAB_MONITOR_TOKEN` 若设置，会覆盖配置中的 viewer 令牌。部署服务应清除意外继承的此变量；不能与 agent 令牌相同。
- 数据库路径不会从其他项目或用户账户自动推断、迁移、合并或覆盖。演示与真实模式禁止共用数据库。

## Agent JSON 与执行

字段为 `version=1`、`host`、`url`、`token`、`interval`、`timeout`，可选 `ca_file` 用于 TLS 自定义 CA。`host` 必须精确匹配中心节点名，`token` 为此节点独有令牌。生成器默认 timeout 为 8 秒。

`url` 必须为 HTTP(S) 完整上报端点 `/api/agent/report`，不得包含用户名、密码、查询参数或片段。节点忽略代理环境变量、不跟随重定向，HTTPS 校验证书；不提供关闭 TLS 验证的选项。

```bash
# 源码形式：只在本机执行，不使用 SSH 获取其他节点信息
python3 monitor/remote_probe.py
python3 monitor/remote_probe.py --config config/local/agents/node-a.json

# 发布形式（由部署方分发 dist/agent.py 和该节点配置）
python3 /path/to/agent.py --config /path/to/agent.json
```

不带参数仅打印本机诊断；带配置时只采集 / 上报一次。请勿在生产节点反复执行演示配置或把 viewer 令牌当成 agent 令牌。正式常驻调度使用普通用户 systemd service / timer + linger，由部署工程配置；需要保证间隔与中心一致、禁止重叠、设置超时，退出后不积压旧报告。

## 接口与安全边界

- `POST /api/agent/report`：逐节点独立 Bearer 令牌，只能上报自身身份。
- `GET /api/overview`、`GET /api/history`、`GET /api/export.csv`：viewer 令牌。
- `GET /healthz`：最小健康检查，不泄露节点、指标或凭据；返回成功不等于 agent 已成功上报。
- 静态页面允许未登录加载，真实指标需要鉴权。网页不提供主机管理、进程终止或远端命令执行接口。
- HTTP 仅用于可信内网；其他网络应使用 HTTPS 反向代理和访问控制。系统不是公网账户 / 抗滥用平台。
- 节点与中心应保持 NTP / chrony 同步。拒绝过旧、未来和乱序报告，不改系统时钟、不通过放宽校验掩盖故障。

具体时效、统计权重、缺失数据和进程采集口径见 [使用说明](user-guide.md)。
