# 部署集成契约

## 职责

ClusterLens 拥有运行包、页面、上报协议、配置校验、通用配置生成、发布工具和应用测试。部署方拥有主机发现、SSH、账户 / 权限、实际配置保管与分发、服务调度、安装卸载和数据迁移。

组件间的通信、错误与版本边界见[通信契约](protocols.md)。后续中心 wheel 安装与三部分目录属于[目标设计](project-layout-plan.md)，本页仍描述当前 format=1 的可用入口。

依赖方向只有“部署方调用应用的公开入口”。应用不读取部署仓库，不要求特定 inventory、中心主机、端口、账户、目录或服务管理器；部署方不应导入应用的内部 Python 模块。

## 控制端 CLI

使用 Python 3.9+，在应用根目录调用；不需要安装应用包或第三方依赖：

```bash
python3 -B -S -m project_tools build --output /absolute/staging
python3 -B -S -m project_tools configure \
  --nodes /absolute/nodes.json \
  --url https://monitor.example.org/api/agent/report \
  --ui /absolute/site-ui.json \
  --output /absolute/private-config \
  --interval 30 --stale-after 100 --retention-days 30
python3 -B -S -m project_tools check-config /absolute/private-config/server.json
```

三条命令均不连接节点。成功退出码为 0，失败非零；状态输出不包含凭据。路径跨工作目录时使用绝对路径。输入及优先级见[配置参考](configuration.md)。

部署方只传公开节点信息与应用配置，SSH 变量不进入应用模型。UI schema 及 icon_file 的处理属于应用；部署方只提供配置文件路径，不重复实现校验或 HTML 替换。

## 发布产物

| 产物 | 内容 |
| --- | --- |
| `server.tar.gz` | `monitor/` 下的中心运行 Python 与静态资源，不含 agent |
| `agent.py` | 从 `agents/python/agent.py` 构建的 Python 3.6+ 独立采集 / 上报脚本 |
| `release.json` | 格式版本、发行标识、两个产物的 SHA-256 |

清单结构：

```json
{
  "format_version": 1,
  "application": "hqlab-monitor",
  "files": {
    "server.tar.gz": {"sha256": "<64 hex characters>"},
    "agent.py": {"sha256": "<64 hex characters>"}
  }
}
```

`application` 是稳定的机器可读发行标识，不控制页面品牌。归档排序、元数据及压缩时间戳固定，可以重复构建；产物中没有配置、凭据、数据库、测试、项目工具或部署代码。SHA-256 证明内容一致，不是来源签名，部署方必须信任源码 / 产物来源。

部署模板消费这三个产物，不扫描应用源码位置。中心从解压根目录启动：

```bash
python3 -m monitor --config /absolute/server.json \
  --host 0.0.0.0 --port 8080 --db /absolute/history.sqlite3
```

采集端执行 `python3 /absolute/agent.py --config /absolute/agent.json`。中心 / agent 均使用普通用户权限；服务管理器、超时、调度间隔和 linger 由部署方配置。

## 推荐部署顺序

1. 获取并固定可信的应用版本；部署期间不隐式拉取最新分支。
2. 导出公开节点清单，构建并检查清单版本及 SHA-256。
3. 在受保护的持久配置目录生成配置，保留既有令牌。
4. 先更新中心并验证 viewer 鉴权，再启动新增节点的 agent。
5. 每个节点仅接收自己的配置；中心接收完整中心配置。
6. 验证 agent 实际完成一次上报，并检查实时状态与历史。
7. 清理可重建发布包和临时安装器，保留配置、令牌及数据库。

不要并发操作同一配置输出目录；生成、安装和多节点更新不是跨文件 / 跨节点事务。失败后应按节点检查结果，不能假定自动回滚。

## 源码引用与版本维护

应用可以独立 checkout，也可作为部署工程的 Git submodule。私有仓库需额外读取权限。父仓库应固定已验证提交；应用改动先在应用仓库提交并推送，再更新部署方引用。直接使用外部工作区时，部署方自行记录并验证版本。

配置与历史不是构建缓存。更新源码不应自动移动数据库、轮换令牌、切换账户、调整时钟或删除不可达节点。跨账户迁移需另外授权、保留源库并使用 SQLite backup 正确处理 WAL；不要只复制正在使用的主数据库文件。
