"""Explicit FastAPI routes for the version 1 HTTP contract.

Report bodies are intentionally not FastAPI model parameters: framing and JSON
errors precede authentication, which precedes the shared Pydantic report validator.
Viewer authentication is a dependency, and exception handlers own error envelopes.
"""

import asyncio
import hmac
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import APPLICATION, __version__
from ..assets import PUBLIC_ASSETS, Assets
from ..exports import history_csv
from ..protocol import MAX_REPORT_BYTES
from ..service import AgentAuthenticationError, IngestionDisabledError
from ..ui import favicon, icon_bytes, parse_ui, render_page

LOG = logging.getLogger(__name__)
BODY_TIMEOUT = 15.0
READ_METHODS = ["GET", "HEAD"]
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
}


class RawPathRouting:
    """Let the framework match the original path, not percent-decoded aliases.

    h11/Uvicorn still owns all HTTP parsing. This adapter changes only the ASGI
    routing path on a copied scope; headers, query bytes, body and lifespan pass
    through untouched. There is no second route table or manual dispatch here.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and "raw_path" in scope:
            scope = {**scope, "path": scope["raw_path"].decode("ascii")}
        await self.app(scope, receive, send)


def runtime_identity():
    release_id = os.environ.get("CLUSTERLENS_RELEASE_ID")
    if release_id is not None and not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise ValueError("CLUSTERLENS_RELEASE_ID 必须是小写 SHA256 摘要")
    return {
        "application": APPLICATION,
        "version": __version__,
        "pid": os.getpid(),
        "release_id": release_id,
    }


def response(
    request, status, body, content_type="application/json; charset=utf-8", extra=None
):
    headers = {
        **SECURITY_HEADERS,
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        **(extra or {}),
    }
    return Response(
        b"" if request.method == "HEAD" else body, status_code=status, headers=headers
    )


def json_response(request, status, data):
    return response(
        request,
        status,
        json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
    )


def error(request, status, message):
    return json_response(request, status, {"error": message})


def install_exception_handlers(app):
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error(request, exc.status_code, exc.detail)

    @app.exception_handler(IngestionDisabledError)
    async def ingestion_disabled(request, exc):
        return error(request, 403, str(exc))

    @app.exception_handler(AgentAuthenticationError)
    async def agent_authentication_failed(request, exc):
        return error(request, 401, str(exc))

    @app.exception_handler(ValueError)
    @app.exception_handler(ClientDisconnect)
    async def invalid_request(request, exc):
        return error(
            request,
            400,
            "上报请求体不完整" if isinstance(exc, ClientDisconnect) else str(exc),
        )

    @app.exception_handler(TimeoutError)
    async def body_timeout(request, exc):
        return error(request, 408, "读取上报超时")

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        # Never log request objects, URLs, authorization or payload contents.
        LOG.error(
            "节点上报处理失败" if request.method == "POST" else "请求处理失败",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return error(
            request,
            500,
            "保存节点上报失败，请稍后重新采样上报"
            if request.method == "POST"
            else "监控服务内部错误，请查看服务端日志",
        )


async def read_report(request, length):
    body = bytearray()
    # A total deadline, not a resettable per-chunk timeout (slowloris protection).
    async with asyncio.timeout(BODY_TIMEOUT):
        async for chunk in request.stream():
            if len(body) + len(chunk) > min(length, MAX_REPORT_BYTES):
                raise ValueError("上报请求体不完整")
            body.extend(chunk)
    if len(body) != length:
        raise ValueError("上报请求体不完整")
    return body


def create_app(service, token=None, ui=None, *, runtime=None, assets=None):
    resources = Assets(assets)
    resources.validate()
    public_ui = parse_ui({} if ui is None else ui)
    page = render_page(resources.read("index.html").decode("utf-8"), public_ui)
    icon = icon_bytes(public_ui["icon"]) if public_ui["icon"] is not None else None
    identity = runtime_identity()

    @asynccontextmanager
    async def lifespan(app):
        # Uvicorn drains request tasks (including worker-thread commits) BEFORE
        # sending lifespan.shutdown. Transport-only tests supply no runtime.
        try:
            if runtime is not None:
                await run_in_threadpool(runtime.start)
            yield
        finally:
            if runtime is not None:
                await run_in_threadpool(runtime.stop)

    app = FastAPI(
        title="ClusterLens",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.add_middleware(RawPathRouting)
    install_exception_handlers(app)

    async def require_viewer(request: Request):
        if token and not hmac.compare_digest(
            ("Bearer " + token).encode(),
            request.headers.get("authorization", "").encode(),
        ):
            raise HTTPException(401, "请输入有效的监控访问令牌")
        # Legacy local/demo reads may intentionally be unprotected. Readiness is
        # stricter and requires an authenticated viewer even in that mode.
        return bool(token)

    async def require_authenticated_viewer(
        authenticated: Annotated[bool, Depends(require_viewer)],
    ):
        if not authenticated:
            raise HTTPException(401, "请输入有效的监控访问令牌")

    viewer = [Depends(require_viewer)]

    @app.api_route("/api/overview", methods=READ_METHODS, dependencies=viewer)
    def overview(request: Request):
        return json_response(request, 200, service.overview())

    def history_data(request):
        # parse_qs drops blank values; preserve first-nonblank rather than the
        # framework's usual last-value query parameter semantics.
        query = parse_qs(request.scope["query_string"].decode("ascii"))
        return service.history(
            **{key: query.get(key, [None])[0] for key in ("start", "end", "host")}
        )

    @app.api_route("/api/history", methods=READ_METHODS, dependencies=viewer)
    def history(request: Request):
        return json_response(request, 200, history_data(request))

    @app.api_route("/api/export.csv", methods=READ_METHODS, dependencies=viewer)
    def export_csv(request: Request):
        return response(
            request,
            200,
            history_csv(history_data(request)),
            "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="clusterlens-usage.csv"'},
        )

    @app.api_route(
        "/api/runtime",
        methods=READ_METHODS,
        dependencies=[Depends(require_authenticated_viewer)],
    )
    def runtime_status(request: Request):
        return json_response(request, 200, identity)

    @app.post("/api/agent/report")
    async def report(request: Request):
        await run_in_threadpool(service.ensure_ingestion_enabled)
        if request.headers.get("transfer-encoding"):
            raise HTTPException(400, "不支持分块上报，请提供 Content-Length")
        length = request.headers.get("content-length", "")
        if not length.isascii() or not length.isdigit() or int(length) <= 0:
            raise HTTPException(411, "需要有效的 Content-Length")
        length = int(length)
        if length > MAX_REPORT_BYTES:
            raise HTTPException(413, "节点上报不能超过 1 MiB")
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
            or request.headers.get("content-encoding", "identity") != "identity"
        ):
            raise HTTPException(415, "只支持未压缩的 application/json 上报")
        body = await read_report(request, length)

        # JSON parsing, validation, locks, and SQLite all run outside the event loop.
        def ingest():
            payload = json.loads(body)
            result = service.ingest(payload, request.headers.get("authorization", ""))
            return json_response(request, 200, result)

        return await run_in_threadpool(ingest)

    @app.api_route("/healthz", methods=READ_METHODS)
    def health(request: Request):
        return json_response(request, 200, {"status": "ok"})

    @app.api_route("/", methods=READ_METHODS)
    @app.api_route("/index.html", methods=READ_METHODS)
    def index(request: Request):
        return response(request, 200, page, "text/html; charset=utf-8")

    @app.api_route("/favicon.svg", methods=READ_METHODS)
    def favicon_image(request: Request):
        return response(request, 200, favicon(public_ui), "image/svg+xml")

    @app.api_route("/brand-icon.png", methods=READ_METHODS)
    def brand_icon(request: Request):
        if icon is None:
            raise HTTPException(404, "页面不存在")
        return response(request, 200, icon, "image/png")

    @app.api_route("/assets/{path:path}", methods=READ_METHODS)
    def static_asset(request: Request):
        asset = resources.response(request.scope["path"])
        if asset is None:
            raise HTTPException(404, "页面不存在")
        return response(request, 200, *asset)

    for path in PUBLIC_ASSETS:
        app.add_api_route(path, static_asset, methods=READ_METHODS)

    # Fallbacks are deliberately last. They encode only namespace/method policy,
    # not routing of known resources. API reads authenticate before returning 404;
    # unknown POSTs return 405 without inspecting framing, auth, or demo policy.
    @app.api_route(
        "/api/{path:path}",
        methods=READ_METHODS,
        dependencies=viewer,
        include_in_schema=False,
    )
    def unknown_api():
        raise HTTPException(404, "接口不存在")

    @app.post("/{path:path}", include_in_schema=False)
    def unsupported_post():
        raise HTTPException(405, "仅允许节点上报，不提供其他写入接口")

    @app.api_route("/{path:path}", methods=READ_METHODS, include_in_schema=False)
    def unknown_page():
        raise HTTPException(404, "页面不存在")

    return app
