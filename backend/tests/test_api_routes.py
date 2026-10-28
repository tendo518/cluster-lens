"""Explicit route/dependency architecture, plus raw-path compatibility over sockets."""

import asyncio
import inspect
import threading
import unittest
from unittest.mock import patch

from fastapi.routing import APIRoute
from monitor.api.app import RawPathRouting
from monitor.service import AgentAuthenticationError, IngestionDisabledError
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from test_api_contract import NOW, LoopbackCase, report


class RouteContractTests(LoopbackCase):
    def routes(self):
        return {
            (route.path, frozenset(route.methods)): route
            for route in self.server.app.routes
            if isinstance(route, APIRoute)
        }

    def test_known_resources_have_explicit_routes_not_a_dispatch_endpoint(self):
        routes = self.routes()
        reads = frozenset({"GET", "HEAD"})
        known_reads = (
            "/api/overview",
            "/api/history",
            "/api/export.csv",
            "/api/runtime",
            "/healthz",
            "/",
            "/index.html",
            "/favicon.svg",
            "/brand-icon.png",
            "/app.js",
            "/appearance.js",
            "/styles.css",
            "/palettes.css",
        )
        for path in known_reads:
            with self.subTest(path=path):
                route = routes[path, reads]
                self.assertFalse(inspect.iscoroutinefunction(route.endpoint))
        report_route = routes["/api/agent/report", frozenset({"POST"})]
        self.assertTrue(inspect.iscoroutinefunction(report_route.endpoint))
        # Each API has its own endpoint, not multiple registrations of a common
        # dispatcher. Only HTML aliases and static file routes share a handler.
        api_endpoints = {routes[path, reads].endpoint for path in known_reads[:4]}
        self.assertEqual(len(api_endpoints), 4)
        self.assertNotIn(report_route.endpoint, api_endpoints)
        fallbacks = {
            routes["/api/{path:path}", reads].endpoint,
            routes["/{path:path}", reads].endpoint,
            routes["/{path:path}", frozenset({"POST"})].endpoint,
        }
        self.assertEqual(len(fallbacks), 3)
        self.assertFalse(api_endpoints & fallbacks)
        self.assertNotIn(report_route.endpoint, fallbacks)
        self.assertNotIn(("/{path:path}", frozenset({"GET", "HEAD", "POST"})), routes)
        self.assertEqual(
            {path for path, methods in routes if ":path}" in path},
            {"/assets/{path:path}", "/api/{path:path}", "/{path:path}"},
        )

    def test_viewer_dependency_is_shared_and_runtime_requires_authentication(self):
        routes = self.routes()
        reads = frozenset({"GET", "HEAD"})
        dependency = routes["/api/overview", reads].dependant.dependencies[0].call
        for path in ("/api/history", "/api/export.csv", "/api/{path:path}"):
            self.assertIs(
                routes[path, reads].dependant.dependencies[0].call, dependency
            )
        required = routes["/api/runtime", reads].dependant.dependencies[0]
        self.assertIsNot(required.call, dependency)
        self.assertIs(required.dependencies[0].call, dependency)
        for path in ("/healthz", "/", "/favicon.svg", "/brand-icon.png", "/app.js"):
            self.assertEqual(routes[path, reads].dependant.dependencies, [])
        self.assertEqual(
            routes["/api/agent/report", frozenset({"POST"})].dependant.dependencies, []
        )
        self.assertEqual(
            routes["/{path:path}", frozenset({"POST"})].dependant.dependencies, []
        )

    def test_dependency_errors_use_central_handlers_without_running_use_cases(self):
        route = self.routes()["/api/overview", frozenset({"GET", "HEAD"})]
        dependency = route.dependant.dependencies[0].call

        async def reject():
            raise ValueError("dependency rejected before use case")

        self.server.app.dependency_overrides[dependency] = reject
        self.addCleanup(self.server.app.dependency_overrides.clear)
        with (
            patch.object(self.service, "overview") as overview,
            patch.object(self.service, "history") as history,
        ):
            for method in ("GET", "HEAD"):
                for path in (
                    "/api/overview",
                    "/api/history?start=bad",
                    "/api/export.csv",
                    "/api/runtime",
                    "/api/missing",
                ):
                    with self.subTest(method=method, path=path):
                        self.assert_error(
                            self.get(path, method=method),
                            400,
                            "dependency rejected before use case",
                            method,
                        )
            overview.assert_not_called()
            history.assert_not_called()
        self.assert_json(self.get("/healthz", token=None), 200, {"status": "ok"})
        # Agent ingestion must never depend on the viewer authentication chain.
        with patch("monitor.service.time.time", return_value=NOW):
            self.assert_json(
                self.post(report()),
                200,
                {"accepted": True, "duplicate": False, "server_time": NOW},
            )
        self.assertTrue(
            {
                HTTPException,
                AgentAuthenticationError,
                IngestionDisabledError,
                ValueError,
                ClientDisconnect,
                TimeoutError,
                Exception,
            }
            <= self.server.app.exception_handlers.keys()
        )

    def test_sync_read_routes_run_service_work_in_framework_workers(self):
        workers = []
        original_overview, original_history = (
            self.service.overview,
            self.service.history,
        )

        def observe(function, *args, **kwargs):
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            workers.append(threading.current_thread())
            return function(*args, **kwargs)

        with (
            patch.object(
                self.service, "overview", side_effect=lambda: observe(original_overview)
            ),
            patch.object(
                self.service,
                "history",
                side_effect=lambda **kwargs: observe(original_history, **kwargs),
            ),
        ):
            self.assert_response(self.get("/api/overview"), 200)
            self.assert_response(self.get("/api/history"), 200)
            self.assert_response(
                self.get("/api/export.csv"), 200, "text/csv; charset=utf-8"
            )
        self.assertEqual(len(workers), 3)
        self.assertTrue(all(worker is not self.server._thread for worker in workers))

    def test_encoded_paths_keep_raw_namespace_auth_and_never_alias_known_routes(self):
        for method in ("GET", "HEAD"):
            for path, token, status, message in (
                ("/api/%6fverview", None, 401, "请输入有效的监控访问令牌"),
                ("/api/%6fverview", self.viewer_token, 404, "接口不存在"),
                ("/api/overview%2f", self.viewer_token, 404, "接口不存在"),
                ("/api/%2e%2e/healthz", None, 401, "请输入有效的监控访问令牌"),
                ("/%61pi/overview", None, 404, "页面不存在"),
                ("/api%2foverview", None, 404, "页面不存在"),
                ("/health%7a", None, 404, "页面不存在"),
            ):
                with self.subTest(method=method, path=path, token=token):
                    self.assert_error(
                        self.get(path, token, method), status, message, method
                    )
        for path in (
            "/api/agent%2freport",
            "/api/%61gent/report",
            "/api/agent/report%2f",
        ):
            with self.subTest(path=path):
                self.assert_error(
                    self.request("POST", path),
                    405,
                    "仅允许节点上报，不提供其他写入接口",
                )
        self.assertEqual(self.store.latest(), {})


class RawPathRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_changes_only_routing_path_on_a_copy(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/overview",
            "raw_path": b"/api/%6fverview",
            "root_path": "",
            "query_string": b"host=&host=alpha&token=never-log",
            "headers": [(b"authorization", b"Bearer never-log")],
        }
        original = dict(scope)
        receive, send = object(), object()
        seen = []

        async def downstream(actual_scope, actual_receive, actual_send):
            seen.append((actual_scope, actual_receive, actual_send))

        await RawPathRouting(downstream)(scope, receive, send)
        routed, passed_receive, passed_send = seen[0]
        self.assertEqual(routed, {**original, "path": "/api/%6fverview"})
        self.assertIsNot(routed, scope)
        self.assertEqual(scope, original)
        self.assertIs(routed["headers"], scope["headers"])
        self.assertIs(routed["query_string"], scope["query_string"])
        self.assertIs(passed_receive, receive)
        self.assertIs(passed_send, send)

    async def test_lifespan_and_scopes_without_raw_path_pass_through_unchanged(self):
        seen = []

        async def downstream(scope, receive, send):
            seen.append(scope)

        for scope in (
            {"type": "lifespan", "state": {}},
            {"type": "websocket", "path": "/", "raw_path": b"/%2f"},
            {"type": "http", "path": "/healthz"},
        ):
            await RawPathRouting(downstream)(scope, None, None)
            self.assertIs(seen[-1], scope)


if __name__ == "__main__":
    unittest.main()
