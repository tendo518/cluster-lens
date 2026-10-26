# ClusterLens 与部署工程的边界

应用仓库为 [tendo518/cluster-lens](https://github.com/tendo518/cluster-lens)，与部署仓库分别提交和发布。私有仓库需独立授权；部署方建议使用 Git submodule 固定到已验证的提交，而不是每次部署自动拉取最新分支。普通配置生成 / 部署不应隐式调用 `git pull` 或更新引用。

Ansible 仓库可按如下流程引用应用（初次添加由维护者执行，不是每次部署步骤）：

```bash
git submodule add https://github.com/tendo518/cluster-lens.git projects/monitor
# 在部署仓库提交 .gitmodules 和子模块引用，其他操作者再使用：
git submodule update --init --recursive
```

`projects/monitor` 只是部署方选择的检出路径，不是应用运行依赖。既有部署可继续使用此路径；不要为更名自动移动数据库或换发凭据。

## 两个独立职责

**本项目拥有：**运行包、前端、上报协议、JSON 配置规则、通用配置生成、发布产物、应用测试和文档。

**部署工程拥有：**主机发现 / inventory、SSH 用户和密钥、实际配置的存放与分发、账户 / 权限 / linger、用户级 service / timer、安装 / 更新 / 卸载、跨账户数据库迁移及部署测试。

依赖方向为“部署工程调用本项目的公开工具与运行接口”，而不是应用导入部署代码。应用模型仅包含公开节点信息；任何 SSH 变量留在部署工程，不能通过共享模型夹带进中心。

## 本地工具接口

部署方在本项目根目录、使用 Python 3.9+ 调用以下稳定入口；无需安装本项目到控制端虚拟环境：

```bash
python3 -B -S -m project_tools build --output /absolute/path/to/staging
python3 -B -S -m project_tools configure \
  --nodes /absolute/path/to/public-nodes.json \
  --url https://monitor.example.org/api/agent/report \
  --output /absolute/path/to/private-config \
  --interval 30 --stale-after 100 --retention-days 30
python3 -B -S -m project_tools check-config /absolute/path/to/private-config/server.json
```

输入 / 输出路径若来自不同工作目录，应使用绝对路径。`build`、`configure` 和 `check-config` 都不连接节点；成功退出码 0，失败非零。标准输出仅报告状态，**不输出凭据**。节点 JSON 与输出配置格式见 [配置契约](configuration.md)。

配置目录可由部署工程保管在自己的工作区，不必放进 monitor 项目。应用工具不关心 inventory 文件、运维仓库名称或集群主机命名。不要在同一输出目录并发运行两个配置生成 / 部署任务；这些操作不是跨文件或跨节点事务。

## 发布产物契约（format_version=1）

`build` 生成：

| 产物 | 内容 |
| --- | --- |
| `server.tar.gz` | 归档路径全部位于 `monitor/`；只含运行 `.py` 和允许的静态资源 |
| `agent.py` | Python 3.6+ 单文件采集 / 上报脚本，可独立复制到节点 |
| `release.json` | `format_version: 1`、`application: "hqlab-monitor"`，以及 `files` 中两个产物各自的 `sha256` |

发布包排序、归档元数据和压缩时间戳固定，可重复构建。包内没有其他项目、配置、令牌、数据库、项目工具、测试或部署代码。SHA-256 用于一致性校验，**不是来源签名**；源代码和产物来源必须可信。

部署方消费产物时验证版本和校验和，不再扫描应用内部源码目录。以后即使采集器源码位置改变，只要 `agent.py` 产物契约不变，部署模板无需改动。

中心解压后的入口保持为：

```bash
python3 -m monitor --config /absolute/path/to/server.json \
  --host 0.0.0.0 --port 8080 --db /absolute/path/to/monitor.sqlite3
```

当前工作目录应是解压根目录；有外部节点接入前先完成 HTTPS / 防火墙配置。每个 agent 使用：

```bash
python3 /absolute/path/to/agent.py --config /absolute/path/to/agent.json
```

## Ansible 集成建议

1. 控制端通过 `ansible-inventory` 解析选定主机，转换为公开 `name/address/groups` JSON，不发送 SSH 变量。
2. 调用 `build`，核对 `release.json`；调用 `configure`，保留既有凭据目录。
3. 限定目标范围并确认实际运行账户，先安装 / 校验中心，再启用新节点 agent。
4. 使用普通用户的 systemd 服务和 timer + linger；首次创建用户或迁移历史是单独的显式管理员操作，不在更新代码时自动执行。
5. 中心成功鉴权就绪后，验证节点完成一次实际上报，而非仅检查 timer 已启用。
6. 日常清理只移除可重建发布包和临时安装器，保留实际配置和历史。

不要把整个私密配置目录或 SSH 私钥下发给中心 / 每个 agent，不按端口杀其他进程，不因不可达而从清单静默移除节点，不擅自切换运行账户。

应用不规定某个实验室的 inventory 路径、中心主机、端口、用户名、UID/GID、数据库存放策略或 sudo 策略。这些设置应出现在部署工程，而非本项目。
