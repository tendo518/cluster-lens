"""Application tests: python3 -S -m unittest discover -s tests. No real SSH."""

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from monitor import remote_probe
from monitor.collector import Collector, classify
from monitor.demo import demo_hosts, demo_snapshot, seed_demo
from monitor.models import Host
from monitor.server import MonitorServer
from monitor.store import Store


def snapshot(host="alpha", ts=100, status="online", gpu_values=(50,), cpu=25):
    return {
        "name": host,
        "address": "127.0.0.1",
        "checked_at": ts,
        "status": status,
        "system": {"cpu_percent": cpu, "memory_percent": 40},
        "warnings": [],
        "gpus": [
            {
                "index": i,
                "utilization": value,
                "memory_used_mb": 100,
                "memory_total_mb": 1000,
                "processes": [],
                "temperature": 50,
            }
            for i, value in enumerate(gpu_values)
        ],
    }


class ProbeTests(unittest.TestCase):
    XML = """<nvidia_smi_log><driver_version>535.1</driver_version><gpu>
      <product_name>Tesla V100</product_name><uuid>GPU-one</uuid><minor_number>0</minor_number>
      <utilization><gpu_util>0 %</gpu_util></utilization>
      <fb_memory_usage><used>0 MiB</used><total>32768 MiB</total></fb_memory_usage>
      <temperature><gpu_temp>45 C</gpu_temp></temperature><fan_speed>N/A</fan_speed>
      <power_readings><power_draw>29.5 W</power_draw><power_limit>250 W</power_limit></power_readings>
      <processes><process_info><pid>1234</pid><type>C</type><process_name>/bin/python</process_name>
      <used_memory>N/A</used_memory></process_info></processes></gpu>
      <gpu><product_name>New GPU</product_name><minor_number>1</minor_number>
      <utilization><gpu_util>N/A</gpu_util></utilization>
      <gpu_power_readings><power_draw>110 W</power_draw><power_limit>300 W</power_limit></gpu_power_readings>
      </gpu></nvidia_smi_log>"""

    def test_gpu_xml_zero_na_power_versions_and_missing_user(self):
        with (
            patch.object(remote_probe.shutil, "which", return_value="/bin/nvidia-smi"),
            patch.object(
                remote_probe.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, self.XML, ""),
            ),
            patch.object(remote_probe.os, "stat", side_effect=PermissionError),
        ):
            gpus, warnings = remote_probe.gpu_data()
        self.assertFalse(warnings)
        self.assertEqual(gpus[0]["utilization"], 0)
        self.assertEqual(gpus[0]["memory_percent"], 0)
        self.assertEqual(gpus[0]["power_w"], 29.5)
        self.assertIsNone(gpus[0]["fan_percent"])
        self.assertIsNone(gpus[0]["processes"][0]["user"])
        self.assertIsNone(gpus[0]["processes"][0]["memory_mb"])
        self.assertIsNone(gpus[1]["utilization"])
        self.assertEqual(gpus[1]["power_w"], 110)

    def test_gpu_process_owner_comes_from_remote_proc_uid(self):
        with (
            patch.object(remote_probe.shutil, "which", return_value="nvidia-smi"),
            patch.object(
                remote_probe.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, self.XML, ""),
            ),
            patch.object(
                remote_probe.os, "stat", return_value=SimpleNamespace(st_uid=123)
            ),
            patch.object(
                remote_probe.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_name="labuser"),
            ) as lookup,
        ):
            process = remote_probe.gpu_data()[0][0]["processes"][0]
        self.assertEqual(process["user"], "labuser")
        self.assertEqual(process["pid"], 1234)
        lookup.assert_called_once_with(123)

    def test_probe_source_stays_python36_compatible(self):
        source = Path(remote_probe.__file__).read_text()
        ast.parse(source, feature_version=(3, 6))
        self.assertNotIn("capture_output=", source)
        self.assertNotIn("text=True", source)
        self.assertNotIn(".isascii(", source)  # Only available starting in Python 3.7.

    def test_driver_failure_and_cpu_only(self):
        with patch.object(remote_probe.shutil, "which", return_value=None):
            self.assertEqual(remote_probe.gpu_data(), ([], []))
        with (
            patch.object(remote_probe.shutil, "which", return_value="nvidia-smi"),
            patch.object(
                remote_probe.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1, "", "driver error"),
            ),
        ):
            gpus, warnings = remote_probe.gpu_data()
            self.assertFalse(gpus)
            self.assertIn("driver error", warnings[0])

    def test_bad_xml_is_warning_not_crash(self):
        with (
            patch.object(remote_probe.shutil, "which", return_value="nvidia-smi"),
            patch.object(
                remote_probe.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "broken", ""),
            ),
        ):
            self.assertTrue(remote_probe.gpu_data()[1])

    def test_cpu_does_not_double_count_guest(self):
        with patch.object(
            remote_probe, "read_text", return_value="cpu 100 0 20 300 10 0 0 0 50 0\n"
        ):
            self.assertEqual(remote_probe.cpu_ticks(), (430, 310))

    def test_memory_uses_available_not_free_and_zero_swap(self):
        with patch.object(
            remote_probe,
            "read_text",
            return_value="MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
        ):
            memory = remote_probe.memory()
        self.assertEqual(memory["memory_percent"], 60)
        self.assertEqual(memory["swap_percent"], 0)

    def test_classify_hot_gpu_and_full_disk(self):
        data = snapshot()
        data["gpus"][0]["temperature"] = 88
        data["system"]["disks"] = [{"mount": "/data", "percent": 95}]
        self.assertEqual(classify(data)["status"], "warning")
        self.assertEqual(len(data["warnings"]), 2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "history.sqlite3", interval=30)

    def tearDown(self):
        self.temp.cleanup()

    def test_gpu_averages_are_card_weighted(self):
        self.store.record_many(
            [
                snapshot("alpha", 100, gpu_values=(100,)),
                snapshot("beta", 100, gpu_values=(0, 0, 0)),
            ]
        )
        data = self.store.history(100, 130, names=["alpha", "beta"])
        self.assertEqual(data["aggregate"]["avg_gpu"], 25)
        self.assertEqual(data["aggregate"]["peak_gpu"], 100)
        self.assertEqual(data["points"][0]["gpu"], 25)
        self.assertEqual(data["aggregate"]["coverage"], 100)
        self.assertAlmostEqual(data["aggregate"]["busy_gpu_hours"], 30 / 3600)

    def test_offline_is_not_zero_and_gaps_are_not_carried(self):
        self.store.record_many(
            [
                snapshot(ts=100, gpu_values=(80,)),
                snapshot(ts=130, status="offline"),
                snapshot(ts=400, gpu_values=(40,)),
            ]
        )
        data = self.store.history(100, 430, names=["alpha"])
        self.assertEqual(data["aggregate"]["avg_gpu"], 60)
        self.assertAlmostEqual(data["aggregate"]["busy_gpu_hours"], 60 / 3600)
        self.assertAlmostEqual(data["aggregate"]["availability"], 100 * 2 / 3)
        self.assertAlmostEqual(data["aggregate"]["coverage"], 100 * 90 / 330)
        self.assertIsNone(data["points"][1]["gpu"])
        self.assertTrue(any(p["cpu"] is None for p in data["points"]))

    def test_no_data_and_missing_hosts(self):
        data = self.store.history(100, 130, names=["alpha", "beta"])
        self.assertEqual(data["points"], [])
        self.assertIsNone(data["aggregate"]["avg_gpu"])
        self.assertIsNone(data["aggregate"]["availability"])
        self.assertEqual(len(data["stats"]), 2)
        self.assertEqual(data["stats"][0]["samples"], 0)

    def test_zero_gpu_and_na_are_distinct(self):
        self.store.record_many(
            [snapshot(ts=100, gpu_values=(0,)), snapshot(ts=130, gpu_values=(None,))]
        )
        data = self.store.history(100, 160, names=["alpha"])
        self.assertEqual(data["aggregate"]["avg_gpu"], 0)
        self.assertEqual(data["points"][0]["gpu"], 0)
        self.assertIsNone(data["points"][1]["gpu"])
        self.assertEqual(data["aggregate"]["busy_gpu_hours"], 0)

    def test_filter_limits_to_current_inventory(self):
        self.store.record_many(
            [snapshot("alpha", 100), snapshot("deleted", 100, gpu_values=(100,))]
        )
        data = self.store.history(100, 130, names=["alpha"])
        self.assertEqual(data["aggregate"]["avg_gpu"], 50)
        self.assertEqual(len(data["stats"]), 1)

    def test_interval_change_does_not_rewrite_historical_gpu_hours(self):
        self.store.record(snapshot(ts=100))
        self.store.interval = 300
        data = self.store.history(100, 400, names=["alpha"])
        self.assertAlmostEqual(data["aggregate"]["busy_gpu_hours"], 30 / 3600)

    def test_latest_and_retention(self):
        self.store.record_many([snapshot(ts=1), snapshot(ts=time.time())])
        self.assertGreater(self.store.latest()["alpha"]["checked_at"], 1)
        self.store.prune(1)
        with self.store.connection() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1
            )
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_demo_and_live_databases_cannot_be_mixed(self):
        path = Path(self.temp.name) / "separate.sqlite3"
        Store(path, mode="demo")
        Store(path, mode="demo")
        with self.assertRaisesRegex(ValueError, "不能共用"):
            Store(path, mode="live")

    def test_collector_last_seen_is_preserved_but_metrics_are_not(self):
        collector = Collector([Host("alpha", "127.0.0.1")], self.store)
        collector.accept(snapshot(ts=100))
        offline = snapshot(ts=130, status="offline", gpu_values=())
        offline["system"] = {}
        collector.accept(offline)
        result = collector.overview()["hosts"][0]
        self.assertEqual(result["last_seen"], 100)
        self.assertEqual(result["known_gpu_count"], 1)
        self.assertEqual(result["system"], {})

    def test_demo_never_executes_commands(self):
        self.store.interval = 3600
        with patch(
            "subprocess.run", side_effect=AssertionError("Demo cannot execute commands")
        ):
            hosts = demo_hosts()
            seed_demo(self.store, hosts)
            self.assertEqual(len(self.store.latest()), 8)
            self.assertEqual(demo_snapshot(hosts[-1], time.time())["status"], "offline")
            self.assertTrue(
                any(s["status"] == "warning" for s in self.store.latest().values())
            )


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        store = Store(Path(cls.temp.name) / "http.sqlite3")
        hosts = [
            Host("alpha", "127.0.0.1"),
            Host("=SUM(1,1)", "localhost"),
        ]
        cls.collector = Collector(hosts, store)
        cls.collector.accept(snapshot("alpha", time.time() - 10))
        cls.collector.accept(snapshot("=SUM(1,1)", time.time() - 10))
        cls.server = MonitorServer(
            ("127.0.0.1", 0), cls.collector, "test-monitor-token-long"
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def get(self, path, token=True):
        return urlopen(
            Request(
                self.url + path,
                headers={"Authorization": "Bearer test-monitor-token-long"}
                if token
                else {},
            ),
            timeout=5,
        )

    def test_auth_and_no_credential_exposure(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/api/overview", token=False)
        self.assertEqual(error.exception.code, 401)
        with self.get("/api/overview") as response:
            body = response.read().decode()
            self.assertNotIn("test-monitor-token-long", body)
            self.assertNotIn("variables", body)
            self.assertEqual(len(json.loads(body)["hosts"]), 2)

    def test_static_is_available_before_login_and_secure_headers(self):
        for path in (
            "/",
            "/app.js",
            "/appearance.js",
            "/styles.css",
            "/palettes.css",
            "/favicon.svg",
        ):
            with self.get(path, token=False) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn(
                    "frame-ancestors 'none'",
                    response.headers["Content-Security-Policy"],
                )
                self.assertGreater(len(response.read()), 100)

    def test_dashboard_copy_keeps_controls_without_slogans(self):
        with self.get("/", token=False) as response:
            body = response.read().decode()
        for phrase in (
            "只读监控，安心运行",
            "所有节点，一目了然",
            "实时掌握算力资源与任务运行状态",
            "算力有数，研究有序",
            "用户不可读时标记为未知",
            "不采集进程命令行参数",
            "让算力分配更有依据",
            "了解正在运行的任务，以及每份显存的去向",
            "CLUSTER OBSERVABILITY",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, body)
        for control in (
            "refresh-button",
            "pinned-servers",
            "server-search",
            "status-filter",
            "history",
            "processes",
            "process-footer",
            "process-page-size",
            "process-pagination",
            "process-page-info",
            "process-next",
            "appearance-toggle",
            "appearance-panel",
            "appearance-theme",
            "appearance-palette",
        ):
            with self.subTest(control=control):
                self.assertIn(f'id="{control}"', body)
        self.assertIn("仅统计有效采样", body)
        self.assertIn('href="/palettes.css"', body)
        self.assertIn('aria-controls="appearance-panel"', body)
        self.assertIn('aria-expanded="false"', body)
        self.assertIn('role="dialog" aria-label="外观设置" hidden', body)
        for palette in ("green", "blue", "purple", "amber", "teal", "rose"):
            self.assertIn(f'<option value="{palette}">', body)
        self.assertLess(
            body.index('src="/appearance.js"'), body.index('href="/styles.css"')
        )

    def test_workspace_navigation_matches_page_order(self):
        with self.get("/", token=False) as response:
            body = response.read().decode()
        sections = re.findall(r'data-nav="([^"]+)"', body)
        self.assertEqual(sections, ["overview", "history", "servers", "processes"])
        positions = [body.index(f'id="{section}"') for section in sections]
        self.assertEqual(positions, sorted(positions))
        for section in sections:
            with self.subTest(section=section):
                self.assertEqual(body.count(f'id="{section}"'), 1)
                self.assertIn(f'href="#{section}" data-nav="{section}"', body)
                self.assertIn(f'id="{section}-title" tabindex="-1"', body)
        self.assertEqual(body.count('aria-current="location"'), 1)
        self.assertIn('data-nav="overview" aria-current="location"', body)

    def test_history_rejects_bad_ranges_and_hosts(self):
        for query in (
            "start=nan",
            "start=inf",
            "start=nope",
            "start=200&end=100",
            "start=0&end=999999999999",
            "host=not-managed",
        ):
            with self.subTest(query=query), self.assertRaises(HTTPError) as error:
                self.get("/api/history?" + query)
            self.assertEqual(error.exception.code, 400)

    def test_history_export_and_formula_injection_protection(self):
        with self.get("/api/history?host=alpha") as response:
            data = json.loads(response.read())
            self.assertEqual(len(data["stats"]), 1)
            self.assertEqual(data["stats"][0]["avg_gpu"], 50)
        with self.get("/api/export.csv") as response:
            body = response.read().decode("utf-8-sig")
            self.assertIn("服务器", body)
            self.assertIn("'=SUM(1,1)", body)
            self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_no_path_traversal_or_write_api(self):
        for path in (
            "/../inventory/hosts.ini",
            "/%2e%2e/inventory/hosts.ini",
            "/api/unknown",
        ):
            with self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + "/api/reboot", method="POST"), timeout=5)
        self.assertEqual(error.exception.code, 405)

    def test_public_bind_requires_token(self):
        env = {k: v for k, v in os.environ.items() if k != "CLUSTERLENS_VIEWER_TOKEN"}
        result = subprocess.run(
            [sys.executable, "-m", "monitor", "--demo", "--host", "0.0.0.0"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("CLUSTERLENS_VIEWER_TOKEN", result.stderr)


if __name__ == "__main__":
    unittest.main()
