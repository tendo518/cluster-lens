"""ASGI ownership, nonblocking IO, and real-socket graceful request draining."""

import asyncio
import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from monitor.api import create_app
from monitor.api.app import read_report
from monitor.lifecycle import MonitorRuntime
from monitor.models import Host
from monitor.server import MonitorServer
from monitor.service import MonitoringService
from monitor.store import Store
from starlette.requests import Request

TOKEN = "test-lifecycle-node-token"
VIEWER = "test-lifecycle-viewer-token"


def payload():
    return {
        "version": 1,
        "host": "alpha",
        "report_id": "00000000-0000-0000-0000-000000000001",
        "interval": 30,
        "collected_at": time.time(),
        "system": {"cpu_percent": 10},
        "gpus": [],
    }


def http(server, method="GET", path="/healthz", body=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        headers = {
            "Authorization": "Bearer " + (TOKEN if method == "POST" else VIEWER),
            "Content-Type": "application/json",
        }
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class ASGILifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / "history.sqlite3")
        self.service = MonitoringService(
            [Host("alpha", "192.0.2.1")], self.store, credentials={"alpha": TOKEN}
        )

    def serve(self, runtime=None):
        server = MonitorServer(("127.0.0.1", 0), self.service, VIEWER, runtime=runtime)
        failures = []

        def run():
            try:
                server.serve_forever()
            except BaseException as exc:  # noqa: BLE001 - hand server failures to the test thread
                failures.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        return server, failures

    def test_shutdown_drains_blocked_commit_before_runtime_teardown_and_last_ack(self):
        entered, release, stopped = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.addCleanup(release.set)
        runtime = MonitorRuntime(self.service)
        record, stop = self.store.record, runtime.stop
        teardown = []
        results = []
        request = payload()

        def blocked(snapshot, *, interval):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release persistence")
            record(snapshot, interval=interval)

        def stop_runtime():
            teardown.append(
                (
                    self.store.latest()["alpha"]["report_id"],
                    server._server.server_state.total_requests,
                )
            )
            stop()

        with (
            patch.object(runtime, "step"),
            patch.object(runtime, "stop", side_effect=stop_runtime),
            patch.object(self.store, "record", side_effect=blocked),
        ):
            server, failures = self.serve(runtime)
            client = threading.Thread(
                target=lambda: results.append(
                    http(server, "POST", "/api/agent/report", json.dumps(request))
                )
            )
            client.start()
            self.assertTrue(entered.wait(2))
            self.assertEqual(results, [], "no acknowledgement before SQLite commits")
            # Neither a blocked DB write nor its service lock may block the loop.
            before = time.monotonic()
            self.assertEqual(http(server), (200, b'{"status": "ok"}'))
            self.assertLess(time.monotonic() - before, 1)
            self.assertIsInstance(server.app, FastAPI)
            self.assertEqual(server.config.workers, 1)
            self.assertFalse(server.config.proxy_headers)
            self.assertFalse(server.config.access_log)
            self.assertFalse(server.app.router.redirect_slashes)

            def shutdown():
                server.shutdown()
                stopped.set()

            stopper = threading.Thread(target=shutdown)
            stopper.start()
            try:
                self.assertFalse(
                    stopped.wait(0.25), "shutdown must wait for the commit and response"
                )
                self.assertFalse(
                    runtime.stop_event.is_set(),
                    "lifespan teardown must follow request drain",
                )
                self.assertTrue(runtime.thread.is_alive())
                self.assertEqual(teardown, [])
            finally:
                release.set()
                client.join(5)
                stopper.join(5)
            self.assertTrue(stopped.is_set())
            self.assertFalse(runtime.thread.is_alive())
            self.assertFalse(failures)
            self.assertEqual(results[0][0], 200)
            self.assertTrue(json.loads(results[0][1])["accepted"])
            self.assertEqual(
                self.store.latest()["alpha"]["report_id"], request["report_id"]
            )
            self.assertEqual(teardown, [(request["report_id"], 2)])

    def test_history_work_is_off_event_loop_and_transport_does_not_tick(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.service.history

        def history(**kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release history")
            return original(**kwargs)

        with (
            patch.object(self.service, "history", side_effect=history),
            patch.object(self.service, "tick") as tick,
        ):
            server, failures = self.serve()
            results = []
            client = threading.Thread(
                target=lambda: results.append(http(server, path="/api/history"))
            )
            client.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(http(server)[0], 200)
                tick.assert_not_called()
            finally:
                release.set()
                client.join(5)
            self.assertEqual(results[0][0], 200)
            server.shutdown()
            self.assertFalse(failures)

    def test_storage_failure_has_no_ack_and_retries_after_worker_finishes(self):
        server, failures = self.serve()
        request = json.dumps(payload())
        with (
            patch.object(self.store, "record", side_effect=OSError("disk full")),
            self.assertLogs("monitor.api.app", level="ERROR"),
        ):
            status, body = http(server, "POST", "/api/agent/report", request)
        self.assertEqual(status, 500)
        self.assertEqual(
            json.loads(body), {"error": "保存节点上报失败，请稍后重新采样上报"}
        )
        self.assertEqual(self.store.latest(), {})
        self.assertIsNone(self.service.last_reports["alpha"])
        self.assertEqual(http(server, "POST", "/api/agent/report", request)[0], 200)
        server.shutdown()
        self.assertFalse(failures)

    def test_lifespan_start_failure_always_stops_partial_runtime(self):
        runtime = Mock()
        runtime.start.side_effect = RuntimeError("start failed")
        app = create_app(self.service, runtime=runtime)

        async def run():
            async with app.router.lifespan_context(app):
                self.fail("startup cannot succeed")

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            asyncio.run(run())
        runtime.start.assert_called_once_with()
        runtime.stop.assert_called_once_with()

    def test_real_server_cleans_runtime_on_listener_or_main_loop_failure(self):
        for failure in ("listener", "main_loop"):
            with self.subTest(failure=failure):
                runtime = MonitorRuntime(self.service)
                self.addCleanup(runtime.stop)
                server = MonitorServer(("127.0.0.1", 0), self.service, runtime=runtime)
                self.addCleanup(server.server_close)
                target = (
                    asyncio.BaseEventLoop if failure == "listener" else server._server
                )
                method = "create_server" if failure == "listener" else "main_loop"
                with (
                    patch.object(runtime, "step"),
                    patch.object(
                        target,
                        method,
                        new=AsyncMock(side_effect=RuntimeError("injected failure")),
                    ),
                    self.assertRaisesRegex(RuntimeError, "injected failure"),
                ):
                    server.serve_forever()
                self.assertTrue(runtime.stop_event.is_set())
                self.assertFalse(runtime.thread.is_alive())
                self.assertEqual(server.socket.fileno(), -1)
                self.assertEqual(server._server.server_state.tasks, set())

    def test_runtime_stop_failure_closes_listener_and_leaves_no_worker(self):
        runtime = MonitorRuntime(self.service)
        stop = runtime.stop

        def failed_stop():
            stop()
            raise RuntimeError("stop failed")

        with (
            patch.object(runtime, "step"),
            patch.object(runtime, "stop", side_effect=failed_stop),
        ):
            server, failures = self.serve(runtime)
            self.assertEqual(http(server)[0], 200)
            with self.assertLogs("uvicorn.error", level="ERROR"):
                server.shutdown()
                server._thread.join(5)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeError)
        self.assertFalse(runtime.thread.is_alive())
        self.assertEqual(server.socket.fileno(), -1)

    def test_body_timeout_is_json_408_without_auth_or_persistence(self):
        server, failures = self.serve()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            with patch("monitor.api.app.BODY_TIMEOUT", 0.05):
                connection.putrequest("POST", "/api/agent/report")
                connection.putheader("Content-Length", "2")
                connection.putheader("Content-Type", "application/json")
                connection.endheaders(b"{")
                response = connection.getresponse()
                self.assertEqual(response.status, 408)
                self.assertEqual(json.loads(response.read()), {"error": "读取上报超时"})
        finally:
            connection.close()
        self.assertEqual(self.store.latest(), {})
        server.shutdown()
        self.assertFalse(failures)


class ASGIStreamingTests(unittest.IsolatedAsyncioTestCase):
    def request(self, chunks, delay=0):
        chunks = iter(chunks)

        async def receive():
            if delay:
                await asyncio.sleep(delay)
            return next(chunks)

        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/agent/report",
                "headers": [],
            },
            receive,
        )

    async def test_stream_is_bounded_and_short_complete_body_is_rejected(self):
        for chunks, length in (
            ([{"type": "http.request", "body": b"{", "more_body": False}], 2),
            ([{"type": "http.request", "body": b"{}", "more_body": False}], 1),
            (
                [
                    {
                        "type": "http.request",
                        "body": b"x" * (1024 * 1024 + 1),
                        "more_body": False,
                    }
                ],
                1024 * 1024 + 1,
            ),
        ):
            with (
                self.subTest(length=length),
                self.assertRaisesRegex(ValueError, "上报请求体不完整"),
            ):
                await read_report(self.request(chunks), length)

    async def test_deadline_is_total_not_reset_by_each_chunk(self):
        request = self.request(
            [{"type": "http.request", "body": b"x", "more_body": True}] * 20, delay=0.01
        )
        with (
            patch("monitor.api.app.BODY_TIMEOUT", 0.05),
            self.assertRaises(TimeoutError),
        ):
            await read_report(request, 20)

    async def test_exact_limit_in_multiple_chunks_is_allowed(self):
        chunks = [
            {"type": "http.request", "body": b"x" * 65536, "more_body": True}
            for _ in range(16)
        ]
        chunks[-1]["more_body"] = False
        self.assertEqual(
            len(await read_report(self.request(chunks), 1024 * 1024)), 1024 * 1024
        )


if __name__ == "__main__":
    unittest.main()
