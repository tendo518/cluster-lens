"""Public project CLI: python3 -m project_tools {configure,build,check-config}."""

import argparse
import json
from pathlib import Path

from monitor.config import load_config

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
    release = commands.add_parser("build", help="生成无凭据的运行发布包与单文件 agent")
    release.add_argument("--output", type=Path, default=Path("dist"))
    check = commands.add_parser(
        "check-config", help="校验中心配置，不监听、不采集、不写数据库"
    )
    check.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "build":
            build(args.output)
            print("发布产物已生成：server.tar.gz、agent.py、release.json")
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
            )
            print("配置已生成；保留已有令牌。未连接服务器，也未创建或移动数据库。")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"操作失败：{exc}\n")


if __name__ == "__main__":
    main()
