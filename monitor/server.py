"""Read-only viewer API, authenticated node ingestion, and static dashboard."""

import hmac
import json
import logging
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .exports import history_csv
from .protocol import MAX_REPORT_BYTES
from .service import AgentAuthenticationError, IngestionDisabledError
from .ui import favicon, icon_bytes, parse_ui, render_page

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/appearance.js": ("appearance.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/palettes.css": ("palettes.css", "text/css; charset=utf-8"),
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

    def __init__(self, address, service, token=None, ui=None):
        self.ui = parse_ui({} if ui is None else ui)
        self.page = render_page(
            (STATIC / "index.html").read_text(encoding="utf-8"), self.ui
        )
        self.icon = icon_bytes(self.ui["icon"]) if self.ui["icon"] is not None else None
        self.service, self.token = service, token
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "ClusterLens/1.0"
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
                    self.json(200, self.server.service.overview())
                elif url.path in ("/api/history", "/api/export.csv"):
                    query = parse_qs(url.query)
                    history = self.server.service.history(
                        **{
                            key: query.get(key, [None])[0]
                            for key in ("start", "end", "host")
                        }
                    )
                    if url.path.endswith(".csv"):
                        self.respond(
                            200,
                            history_csv(history),
                            "text/csv; charset=utf-8",
                            {
                                "Content-Disposition": 'attachment; filename="clusterlens-usage.csv"'
                            },
                        )
                    else:
                        self.json(200, history)
                else:
                    self.json(404, {"error": "接口不存在"})
            elif url.path == "/healthz":
                self.json(200, {"status": "ok"})
            elif url.path in ("/", "/index.html"):
                self.respond(200, self.server.page, "text/html; charset=utf-8")
            elif url.path == "/favicon.svg":
                self.respond(200, favicon(self.server.ui), "image/svg+xml")
            elif url.path == "/brand-icon.png" and self.server.icon is not None:
                self.respond(200, self.server.icon, "image/png")
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
            self.server.service.ensure_ingestion_enabled()
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
            self.json(
                200,
                self.server.service.ingest(
                    payload, self.headers.get("Authorization", "")
                ),
            )
        except IngestionDisabledError as exc:
            self.json(403, {"error": str(exc)})
        except AgentAuthenticationError as exc:
            self.json(401, {"error": str(exc)})
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
