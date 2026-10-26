"""Central push receiver: python3 -m monitor --config /path/to/server.json."""

import argparse
import ipaddress
import logging
import os
import signal
import sqlite3
import threading
from pathlib import Path

from .collector import Collector
from .config import load_config, valid_token
from .paths import ROOT
from .server import MonitorServer
from .store import Store
from .ui import load_ui, parse_ui

LOG = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="ClusterLens 监控中心（接收节点上报，不通过 SSH 采集）"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="HTTP 监听地址；部署时使用 0.0.0.0"
    )
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument(
        "--config",
        type=Path,
        help="中心 JSON 配置（含节点清单和独立令牌）",
    )
    parser.add_argument(
        "--interval", type=int, help="演示采样间隔；真实模式在部署配置中设置"
    )
    parser.add_argument("--retention-days", type=int, help="覆盖历史保留天数（1—365）")
    parser.add_argument("--db", type=Path, help="SQLite 文件路径")
    parser.add_argument(
        "--ui", type=Path, help="UI JSON 文件；覆盖中心配置的 ui，亦可用于演示"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="仅使用模拟数据，不读取部署配置、不接收节点上报",
    )
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port 应为 0—65535")
    if args.interval is not None and (not args.demo or not 5 <= args.interval <= 3600):
        parser.error(
            "--interval 仅用于演示模式（5—3600）；真实模式请修改 JSON 配置中的 interval"
        )
    if args.retention_days is not None and not 1 <= args.retention_days <= 365:
        parser.error("retention-days 应为 1—365")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        if sqlite3.sqlite_version_info < (3, 25, 0):
            raise ValueError(
                "Web 服务需要 SQLite 3.25+，请使用较新的 Python / SQLite 运行时"
            )
        credentials = {}
        config = {}
        if args.demo:
            from .demo import demo_hosts, seed_demo

            hosts = demo_hosts()
            interval, stale_after, retention, token = args.interval or 30, 100, 30, None
        else:
            if args.config is None:
                raise ValueError("真实监控必须用 --config 指定中心 JSON 配置")
            config, hosts, credentials = load_config(args.config)
            interval = config.get("interval", 30)
            stale_after = config.get("stale_after", 100)
            retention = config.get("retention_days", 30)
            token = config["viewer_token"]
        ui = load_ui(args.ui) if args.ui else parse_ui(config.get("ui", {}))
        retention = args.retention_days or retention
        token = os.environ.get("CLUSTERLENS_VIEWER_TOKEN") or token
        try:
            local = (
                args.host == "localhost" or ipaddress.ip_address(args.host).is_loopback
            )
        except ValueError:
            local = False
        if not local and not token:
            parser.error(
                "非本机监听必须设置 CLUSTERLENS_VIEWER_TOKEN 或配置 viewer_token"
            )
        if token and not valid_token(token):
            parser.error(
                "CLUSTERLENS_VIEWER_TOKEN / viewer_token 必须是至少 16 位无空白的可打印 ASCII 令牌"
            )
        if token in credentials.values():
            parser.error("网页访问令牌不能与节点上报令牌相同")
        db = args.db or ROOT / "data" / (
            "monitor-demo.sqlite3" if args.demo else "monitor.sqlite3"
        )
        store = Store(db, interval, mode="demo" if args.demo else "live")
        if args.demo:
            LOG.info("演示模式：生成最近 24 小时模拟历史，不会连接真实服务器")
            seed_demo(store, hosts)
        collector = Collector(
            hosts,
            store,
            interval=interval,
            stale_after=stale_after,
            retention_days=retention,
            demo=args.demo,
            credentials=credentials,
        )
        server = MonitorServer((args.host, args.port), collector, token, ui=ui)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"启动失败：{exc}\n请检查 --config 指定的配置及运行环境。\n")
    address, port = server.server_address[:2]
    display_address = f"[{address}]" if ":" in address else address
    LOG.info(
        "%s监控已启动：http://%s:%s（%d 台节点，每 %ss 采样）",
        "演示" if args.demo else "上报式",
        display_address,
        port,
        len(hosts),
        interval,
    )
    LOG.info(
        "历史数据：%s；保留 %d 天；无上报 %ss 后判定离线", db, retention, stale_after
    )

    def shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    collector.start()
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        collector.stop()
        server.server_close()


if __name__ == "__main__":
    main()
