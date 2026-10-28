"""Push protocol tests. Only temporary files and loopback HTTP; no Ansible."""

import copy
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from _reporter import remote_probe
from monitor.models import Host
from monitor.protocol import MAX_REPORT_BYTES
from monitor.server import MonitorServer
from monitor.service import MonitoringService
from monitor.store import Store

VIEWER = "viewer-token-for-test-only-12345678"
ALPHA = "alpha-agent-token-for-test-12345678"
BETA = "beta-agent-token-for-test-123456789"


def report(host="alpha", ts=None):
    return {
        "version": 1,
        "host": host,
        "report_id": str(uuid.uuid4()),
        "collected_at": time.time() if ts is None else ts,
        "interval": 30,
        "collection_ms": 300,
        "error": None,
        "warnings": [],
        "system": {
            "cpu_percent": 25,
            "memory_percent": 40,
            "disks": [],
            "load": [1, 2, 3],
        },
        "gpus": [
            {
                "index": 0,
                "name": "NVIDIA test GPU",
                "uuid": "GPU-alpha",
                "driver": "535",
                "utilization": 50,
                "memory_used_mb": 100,
                "memory_total_mb": 1000,
                "memory_percent": 10,
                "temperature": 50,
                "processes": [
                    {
                        "pid": 1234,
                        "name": "/bin/python",
                        "user": "labuser",
                        "type": "C",
                        "memory_mb": 80,
                    }
                ],
            }
        ],
    }


class PushServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "push.sqlite3")
        self.hosts = [
            Host("alpha", "10.1.2.3", groups=("gpu_servers",)),
            Host("beta", "10.1.2.4"),
        ]
        self.collector = MonitoringService(
            self.hosts, self.store, credentials={"alpha": ALPHA, "beta": BETA}
        )
        self.collector.started_at = 100

    def tearDown(self):
        self.temp.cleanup()

    def test_waiting_nodes_become_offline_without_ever_reporting(self):
        self.collector.tick(199)
        self.assertEqual(self.collector.overview()["hosts"][0]["status"], "pending")
        self.collector.tick(201)
        self.assertTrue(
            all(h["status"] == "offline" for h in self.collector.overview()["hosts"])
        )
        with self.store.connection() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 2
            )
        self.collector.tick(202)
        with self.store.connection() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 2
            )

    def test_offline_clears_metrics_and_recovers_on_a_new_report(self):
        self.collector.accept_report(report(ts=100), now=100)
        self.collector.tick(201)
        node = self.collector.overview()["hosts"][0]
        self.assertEqual(node["status"], "offline")
        self.assertEqual(node["last_seen"], 100)
        self.assertEqual(node["received_at"], 100)
        self.assertEqual(node["gpus"], [])
        self.assertEqual(node["system"], {})
        self.collector.accept_report(report(ts=202), now=202)
        self.assertEqual(self.collector.overview()["hosts"][0]["status"], "online")

    def test_duplicate_and_out_of_order_reports_do_not_inflate_history(self):
        payload = report(ts=100)
        self.collector.accept_report(payload, now=100)
        duplicate = self.collector.accept_report(payload, now=101)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.collector.last_reports["alpha"], 100)
        with self.assertRaisesRegex(ValueError, "乱序"):
            self.collector.accept_report(report(ts=99), now=101)
        with self.store.connection() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1
            )

    def test_restart_dedup_and_no_cached_online_claim(self):
        payload = report(ts=100)
        self.collector.accept_report(payload, now=100)
        restarted = MonitoringService(
            self.hosts, self.store, credentials={"alpha": ALPHA}
        )
        self.assertEqual(restarted.overview()["hosts"][0]["status"], "pending")
        self.assertTrue(restarted.accept_report(payload, now=101)["duplicate"])
        self.assertIsNone(restarted.last_reports["alpha"])

    def test_center_owns_identity_and_sample_timestamp(self):
        payload = dict(
            report(ts=100),
            address="spoofed",
            name="beta",
            groups=["admin"],
            checked_at=1,
            status="offline",
        )
        self.collector.accept_report(payload, now=102)
        node = self.collector.overview()["hosts"][0]
        self.assertEqual(
            (node["name"], node["address"], node["groups"]),
            ("alpha", "10.1.2.3", ["gpu_servers"]),
        )
        self.assertEqual(node["checked_at"], 102)
        self.assertEqual(node["status"], "online")

    def test_agent_probe_failure_is_reported_and_last_success_is_preserved(self):
        self.collector.accept_report(report(ts=100), now=100)
        failed = dict(report(ts=131), error="cannot read /proc", system={}, gpus=[])
        self.collector.accept_report(failed, now=131)
        node = self.collector.overview()["hosts"][0]
        self.assertEqual(node["status"], "offline")
        self.assertEqual(node["received_at"], 131)
        self.assertEqual(node["last_seen"], 100)
        self.assertIn("/proc", node["error"])

    def test_validation_rejects_bad_nested_data_and_clock_skew(self):
        bad = []
        for key, value in (
            ("version", 2),
            ("version", True),
            ("version", False),
            ("version", 1.0),
            ("version", "1"),
            ("version", None),
            ("interval", 60),
            ("report_id", "not-a-uuid"),
            ("collected_at", -100),
            ("gpus", "bad"),
        ):
            payload = report(ts=100)
            payload[key] = value
            bad.append(payload)
        payload = report(ts=100)
        payload["gpus"][0]["utilization"] = float("nan")
        bad.append(payload)
        payload = report(ts=100)
        payload["gpus"][0]["processes"][0]["pid"] = "<script>bad</script>"
        bad.append(payload)
        payload = report(ts=100)
        payload["gpus"].append(copy.deepcopy(payload["gpus"][0]))
        bad.append(payload)
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.collector.accept_report(payload, now=100)
        self.assertEqual(self.store.latest(), {})

    def test_runtime_never_executes_ssh_or_other_remote_commands(self):
        with patch(
            "subprocess.run", side_effect=AssertionError("No runtime subprocesses")
        ):
            self.collector.accept_report(report(ts=100), now=100)
            self.collector.tick(201)
            self.collector.overview()


class IngestionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "http.sqlite3")
        self.collector = MonitoringService(
            [Host("alpha", "10.0.0.1"), Host("beta", "10.0.0.2")],
            self.store,
            credentials={"alpha": ALPHA, "beta": BETA},
        )
        self.server = MonitorServer(("127.0.0.1", 0), self.collector, VIEWER)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, payload, token=ALPHA):
        return urlopen(
            Request(
                self.url + "/api/agent/report",
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                },
            ),
            timeout=5,
        )

    def test_report_is_visible_in_dashboard_and_history(self):
        with self.post(report()) as response:
            self.assertTrue(json.load(response)["accepted"])
        with urlopen(
            Request(
                self.url + "/api/overview",
                headers={"Authorization": "Bearer " + VIEWER},
            ),
            timeout=5,
        ) as response:
            data = json.load(response)
        self.assertEqual(data["mode"], "push")
        self.assertEqual(data["hosts"][0]["gpus"][0]["processes"][0]["user"], "labuser")
        self.assertNotIn(ALPHA, json.dumps(data))
        self.assertEqual(
            self.collector.history(time.time() - 60, time.time() + 1, host="alpha")[
                "aggregate"
            ]["avg_gpu"],
            50,
        )

    def test_credentials_are_separate_for_viewers_and_each_node(self):
        for payload, token in (
            (report(), VIEWER),
            (report("beta"), ALPHA),
            (report("unknown"), ALPHA),
            (report(), "wrong"),
        ):
            with self.subTest(token=token), self.assertRaises(HTTPError) as error:
                self.post(payload, token)
            self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            urlopen(
                Request(
                    self.url + "/api/overview",
                    headers={"Authorization": "Bearer " + ALPHA},
                ),
                timeout=5,
            )
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(self.store.latest(), {})

    def test_body_limits_content_type_and_malformed_json(self):
        for length, content_type, expected in (
            (None, "application/json", 411),
            (MAX_REPORT_BYTES + 1, "application/json", 413),
            (2, "text/plain", 415),
        ):
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_port, timeout=5
            )
            connection.putrequest("POST", "/api/agent/report")
            if length is not None:
                connection.putheader("Content-Length", str(length))
            connection.putheader("Content-Type", content_type)
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, expected)
            connection.close()
        with self.assertRaises(HTTPError) as error:
            urlopen(
                Request(
                    self.url + "/api/agent/report",
                    data=b"{",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=5,
            )
        self.assertEqual(error.exception.code, 400)

    def test_healthz_has_no_credentials_or_host_metadata(self):
        with urlopen(self.url + "/healthz", timeout=5) as response:
            self.assertEqual(json.load(response), {"status": "ok"})

    def test_agent_posts_a_real_http_request_without_proxy_or_third_party_dependencies(
        self,
    ):
        config = {
            "version": 1,
            "host": "alpha",
            "url": self.url + "/api/agent/report",
            "token": ALPHA,
            "interval": 30,
            "timeout": 5,
        }
        payload = report()
        with (
            patch.object(
                remote_probe,
                "collect",
                return_value={
                    key: payload[key] for key in ("system", "gpus", "warnings")
                },
            ),
            patch.dict(
                os.environ,
                {
                    "http_proxy": "http://127.0.0.1:1",
                    "HTTP_PROXY": "http://127.0.0.1:1",
                },
            ),
        ):
            result = remote_probe.send_report(config, remote_probe.make_report(config))
        self.assertTrue(result["accepted"])
        self.assertEqual(self.store.latest()["alpha"]["gpus"][0]["utilization"], 50)

    def test_probe_exception_can_still_be_reported(self):
        config = {
            "host": "alpha",
            "url": self.url + "/api/agent/report",
            "token": ALPHA,
            "interval": 30,
            "timeout": 5,
        }
        with patch.object(
            remote_probe, "collect", side_effect=OSError("probe unavailable")
        ):
            result = remote_probe.send_report(config, remote_probe.make_report(config))
        self.assertTrue(result["accepted"])
        self.assertEqual(self.store.latest()["alpha"]["status"], "offline")

    def test_demo_refuses_ingestion(self):
        self.collector.demo = True
        with self.assertRaises(HTTPError) as error:
            self.post(report())
        self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
