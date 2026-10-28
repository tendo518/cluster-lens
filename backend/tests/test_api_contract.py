"""Wire contracts for FastAPI/Uvicorn: real loopback sockets, temporary SQLite only.

Run with: .venv/bin/python -m unittest discover -s backend/tests -p test_api_contract.py -v
Unlike the service/store unit tests, these assert literal public payloads and
error precedence. No receiver thread, real reporter, deployment config, or SSH runs.
"""

import csv
import http.client
import io
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.models import Host
from monitor.server import MonitorServer
from monitor.service import MonitoringService
from monitor.store import Store

NOW = 4_000_000
VIEWER = "contract-viewer-token-not-a-real-secret"
ALPHA = "contract-alpha-token-not-a-real-secret"
BETA = "contract-beta-token-not-a-real-secret"
FIRST_ID = "00000000-0000-0000-0000-000000000001"
SECOND_ID = "00000000-0000-0000-0000-000000000002"
THIRD_ID = "00000000-0000-0000-0000-000000000003"
JSON_TYPE = "application/json; charset=utf-8"
CSV_TYPE = "text/csv; charset=utf-8"
VIEWER_ERROR = "请输入有效的监控访问令牌"
AGENT_ERROR = "无效的节点上报身份"
RANGE_ERROR = "请选择有效时间范围（不超过 31 天，结束时间不能晚于当前时间）"
SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "x-frame-options": "DENY",
}
CSV_COLUMNS = [
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


def gpu(index=0, utilization=50, used=100, total=1000):
    return {
        "index": index,
        "name": "Test GPU",
        "utilization": utilization,
        "memory_used_mb": used,
        "memory_total_mb": total,
        "processes": [],
    }


def report(host="alpha", ts=NOW, report_id=FIRST_ID, **changes):
    payload = {
        "version": 1,
        "host": host,
        "report_id": report_id,
        "collected_at": ts,
        "interval": 30,
        "collection_ms": 12.5,
        "error": None,
        "warnings": [],
        "system": {"cpu_percent": 25, "memory_percent": 40, "load": [1, 2, 3]},
        "gpus": [gpu()],
    }
    payload.update(changes)
    return payload


def missing_stat(name):
    # Missing hosts deliberately have fewer keys than hosts with observations.
    return {
        "host": name,
        "samples": 0,
        "coverage": 0,
        "availability": None,
        "busy_gpu_hours": 0,
        "observed_gpu_hours": 0,
    }


def empty_aggregate():
    return {
        "samples": 0,
        "online_samples": None,
        "observed_seconds": None,
        "online_seconds": None,
        "avg_cpu": None,
        "avg_memory": None,
        "avg_gpu": None,
        "peak_gpu": None,
        "avg_gpu_memory": None,
        "busy_gpu_hours": 0,
        "observed_gpu_hours": 0,
        "availability": None,
        "coverage": 0,
    }


def pending_host(name, address, groups):
    return {
        "name": name,
        "address": address,
        "groups": groups,
        "status": "pending",
        "system": {},
        "gpus": [],
        "checked_at": None,
        "warnings": [],
        "error": None,
        "last_seen": None,
        "received_at": None,
        "report_id": None,
        "collected_at": None,
        "known_gpu_count": 0,
    }


def pending_overview():
    return {
        "hosts": [
            pending_host("alpha", "192.0.2.1", ["gpu_servers"]),
            pending_host("beta", "192.0.2.2", []),
            pending_host("idle", "192.0.2.3", []),
        ],
        "demo": False,
        "mode": "push",
        "stale_after": 100,
        "interval": 30,
        "retention_days": 30,
        "collecting": False,
        "last_cycle": None,
        "server_time": NOW,
    }


class LoopbackCase(unittest.TestCase):
    hosts = (
        Host("alpha", "192.0.2.1", groups=("gpu_servers",)),
        Host("beta", "192.0.2.2"),
        Host("idle", "192.0.2.3"),
    )
    viewer_token = VIEWER
    demo = False

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / "contract.sqlite3")
        self.service = MonitoringService(
            list(self.hosts),
            self.store,
            credentials={"alpha": ALPHA, "beta": BETA},
            demo=self.demo,
        )
        self.server = MonitorServer(("127.0.0.1", 0), self.service, self.viewer_token)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None, incomplete=False):
        # Low-level calls intentionally avoid HTTPConnection.request's automatic
        # Content-Length, so missing/invalid framing is really sent on the wire.
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        try:
            connection.putrequest(method, path)
            connection.putheader("Connection", "close")
            for key, value in (headers or {}).items():
                connection.putheader(key, value)
            connection.endheaders(body)
            if incomplete:
                connection.sock.shutdown(socket.SHUT_WR)
            with connection.getresponse() as response:
                # HTTPResponse.read() suppresses HEAD bodies even if a buggy
                # server sends one. Read to EOF on the raw stream to detect it.
                received = response.fp.read() if method == "HEAD" else response.read()
                return (
                    response.status,
                    {key.lower(): value for key, value in response.getheaders()},
                    received,
                )
        finally:
            connection.close()

    def get(self, path, token=VIEWER, method="GET"):
        headers = {} if token is None else {"Authorization": "Bearer " + token}
        return self.request(method, path, headers=headers)

    def post(self, payload, token=ALPHA, path="/api/agent/report", headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        fields = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }
        if token is not None:
            fields["Authorization"] = "Bearer " + token
        fields.update(headers or {})
        return self.request("POST", path, body, fields)

    def assert_response(self, response, status, content_type=JSON_TYPE, method="GET"):
        actual_status, headers, body = response
        self.assertEqual(actual_status, status, body)
        self.assertEqual(headers.get("content-type"), content_type)
        for key, expected in SECURITY_HEADERS.items():
            self.assertEqual(headers.get(key), expected, key)
        self.assertIn("content-length", headers)
        self.assertTrue(headers["content-length"].isdigit())
        if method == "HEAD":
            self.assertEqual(body, b"")
            self.assertGreater(int(headers["content-length"]), 0)
        else:
            self.assertEqual(int(headers["content-length"]), len(body))
        return body

    def assert_json(self, response, status, expected, method="GET"):
        body = self.assert_response(response, status, method=method)
        if method == "HEAD":
            self.assertEqual(
                int(response[1]["content-length"]),
                len(json.dumps(expected, ensure_ascii=False).encode("utf-8")),
            )
        else:
            self.assertEqual(json.loads(body), expected)

    def assert_error(self, response, status, message, method="GET"):
        self.assert_json(response, status, {"error": message}, method)
        if method != "HEAD":
            self.assertEqual(
                response[2],
                json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"),
            )


class ViewerContractTests(LoopbackCase):
    def test_public_health_and_static_get_head_headers(self):
        for method in ("GET", "HEAD"):
            with self.subTest(method=method, path="/healthz"):
                self.assert_json(
                    self.get("/healthz?ignored=1", token="wrong", method=method),
                    200,
                    {"status": "ok"},
                    method,
                )
        for path, content_type in (
            ("/", "text/html; charset=utf-8"),
            ("/index.html", "text/html; charset=utf-8"),
            ("/app.js", "text/javascript; charset=utf-8"),
            ("/appearance.js", "text/javascript; charset=utf-8"),
            ("/styles.css", "text/css; charset=utf-8"),
            ("/palettes.css", "text/css; charset=utf-8"),
            ("/favicon.svg", "image/svg+xml"),
        ):
            with self.subTest(path=path):
                body = self.assert_response(
                    self.get(path + "?ignored=1", token=None), 200, content_type
                )
                self.assertGreater(len(body), 100)
                head = self.get(path, token="wrong", method="HEAD")
                self.assert_response(head, 200, content_type, "HEAD")
                self.assertEqual(int(head[1]["content-length"]), len(body))

    def test_route_and_auth_error_precedence_for_get_and_head(self):
        cases = [
            ("/api/missing", None, 401, VIEWER_ERROR),
            ("/api/agent/report", ALPHA, 401, VIEWER_ERROR),
            ("/api/history?start=bad&host=unknown", None, 401, VIEWER_ERROR),
            ("/api/export.csv?start=nan", ALPHA, 401, VIEWER_ERROR),
            ("/api/missing", VIEWER, 404, "接口不存在"),
            ("/api/agent/report", VIEWER, 404, "接口不存在"),
            ("/api/overview/", VIEWER, 404, "接口不存在"),
            ("/api", None, 404, "页面不存在"),
            ("/missing", None, 404, "页面不存在"),
            ("/brand-icon.png", None, 404, "页面不存在"),
            ("/healthz/", VIEWER, 404, "页面不存在"),
        ]
        for method in ("GET", "HEAD"):
            for path, token, status, message in cases:
                with self.subTest(method=method, path=path, token=token):
                    self.assert_error(
                        self.get(path, token, method), status, message, method
                    )

    def test_viewer_bearer_header_is_exact_and_not_a_query_parameter(self):
        for authorization in (
            "Bearer " + ALPHA,
            "Bearer " + BETA,
            "bearer " + VIEWER,
            "Bearer  " + VIEWER,
            "",
        ):
            with self.subTest(authorization=authorization):
                self.assert_error(
                    self.request(
                        "GET",
                        "/api/overview?token=" + VIEWER,
                        headers={"Authorization": authorization},
                    ),
                    401,
                    VIEWER_ERROR,
                )

    def test_http_optional_whitespace_is_normalized_by_h11(self):
        # Unlike email.message in the former stdlib handler, h11 strips field
        # value OWS (RFC 9110). This is not part of the bearer credential. Exact
        # scheme and internal spacing remain covered by the rejection test above.
        for suffix in (" ", "\t"):
            with patch("monitor.service.time.time", return_value=NOW):
                self.assert_json(
                    self.request(
                        "GET",
                        "/api/overview",
                        headers={
                            "Authorization": "Bearer " + VIEWER + suffix,
                        },
                    ),
                    200,
                    pending_overview(),
                )

    def test_pending_overview_shape(self):
        with patch("monitor.service.time.time", return_value=NOW):
            self.assert_json(self.get("/api/overview"), 200, pending_overview())
            self.assert_json(
                self.get("/api/overview", method="HEAD"),
                200,
                pending_overview(),
                "HEAD",
            )


class UnprotectedViewerContractTests(LoopbackCase):
    viewer_token = None

    def test_no_viewer_token_opens_reads_but_not_agent_ingestion(self):
        with patch("monitor.service.time.time", return_value=NOW):
            self.assert_json(
                self.get("/api/overview", token=None), 200, pending_overview()
            )
        self.assert_error(self.get("/api/missing", token=None), 404, "接口不存在")
        self.assert_error(self.post(report(), token=None), 401, AGENT_ERROR)


class HistoryContractTests(LoopbackCase):
    def test_default_blank_repeated_and_boundary_query_values(self):
        # parse_qs drops blank values; the first remaining value wins. A future
        # framework must not silently switch to last-value or reject duplicates.
        cases = [
            ("", NOW - 3600, NOW, ["alpha", "beta", "idle"], 30),
            ("start=&end=&host=", NOW - 3600, NOW, ["alpha", "beta", "idle"], 30),
            ("end=4000000", 3996400, 4000000, ["alpha", "beta", "idle"], 30),
            ("start=3999970", 3999970, 4000000, ["alpha", "beta", "idle"], 30),
            (
                "start=100&start=bad&end=130&end=nan&host=alpha&host=unknown",
                100,
                130,
                ["alpha"],
                30,
            ),
            (
                "start=&start=100&end=&end=130&host=&host=beta",
                100,
                130,
                ["beta"],
                30,
            ),
            (
                "start=100&end=130&host=&unused=1",
                100,
                130,
                ["alpha", "beta", "idle"],
                30,
            ),
            ("start=0&end=2678400", 0, 2678400, ["alpha", "beta", "idle"], 14880),
            (
                "start=4000000&end=4000060",
                4000000,
                4000060,
                ["alpha", "beta", "idle"],
                30,
            ),
            ("start=100.25&end=100.75", 100.25, 100.75, ["alpha", "beta", "idle"], 30),
        ]
        with patch("monitor.service.time.time", return_value=NOW):
            for query, start, end, names, bucket in cases:
                with self.subTest(query=query):
                    self.assert_json(
                        self.get("/api/history?" + query),
                        200,
                        {
                            "start": start,
                            "end": end,
                            "bucket_seconds": bucket,
                            "points": [],
                            "stats": [missing_stat(name) for name in names],
                            "aggregate": empty_aggregate(),
                            "busy_threshold": 10,
                            "sample_interval": 30,
                        },
                    )

    def test_history_and_csv_validation_errors_have_the_same_precedence(self):
        cases = [
            ("start=nope&end=nan&host=unknown", "start / end 必须是 Unix 秒时间戳"),
            ("end=nope", "start / end 必须是 Unix 秒时间戳"),
            ("start=bad&start=100&end=130", "start / end 必须是 Unix 秒时间戳"),
            ("start=nan&host=unknown", "时间必须为有限数值"),
            ("start=inf", "时间必须为有限数值"),
            ("start=-inf", "时间必须为有限数值"),
            ("end=nan", "时间必须为有限数值"),
            ("start=-1&end=1&host=unknown", RANGE_ERROR),
            ("start=100&end=100", RANGE_ERROR),
            ("start=200&end=100", RANGE_ERROR),
            ("start=0&end=2678400.01", RANGE_ERROR),
            ("start=4000000&end=4000060.01", RANGE_ERROR),
            ("host=unknown", "未知的服务器"),
            ("start=100&end=130&host=unknown&host=alpha", "未知的服务器"),
            ("host=+", "未知的服务器"),
        ]
        with patch("monitor.service.time.time", return_value=NOW):
            for endpoint in ("/api/history", "/api/export.csv"):
                for query, message in cases:
                    for method in ("GET", "HEAD"):
                        with self.subTest(
                            endpoint=endpoint, query=query, method=method
                        ):
                            self.assert_error(
                                self.get(endpoint + "?" + query, method=method),
                                400,
                                message,
                                method,
                            )

    def seed_history(self):
        fixtures = [
            report(
                "alpha",
                100,
                warnings=["test warning"],
                system={"cpu_percent": 20, "memory_percent": 40},
                gpus=[gpu(utilization=100, used=500)],
            ),
            report(
                "beta",
                100,
                system={"cpu_percent": 80, "memory_percent": 60},
                gpus=[gpu(0, 0), gpu(1, 0), gpu(2, None, 300, 3000)],
            ),
            report("alpha", 130, SECOND_ID, error="probe failed", system={}, gpus=[]),
            report(
                "beta",
                130,
                SECOND_ID,
                system={"cpu_percent": None, "memory_percent": 0},
                gpus=[],
            ),
            report(
                "alpha",
                190,
                THIRD_ID,
                system={"cpu_percent": 40, "memory_percent": 20},
                gpus=[gpu(utilization=0, used=0)],
            ),
        ]
        for payload in fixtures:
            self.service.accept_report(payload, now=payload["collected_at"])
        # Retained data from a removed inventory entry must not leak into totals.
        self.store.record(
            {
                "name": "removed",
                "checked_at": 100,
                "status": "online",
                "system": {"cpu_percent": 100, "memory_percent": 100},
                "gpus": [gpu(utilization=100)],
            },
            interval=30,
        )

    def test_history_shape_card_weighting_offline_nulls_and_empty_buckets(self):
        self.seed_history()
        self.assert_json(
            self.get("/api/history?start=100&end=220"),
            200,
            {
                "start": 100,
                "end": 220,
                "bucket_seconds": 30,
                "sample_interval": 30,
                "busy_threshold": 10,
                "points": [
                    {
                        "ts": 115,
                        "cpu": 50,
                        "memory": 50,
                        "gpu": 100 / 3,
                        "gpu_memory": 100 / 6,
                    },
                    {
                        "ts": 145,
                        "cpu": None,
                        "memory": 0,
                        "gpu": None,
                        "gpu_memory": None,
                    },
                    {
                        "ts": 175,
                        "cpu": None,
                        "memory": None,
                        "gpu": None,
                        "gpu_memory": None,
                    },
                    {"ts": 205, "cpu": 40, "memory": 20, "gpu": 0, "gpu_memory": 0},
                ],
                "stats": [
                    {
                        "host": "alpha",
                        "samples": 3,
                        "online_samples": 2,
                        "observed_seconds": 90,
                        "online_seconds": 60,
                        "avg_cpu": 30,
                        "avg_memory": 30,
                        "avg_gpu": 50,
                        "peak_gpu": 100,
                        "avg_gpu_memory": 25,
                        "busy_gpu_hours": 1 / 120,
                        "observed_gpu_hours": 1 / 60,
                        "availability": 200 / 3,
                        "coverage": 75,
                    },
                    {
                        "host": "beta",
                        "samples": 2,
                        "online_samples": 2,
                        "observed_seconds": 60,
                        "online_seconds": 60,
                        "avg_cpu": 80,
                        "avg_memory": 30,
                        "avg_gpu": 0,
                        "peak_gpu": 0,
                        "avg_gpu_memory": 10,
                        "busy_gpu_hours": 0,
                        "observed_gpu_hours": 1 / 60,
                        "availability": 100,
                        "coverage": 50,
                    },
                    missing_stat("idle"),
                ],
                "aggregate": {
                    "samples": 5,
                    "online_samples": 4,
                    "observed_seconds": 150,
                    "online_seconds": 120,
                    "avg_cpu": 140 / 3,
                    "avg_memory": 30,
                    "avg_gpu": 25,
                    "peak_gpu": 100,
                    "avg_gpu_memory": 100 / 7,
                    "busy_gpu_hours": 1 / 120,
                    "observed_gpu_hours": 1 / 30,
                    "availability": 80,
                    "coverage": 125 / 3,
                },
            },
        )

    def test_host_filter_half_open_range_and_partial_bucket(self):
        self.seed_history()
        expected = {
            "samples": 1,
            "online_samples": 0,
            "observed_seconds": 15,
            "online_seconds": 0,
            "avg_cpu": None,
            "avg_memory": None,
            "avg_gpu": None,
            "peak_gpu": None,
            "avg_gpu_memory": None,
            "busy_gpu_hours": 0,
            "observed_gpu_hours": 0,
            "availability": 0,
            "coverage": 100,
        }
        self.assert_json(
            self.get("/api/history?start=130&end=145&host=alpha"),
            200,
            {
                "start": 130,
                "end": 145,
                "bucket_seconds": 30,
                "sample_interval": 30,
                "busy_threshold": 10,
                "points": [
                    {
                        "ts": 137.5,
                        "cpu": None,
                        "memory": None,
                        "gpu": None,
                        "gpu_memory": None,
                    }
                ],
                "stats": [{"host": "alpha", **expected}],
                "aggregate": expected,
            },
        )
        # The 130 sample is included above but excluded when it is the end.
        response = self.get("/api/history?start=100&end=130&host=alpha")
        data = json.loads(self.assert_response(response, 200))
        self.assertEqual(data["aggregate"]["samples"], 1)
        self.assertEqual(data["aggregate"]["avg_gpu"], 100)
        self.assertEqual(data["aggregate"]["coverage"], 100)
        self.assertEqual([row["host"] for row in data["stats"]], ["alpha"])
        self.assert_error(
            self.get("/api/history?start=100&end=130&host=removed"),
            400,
            "未知的服务器",
        )


class ReportContractTests(LoopbackCase):
    def test_success_duplicate_and_out_of_order_wire_contract(self):
        payload = report(ts=NOW - 5)
        payload["gpus"][0]["processes"] = [{"pid": 123, "name": "python"}]
        with patch("monitor.service.time.time", return_value=NOW):
            self.assert_json(
                self.post(payload),
                200,
                {"accepted": True, "duplicate": False, "server_time": NOW},
            )
        with patch("monitor.service.time.time", return_value=NOW + 1):
            self.assert_json(
                self.post(payload),
                200,
                {"accepted": False, "duplicate": True, "server_time": NOW + 1},
            )
            for ts in (NOW - 6, NOW - 5):
                with self.subTest(collected_at=ts):
                    self.assert_error(
                        self.post(report(ts=ts, report_id=SECOND_ID)),
                        400,
                        "拒绝乱序上报，请重新采样并检查节点时钟",
                    )
        expected = pending_overview()
        expected["last_cycle"] = NOW
        expected["hosts"][0] = {
            "name": "alpha",
            "address": "192.0.2.1",
            "groups": ["gpu_servers"],
            "report_id": FIRST_ID,
            "collected_at": NOW - 5,
            "collection_ms": 12.5,
            "error": None,
            "warnings": [],
            "system": {
                "cpu_percent": 25,
                "memory_percent": 40,
                "swap_percent": None,
                "memory_total_bytes": None,
                "memory_used_bytes": None,
                "swap_total_bytes": None,
                "swap_used_bytes": None,
                "uptime_seconds": None,
                "cpu_count": None,
                "network_rx_bytes_s": None,
                "network_tx_bytes_s": None,
                "disk_read_bytes_s": None,
                "disk_write_bytes_s": None,
                "hostname": "—",
                "os": "—",
                "kernel": "—",
                "load": [1, 2, 3],
                "disks": [],
            },
            "gpus": [
                {
                    "index": 0,
                    "name": "Test GPU",
                    "uuid": None,
                    "driver": None,
                    "utilization": 50,
                    "memory_percent": None,
                    "fan_percent": None,
                    "memory_used_mb": 100,
                    "memory_total_mb": 1000,
                    "temperature": None,
                    "power_w": None,
                    "power_limit_w": None,
                    "processes": [
                        {
                            "pid": 123,
                            "name": "python",
                            "user": None,
                            "type": "—",
                            "memory_mb": None,
                        }
                    ],
                }
            ],
            "checked_at": NOW,
            "received_at": NOW,
            "status": "online",
            "last_seen": NOW,
            "known_gpu_count": 1,
        }
        with patch("monitor.service.time.time", return_value=NOW):
            self.assert_json(self.get("/api/overview"), 200, expected)
        history = json.loads(
            self.assert_response(
                self.get("/api/history?start=4000000&end=4000030&host=alpha"), 200
            )
        )
        self.assertEqual(history["aggregate"]["samples"], 1)
        self.assertEqual(history["aggregate"]["observed_seconds"], 30)
        self.assertEqual(history["aggregate"]["avg_gpu"], 50)

    def test_ingestion_rejection_matrix_and_precedence(self):
        # Each early rejection also has invalid later fields. No body is needed
        # for framing/content checks; the handler must not wait to authenticate.
        cases = [
            ("/api/overview", {}, b"", 405, "仅允许节点上报，不提供其他写入接口"),
            ("/api/agent/report/", {}, b"", 405, "仅允许节点上报，不提供其他写入接口"),
            (
                "/healthz",
                {"Transfer-Encoding": "chunked"},
                b"",
                405,
                "仅允许节点上报，不提供其他写入接口",
            ),
            (
                "/api/agent/report",
                {
                    "Transfer-Encoding": "chunked",
                    "Content-Length": "1048577",
                    "Content-Type": "text/plain",
                },
                b"",
                400,
                "不支持分块上报，请提供 Content-Length",
            ),
            ("/api/agent/report", {}, b"", 411, "需要有效的 Content-Length"),
            (
                "/api/agent/report",
                {"Content-Length": "1048577", "Content-Type": "text/plain"},
                b"",
                413,
                "节点上报不能超过 1 MiB",
            ),
            (
                "/api/agent/report",
                {"Content-Length": "1"},
                b"{",
                415,
                "只支持未压缩的 application/json 上报",
            ),
            (
                "/api/agent/report",
                {"Content-Length": "1", "Content-Type": "text/plain"},
                b"{",
                415,
                "只支持未压缩的 application/json 上报",
            ),
            (
                "/api/agent/report",
                {
                    "Content-Length": "1",
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                },
                b"{",
                415,
                "只支持未压缩的 application/json 上报",
            ),
            (
                "/api/agent/report",
                {
                    "Content-Length": "1",
                    "Content-Type": "application/json",
                    "Content-Encoding": "Identity",
                },
                b"{",
                415,
                "只支持未压缩的 application/json 上报",
            ),
            (
                "/api/agent/report",
                {"Content-Length": "1", "Content-Type": "application/json"},
                b"{",
                400,
                "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
            ),
        ]
        # Zero is valid HTTP framing, but invalid report framing (still 411).
        # Malformed numeric syntax is exercised separately at h11's boundary.
        for length in ("0",):
            cases.append(
                (
                    "/api/agent/report",
                    {"Content-Length": length, "Content-Type": "text/plain"},
                    b"",
                    411,
                    "需要有效的 Content-Length",
                )
            )
        for path, headers, body, status, message in cases:
            with self.subTest(path=path, headers=headers):
                self.assert_error(
                    self.request("POST", path, body, headers), status, message
                )
        self.assertEqual(self.store.latest(), {})

    def test_malformed_wire_headers_are_rejected_by_h11_before_application(self):
        # h11, not application code, owns malformed HTTP. Empty/signed/fractional
        # Content-Length formerly reached a JSON 411; unsupported TE formerly
        # reached a JSON 400. They now all get Uvicorn's plain-text 400 + close,
        # without application security headers. No bespoke parser recreates this.
        fields = [
            {"Content-Length": value} for value in ("", "-1", "+1", "1.5", "invalid")
        ]
        fields.append({"Transfer-Encoding": "identity"})
        for headers in fields:
            with self.subTest(headers=headers):
                status, received_headers, body = self.request(
                    "POST", "/api/agent/report", headers=headers
                )
                self.assertEqual(status, 400)
                self.assertEqual(
                    received_headers["content-type"], "text/plain; charset=utf-8"
                )
                self.assertEqual(received_headers["connection"], "close")
                self.assertEqual(body, b"Invalid HTTP request received.")
        self.assertEqual(self.store.latest(), {})

    def test_incomplete_body_disconnect_never_ingests(self):
        # Uvicorn treats a TCP half-close with a short body as a disconnect and
        # cannot send the old stdlib JSON 400. The ASGI short-body check itself is
        # covered separately; valid requests keep their original error bytes.
        with self.assertRaises(http.client.RemoteDisconnected):
            self.request(
                "POST",
                "/api/agent/report",
                b"{",
                {"Content-Length": "2", "Content-Type": "application/json"},
                incomplete=True,
            )
        self.assertEqual(self.store.latest(), {})

    def test_agent_auth_precedes_schema_validation_but_follows_json_parsing(self):
        for payload, token in (
            (report(version=2), VIEWER),
            (report(version=2), BETA),
            (report(version=2), None),
            (report("unknown", version=2), ALPHA),
            (report("beta", version=2), ALPHA),
            ({"version": 2}, ALPHA),
            ({"host": []}, ALPHA),
            ([], ALPHA),
            (None, ALPHA),
        ):
            with self.subTest(payload=payload, token=token):
                self.assert_error(self.post(payload, token), 401, AGENT_ERROR)
        self.assertEqual(self.store.latest(), {})

    def test_authenticated_protocol_errors_are_400_json(self):
        cases = [
            (report(version=2), "上报必须是 version=1 的 JSON 对象"),
            (report(version=True), "上报必须是 version=1 的 JSON 对象"),
            (report(version=1.0), "上报必须是 version=1 的 JSON 对象"),
            (report(version="1"), "上报必须是 version=1 的 JSON 对象"),
            (report(report_id="invalid"), "report_id 必须是 UUID"),
            (
                report(interval=60),
                "节点 interval 与中心配置不一致，请重新生成配置并部署",
            ),
            (report(system={}), "成功采集的上报必须包含 system"),
            (report(gpus=[gpu(utilization=True)]), "无效的数值字段：utilization"),
            (
                report(gpus=[gpu(utilization=float("nan"))]),
                "无效的数值字段：utilization",
            ),
            (report(gpus=[gpu(), gpu()]), "GPU index 无效或重复"),
            (
                report(ts=NOW - 61),
                "采样时间过旧或位于未来，请同步节点和中心服务器时钟后重新采样",
            ),
            (
                report(ts=NOW + 61),
                "采样时间过旧或位于未来，请同步节点和中心服务器时钟后重新采样",
            ),
        ]
        with patch("monitor.service.time.time", return_value=NOW):
            for payload, message in cases:
                with self.subTest(
                    message=message, collected_at=payload["collected_at"]
                ):
                    self.assert_error(self.post(payload), 400, message)
        self.assertEqual(self.store.latest(), {})

    def test_exact_one_mib_and_case_insensitive_json_media_type_are_accepted(self):
        body = json.dumps(report()).encode("utf-8")
        body += b" " * (1_048_576 - len(body))
        with patch("monitor.service.time.time", return_value=NOW):
            self.assert_json(
                self.request(
                    "POST",
                    "/api/agent/report?ignored=1",
                    body,
                    {
                        "Authorization": "Bearer " + ALPHA,
                        "Content-Length": "1048576",
                        "Content-Type": "Application/JSON ; charset=utf-8",
                        "Content-Encoding": "identity",
                    },
                ),
                200,
                {"accepted": True, "duplicate": False, "server_time": NOW},
            )


class DemoReportContractTests(LoopbackCase):
    demo = True

    def test_demo_rejection_precedes_framing_and_auth_but_not_routing(self):
        headers = {"Transfer-Encoding": "chunked", "Content-Length": "1048577"}
        self.assert_error(
            self.request("POST", "/api/agent/report", headers=headers),
            403,
            "演示模式不接受真实节点上报",
        )
        self.assert_error(
            self.request("POST", "/api/missing", headers=headers),
            405,
            "仅允许节点上报，不提供其他写入接口",
        )
        self.assertEqual(self.store.latest(), {})


class CSVContractTests(LoopbackCase):
    hosts = tuple(
        Host(name, "192.0.2.10")
        for name in (
            "=SUM(1,1)",
            "+cmd",
            "-2+3",
            "@SUM(A1)",
            "\tformula",
            "\rformula",
            'gpu, "quoted"\nnode',
            "plain",
        )
    )

    def test_csv_bom_filename_columns_rounding_nulls_and_formula_escaping(self):
        self.service.accept_report(
            report(
                "=SUM(1,1)", 100, system={"cpu_percent": 25.1236, "memory_percent": 40}
            ),
            now=100,
        )
        response = self.get("/api/export.csv?start=100&end=130")
        body = self.assert_response(response, 200, CSV_TYPE)
        self.assertEqual(
            response[1].get("content-disposition"),
            'attachment; filename="clusterlens-usage.csv"',
        )
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(body.endswith(b"\r\n"))
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"), newline="")))
        self.assertEqual(rows[0], CSV_COLUMNS)
        self.assertEqual(
            rows[1],
            [
                "'=SUM(1,1)",
                "100.0",
                "130.0",
                "1",
                "100",
                "100.0",
                "50.0",
                "50.0",
                "10.0",
                "25.124",
                "40.0",
                "0.008",
                "0.008",
            ],
        )
        self.assertEqual(
            rows[2:],
            [
                [name, "100.0", "130.0", "0", "0", "", "", "", "", "", "", "0", "0"]
                for name in (
                    "'+cmd",
                    "'-2+3",
                    "'@SUM(A1)",
                    "'\tformula",
                    "'\rformula",
                    'gpu, "quoted"\nnode',
                    "plain",
                )
            ],
        )
        head = self.get("/api/export.csv?start=100&end=130", method="HEAD")
        self.assert_response(head, 200, CSV_TYPE, "HEAD")
        self.assertEqual(int(head[1]["content-length"]), len(body))
        self.assertEqual(
            head[1].get("content-disposition"),
            'attachment; filename="clusterlens-usage.csv"',
        )

    def test_csv_defaults_and_first_nonblank_query_values(self):
        cases = (
            ("start=&end=&host=plain", "3996400.0", "4000000.0"),
            (
                "start=&start=100&start=bad&end=130&end=bad&host=&host=plain&host=unknown",
                "100.0",
                "130.0",
            ),
        )
        with patch("monitor.service.time.time", return_value=NOW):
            for query, start, end in cases:
                with self.subTest(query=query):
                    body = self.assert_response(
                        self.get("/api/export.csv?" + query), 200, CSV_TYPE
                    )
                    self.assertEqual(
                        list(csv.reader(io.StringIO(body.decode("utf-8-sig")))),
                        [
                            CSV_COLUMNS,
                            [
                                "plain",
                                start,
                                end,
                                "0",
                                "0",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "0",
                                "0",
                            ],
                        ],
                    )


if __name__ == "__main__":
    unittest.main()
