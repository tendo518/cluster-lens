"""Central push receiver: python -I -m monitor --config /path/to/server.json."""

import argparse
import ipaddress
import json
import logging
import os
import signal
import sqlite3
from pathlib import Path

from . import APPLICATION, __version__
from .api.app import runtime_identity
from .assets import Assets
from .config import load_config, valid_token
from .lifecycle import MonitorRuntime
from .paths import default_database
from .schemas import CONFIG_VERSION, REPORT_VERSION
from .server import MonitorServer
from .service import MonitoringService
from .store import Store
from .ui import load_ui, parse_ui, render_page

LOG = logging.getLogger(__name__)


def check_sqlite():
    if sqlite3.sqlite_version_info < (3, 25, 0):
        raise ValueError(
            "Web 服务需要 SQLite 3.25+，请使用较新的 Python / SQLite 运行时"
        )


def preflight(config_path, assets=None):
    """Imports, config and resources only: no Store, mkdir, connect, bind or migration."""
    check_sqlite()
    config, _, credentials = load_config(config_path)
    effective_token(config["viewer_token"], credentials)
    runtime_identity()
    resources = Assets(assets)
    resources.validate()
    render_page(
        resources.read("index.html").decode("utf-8"), parse_ui(config.get("ui", {}))
    )
    return {
        "application": APPLICATION,
        "version": __version__,
        "config_version": CONFIG_VERSION,
        "report_version": REPORT_VERSION,
        "status": "ok",
    }


def effective_token(token, credentials):
    token = os.environ.get("CLUSTERLENS_VIEWER_TOKEN") or token
    if token and not valid_token(token):
        raise ValueError(
            "CLUSTERLENS_VIEWER_TOKEN / viewer_token 必须是至少 16 位无空白的可打印 ASCII 令牌"
        )
    if token in credentials.values():
        raise ValueError("网页访问令牌不能与节点上报令牌相同")
    return token


def main():
    parser = argparse.ArgumentParser(
        description="ClusterLens 监控中心（接收节点上报，不通过 SSH 采集）"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="HTTP 监听地址；部署时使用 0.0.0.0"
    )
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument(
        "--config", type=Path, help="中心 JSON 配置（含节点清单和独立令牌）"
    )
    parser.add_argument(
        "--check-config",
        type=Path,
        metavar="PATH",
        help="无副作用地检查中心配置及运行资源，输出 JSON 后退出",
    )
    parser.add_argument(
        "--assets",
        type=Path,
        metavar="DIRECTORY",
        help="显式静态资源目录（开发 / 浏览器测试）；默认使用安装包资源",
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
    if args.check_config is not None:
        if args.demo or any(
            value is not None
            for value in (
                args.config,
                args.db,
                args.ui,
                args.interval,
                args.retention_days,
            )
        ):
            parser.error("--check-config 不能与运行配置或数据库选项一起使用")
        try:
            result = preflight(args.check_config, args.assets)
        except (ValueError, OSError, sqlite3.Error):
            # Even malformed files and resource names must not echo credentials.
            parser.exit(1, "配置或运行资源检查失败，请检查配置及安装环境。\n")
        print(json.dumps(result, ensure_ascii=False))
        return
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
        check_sqlite()
        runtime_identity()
        resources = Assets(args.assets)
        resources.validate()
        credentials, config = {}, {}
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
        try:
            token = effective_token(token, credentials)
        except ValueError as exc:
            parser.error(str(exc))
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
        db = args.db if args.db is not None else default_database(demo=args.demo)
        store = Store(db, mode="demo" if args.demo else "live")
        if args.demo:
            LOG.info("演示模式：生成最近 24 小时模拟历史，不会连接真实服务器")
            seed_demo(store, hosts, interval=interval)
        service = MonitoringService(
            hosts,
            store,
            interval=interval,
            stale_after=stale_after,
            retention_days=retention,
            demo=args.demo,
            credentials=credentials,
        )
        runtime = MonitorRuntime(service)
        server = MonitorServer(
            (args.host, args.port),
            service,
            token,
            ui=ui,
            runtime=runtime,
            assets=args.assets,
        )
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
        server.request_shutdown()

    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, shutdown)
        # FastAPI lifespan starts/stops runtime. Uvicorn drains HTTP writes first.
        server.serve_forever()
    finally:
        try:
            server.server_close()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    main()
