# 配置参考

ClusterLens 只读取显式的 JSON 与启动参数，不发现 inventory、不搜索父目录，也不推断部署账户或数据库位置。除 CLI 特别说明外，路径相对于当前工作目录；跨工程调用建议使用绝对路径。

## 1. 生成配置

公开节点清单为非空 JSON 数组：

```json
[
  {"name": "node-a", "address": "192.0.2.11", "groups": ["compute"]},
  {"name": "node-b", "address": "192.0.2.12", "groups": ["compute"]}
]
```

地址是展示信息，不是 SSH 采集目标。每项仅接受 `name`、`address`、`groups`；名称使用 1—128 个字母、数字、点、横线或下划线，不能为 `.` / `..`，必须唯一。地址长度为 1—255；groups 是字符串数组，可省略。

```bash
python3 -m project_tools configure --nodes examples/nodes.json \
  --url https://monitor.example.org/api/agent/report \
  --ui examples/ui.json --output config/local \
  --interval 30 --stale-after 100 --retention-days 30
```

| 输出 | 分发范围 |
| --- | --- |
| `server.json` | 仅中心，包含全部节点令牌与 viewer 令牌 |
| `agents/<name>.json` | 仅对应节点 |
| `viewer-token` | 仅授权查看者 |

目录权限为 `0700`、文件为 `0600`，均不得入库或打印到共享日志。生成器不连接服务器、不修改数据库、不卸载远端节点。重复生成保留 viewer / 同名节点令牌；新增节点产生新令牌，移除节点会移除本地对应配置。不要并发生成同一目录。

### 生成优先级

- 采样 / 保留参数：显式 CLI → 现有 `server.json` → 默认值。
- `--url` 每次必须明确提供。
- `--ui`：提供文件时，按文件内容与应用默认值生成完整 UI；省略时保留现有 UI。传入内容为 `{}` 的 UI 文件可恢复默认展示。
- 改变 interval 且未指定 stale-after 时，自动提高离线阈值到至少 `3 × interval + 10`。
- 输入或现有凭据无效时拒绝替换，不静默轮换令牌。

## 2. 中心运行配置

`server.json` 是 `version=1` 对象：

| 字段 | 要求 / 默认值 |
| --- | --- |
| `version` | `1` |
| `interval` | 整数 5—3600 秒；默认 30 |
| `stale_after` | 整数，至少 30 秒且不少于两倍 interval；最大 86400；默认 100 |
| `retention_days` | 整数 1—365 天；默认 30 |
| `viewer_token` | 16—256 位无空白可打印 ASCII；生成器使用安全随机值 |
| `hosts` | 非空列表，含公开节点字段与逐节点 `agent_token` |
| `ui` | 可选公开展示对象，省略字段使用下表默认值 |

所有 agent 令牌必须彼此不同，并与 viewer 令牌不同。

```bash
python3 -m project_tools check-config /path/to/server.json
python3 -m monitor --config /path/to/server.json \
  --host 127.0.0.1 --port 8080 --db /path/to/history.sqlite3
```

监听地址、端口和数据库路径属于启动参数，不是 server.json 字段。默认只监听本机；非本机监听必须有有效访问令牌。环境变量 `HQLAB_MONITOR_TOKEN` 可覆盖 viewer 令牌，服务部署应避免意外继承它。演示和真实模式禁止共用数据库；应用不自动迁移、合并或清理其他数据库。

## 3. UI 配置

UI 文件是单独的 JSON 对象；也可以直接将同样的运行字段写入中心配置的 `ui`。它是公开信息，登录前即可看到，不能存放令牌或内部秘密。

| 字段 | 默认值 | 最大长度 |
| --- | --- | --- |
| `title` | `ClusterLens · 服务器监控` | 120 |
| `name` | `ClusterLens` | 40 |
| `subtitle` | `SERVER MONITORING` | 60，可为空 |
| `mark` | `C` | 2 |
| `avatar` | `CL` | 3 |
| `workspace` | `服务器集群` | 60 |
| `workspace_subtitle` | `Monitoring workspace` | 80，可为空 |
| `heading` | `集群运行监控` | 80 |
| `footer` | `ClusterLens` | 120，可为空 |
| `theme` | `system` | `system` / `light` / `dark` |
| `palette` | `green` | `green` / `blue` / `purple` / `amber` / `teal` / `rose` |
| `icon` | `null` | PNG data URI，见下文 |

文本禁止控制字符；未知字段、无效主题 / 图标直接报错。文本按纯文本转义，不作为 HTML 执行。长品牌名称会截断展示，完整名称保留在链接的可访问名称和提示中。

### 图标

默认使用 `mark` 生成随主题变化的字母图标。自定义图标需为 PNG，文件大小不超过 64 KiB、宽高各为 1—1024 像素：

```json
{
  "title": "Compute Center · 服务器监控",
  "name": "Compute Center",
  "mark": "C",
  "icon_file": "logo.png",
  "palette": "blue"
}
```

`icon_file` 仅用于 UI **输入文件**，相对于该 UI 文件解析。工具会把 PNG 编码为 `icon: "data:image/png;base64,..."` 写入生成的中心配置；`icon` 与 `icon_file` 不能同时提供。生成后不需要在中心保留输入文件或原始图片。

自定义 PNG 同时用于侧栏、登录标记与 favicon，切换配色不会替换图片。页脚和头像使用 `mark` / `avatar` 文本。运行配置仅接受内嵌 PNG，不接受 icon_file、外部 URL 或自定义 SVG；服务端只公开受限图像端点，不暴露任意文件读取能力。

### 运行时与浏览器优先级

```bash
python3 -m monitor --demo --ui examples/ui.json
```

运行参数 `--ui` → 中心配置 `ui` → 应用默认值。`--ui` 对整份 UI 使用替换语义，不与 server.json.ui 逐字段合并。站点文字、默认主题和配色在 HTML 首次响应中生效，无需等待鉴权或额外配置请求。

明暗 / 配色的最终优先级为：浏览器已保存的有效偏好 → 站点配置 → 应用默认值。自动主题跟随系统；损坏、被清除或无法读取的偏好回落到站点默认。管理员修改默认值不会覆盖用户的个人选择。

## 4. 采集端配置

独立 agent 使用 `version=1`、`host`、`url`、`token`、`interval`、`timeout`；可选 `ca_file` 指定自定义 TLS CA 文件。interval 为 5—3600 秒，timeout 为 1—30 秒、默认 8。host 必须精确匹配中心名称，token 仅属于该节点。

```bash
python3 /path/to/agent.py --config /path/to/agent.json
```

不带配置执行采集器只打印本机诊断；带配置执行一次采集 / 上报后退出。周期调度由部署方负责，需禁止重叠、限制总运行时间，不积压旧报告。

URL 必须是 HTTP(S) 完整 `/api/agent/report` 端点，不含用户名、密码、查询或片段。agent 忽略代理环境变量、拒绝重定向、校验 TLS；不支持关闭证书校验。多机部署前请检查路由、时钟和证书。

## 5. HTTP 接口

| 接口 | 鉴权与用途 |
| --- | --- |
| `POST /api/agent/report` | 逐节点 Bearer，只能上报自身身份 |
| `GET /api/overview` | viewer Bearer，实时状态 |
| `GET /api/history` | viewer Bearer，历史 / 统计 |
| `GET /api/export.csv` | viewer Bearer，导出统计 |
| `GET /healthz` | 无鉴权，仅最小存活信息 |
| 页面、静态资源、站点图标 | 无鉴权，不包含真实指标或令牌 |

健康检查成功不代表节点上报成功。HTTP 仅适合可信内网，其他网络需 HTTPS、访问限制与防火墙。统计和采样定义见[使用指南](user-guide.md)。
