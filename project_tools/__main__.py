"""Public project CLI: python3 -m project_tools {configure,build,check-config,describe}."""

import argparse
import json
from pathlib import Path

from monitor.config import CONFIG_VERSION, load_config
from monitor.schemas.gpu import GPU_PROVIDERS
from monitor.ui import load_ui

from .assets import assemble
from .configure import generate
from .release import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser(
        "configure", help="从通用节点 JSON 生成配置，保留已有令牌"
    )
    config.add_argument("--nodes", required=True, type=Path)
    config.add_argument("--url", required=True, help="节点上报的完整 HTTP(S) 地址")
    config.add_argument("--output", type=Path, default=Path("config/local"))
    config.add_argument("--interval", type=int)
    config.add_argument("--stale-after", type=int)
    config.add_argument("--retention-days", type=int)
    config.add_argument(
        "--ui", type=Path, help="UI JSON；省略时保留已有设置，空对象恢复应用默认"
    )
    release = commands.add_parser(
        "build", help="离线装配 format=2 发布；不下载依赖或运行 npm install"
    )
    release.add_argument("--output", type=Path, default=Path("dist"))
    release.add_argument("--wheelhouse", type=Path, help="已准备的 uv 锁定 wheel 缓存")
    release.add_argument("--frontend", type=Path, help="已构建的公开前端资源目录")
    wheels = commands.add_parser(
        "prepare-wheels", help="显式从 uv.lock 中的官方 HTTPS 地址获取目标 wheel"
    )
    wheels.add_argument("--output", type=Path, default=Path(".build/wheels"))
    assets = commands.add_parser(
        "assets", help="装配已构建的前端到可安装运行包，不运行 Node.js"
    )
    assets.add_argument("--frontend", type=Path)
    schemas = commands.add_parser(
        "schemas", help="从应用模型导出公开 JSON Schema，不读取配置"
    )
    schemas.add_argument("--output", type=Path, default=Path("contracts"))
    check = commands.add_parser(
        "check-config", help="校验中心配置，不监听、不采集、不写数据库"
    )
    check.add_argument("path", type=Path)
    commands.add_parser("describe", help="输出无凭据的 CLI / 配置版本契约 JSON")
    args = parser.parse_args()
    try:
        if args.command == "describe":
            print(
                json.dumps(
                    {
                        "contract_version": 2,
                        "application": "hqlab-monitor",
                        "config_version": CONFIG_VERSION,
                        "report_version": 1,
                        "release_formats": [2],
                        "agents": ["python"],
                        "gpu_providers": list(GPU_PROVIDERS),
                        "prepared_environment_required": True,
                    }
                )
            )
        elif args.command == "build":
            build(args.output, wheelhouse=args.wheelhouse, frontend=args.frontend)
            print("发布产物已生成：server.tar.gz、agent.py、release.json（format=2）")
        elif args.command == "prepare-wheels":
            from .wheels import prepare

            print(json.dumps(prepare(args.output), ensure_ascii=False))
        elif args.command == "assets":
            assemble(args.frontend)
            print("公开前端资源已装配；未读取运行配置")
        elif args.command == "schemas":
            from monitor.schemas import export_schemas

            from .files import private_write

            args.output.mkdir(parents=True, exist_ok=True)
            for name, schema in export_schemas().items():
                private_write(
                    args.output / (name + ".schema.json"),
                    json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                )
            print("公开模型 Schema 已导出；不包含凭据或运行状态")
        elif args.command == "check-config":
            load_config(args.path)
            print("中心配置有效")
        else:
            path = args.output / "server.json"
            old = load_config(path)[0] if path.exists() else {}
            interval = (
                args.interval if args.interval is not None else old.get("interval", 30)
            )
            if args.stale_after is not None:
                stale = args.stale_after
            elif old and interval == old.get("interval", 30):
                stale = old.get("stale_after", 100)
            else:
                stale = max(old.get("stale_after", 100), interval * 3 + 10)
            retention = (
                args.retention_days
                if args.retention_days is not None
                else old.get("retention_days", 30)
            )
            generate(
                args.output,
                json.loads(args.nodes.read_text()),
                args.url,
                interval,
                stale,
                retention,
                ui=load_ui(args.ui) if args.ui else None,
            )
            print("配置已生成；保留已有令牌。未连接服务器，也未创建或移动数据库。")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"操作失败：{exc}\n")


if __name__ == "__main__":
    main()
