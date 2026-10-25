"""Read-only viewer API, authenticated node ingestion, and static dashboard."""

import csv
import hmac
import io
import json
import logging
import math
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .protocol import MAX_REPORT_BYTES

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/appearance.js": ("appearance.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/palettes.css": ("palettes.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
LOG = logging.getLogger(__name__)


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, collector, token=None):
        self.collector, self.store, self.token = collector, collector.store, token
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "HQLabMonitor/1.0"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        LOG.debug("HTTP %s", fmt % args)

    def respond(
        self, status, body, content_type="application/json; charset=utf-8", extra=None
    ):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, status, data):
        self.respond(
            status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        )

    def do_HEAD(self):
        self.do_GET()

    def history_args(self, query):
        now = time.time()
        try:
            start = float(query.get("start", [now - 3600])[0])
            end = float(query.get("end", [now])[0])
        except (ValueError, IndexError):
            raise ValueError("start / end 必须是 Unix 秒时间戳") from None
        if not all(math.isfinite(value) for value in (start, end)):
            raise ValueError("时间必须为有限数值")
        if start < 0 or end <= start or end - start > 31 * 86400 or end > now + 60:
            raise ValueError(
                "请选择有效时间范围（不超过 31 天，结束时间不能晚于当前时间）"
            )
        host = query.get("host", [""])[0]
        names = [host.name for host in self.server.collector.hosts]
        if host and host not in names:
            raise ValueError("未知的服务器")
        return start, end, host or None, names

    def do_GET(self):
        try:
            url = urlsplit(self.path)
            if url.path.startswith("/api/"):
                if self.server.token:
                    expected = "Bearer " + self.server.token
                    provided = self.headers.get("Authorization", "")
                    if not hmac.compare_digest(expected.encode(), provided.encode()):
                        self.json(401, {"error": "请输入有效的监控访问令牌"})
                        return
                if url.path == "/api/overview":
                    self.json(200, self.server.collector.overview())
                elif url.path in ("/api/history", "/api/export.csv"):
                    history = self.server.store.history(
                        *self.history_args(parse_qs(url.query))
                    )
                    if url.path.endswith(".csv"):
                        self.export_csv(history)
                    else:
                        self.json(200, history)
                else:
                    self.json(404, {"error": "接口不存在"})
            elif url.path == "/healthz":
                self.json(200, {"status": "ok"})
            elif url.path in ASSETS:
                filename, content_type = ASSETS[url.path]
                self.respond(200, (STATIC / filename).read_bytes(), content_type)
            else:
                self.json(404, {"error": "页面不存在"})
        except ValueError as exc:
            self.json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.exception("请求处理失败")
            self.json(500, {"error": "监控服务内部错误，请查看服务端日志"})

    def do_POST(self):
        try:
            if urlsplit(self.path).path != "/api/agent/report":
                self.json(405, {"error": "仅允许节点上报，不提供其他写入接口"})
                return
            if self.server.collector.demo:
                self.json(403, {"error": "演示模式不接受真实节点上报"})
                return
            if self.headers.get("Transfer-Encoding"):
                self.json(400, {"error": "不支持分块上报，请提供 Content-Length"})
                return
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or int(length) <= 0:
                self.json(411, {"error": "需要有效的 Content-Length"})
                return
            length = int(length)
            if length > MAX_REPORT_BYTES:
                self.json(413, {"error": "节点上报不能超过 1 MiB"})
                return
            if (
                self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                != "application/json"
                or self.headers.get("Content-Encoding", "identity") != "identity"
            ):
                self.json(415, {"error": "只支持未压缩的 application/json 上报"})
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("上报请求体不完整")
            payload = json.loads(body)
            name = payload.get("host") if isinstance(payload, dict) else None
            if not self.server.collector.authorized(
                name, self.headers.get("Authorization", "")
            ):
                self.json(401, {"error": "无效的节点上报身份"})
                return
            self.json(200, self.server.collector.accept_report(payload))
        except ValueError as exc:
            LOG.warning("拒绝无效节点上报：%s", exc)
            self.json(400, {"error": str(exc)})
        except TimeoutError:
            self.json(408, {"error": "读取上报超时"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.exception("节点上报处理失败")
            self.json(500, {"error": "保存节点上报失败，请稍后重新采样上报"})

    def export_csv(self, history):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(
            [
                "服务器",
                "开始时间(Unix秒)",
                "结束时间(Unix秒)",
                "样本数",
                "采集覆盖率(%)",
                "观测在线率(%)",
                "GPU平均使用率(%)",
                "GPU单卡峰值(%)",
                "显存平均使用率(%)",
                "CPU平均使用率(%)",
                "内存平均使用率(%)",
                "繁忙GPU卡时(>=10%)",
                "有效观测GPU卡时",
            ]
        )
        for row in history["stats"]:
            name = row["host"]
            # Avoid spreadsheet formula injection from a locally configured hostname.
            if name.startswith(("=", "+", "-", "@", "\t", "\r")):
                name = "'" + name
            values = [
                row.get(key)
                for key in (
                    "samples",
                    "coverage",
                    "availability",
                    "avg_gpu",
                    "peak_gpu",
                    "avg_gpu_memory",
                    "avg_cpu",
                    "avg_memory",
                    "busy_gpu_hours",
                    "observed_gpu_hours",
                )
            ]
            writer.writerow(
                [
                    name,
                    history["start"],
                    history["end"],
                    *[
                        round(value, 3) if isinstance(value, float) else value
                        for value in values
                    ],
                ]
            )
        self.respond(
            200,
            ("\ufeff" + stream.getvalue()).encode(),
            "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="hqlab-usage.csv"'},
        )
