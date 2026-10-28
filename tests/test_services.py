"""Business and SQLite regressions with no HTTP server, inventory or real agents."""

import copy
import csv
import io
import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from monitor.exports import history_csv
from monitor.models import Host
from monitor.service import (
    AgentAuthenticationError,
    IngestionDisabledError,
    MonitoringService,
)
from monitor.store import Store

TOKEN = "unit-test-alpha-agent-token"


def payload(ts=1000):
    return {
        "version": 1,
        "host": "alpha",
        "report_id": str(uuid.uuid4()),
        "interval": 30,
        "collected_at": ts,
        "system": {"cpu_percent": 0, "memory_percent": None},
        "gpus": [
            {
                "index": 0,
                "name": "test GPU",
                "utilization": 50,
                "memory_used_mb": 100,
                "memory_total_mb": 1000,
                "processes": [],
            }
        ],
    }


class MonitoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "history.sqlite3", mode="live")
        self.hosts = [Host("alpha", "192.0.2.1"), Host("beta", "192.0.2.2")]
        self.service = MonitoringService(
            self.hosts, self.store, credentials={"alpha": TOKEN}
        )
        self.service.started_at = 1000

    def ingest(self, report, now=1000):
        return self.service.ingest(report, "Bearer " + TOKEN, now=now)

    def test_ingestion_authentication_and_demo_policy_do_not_need_http(self):
        report = payload()
        for body, credential in (
            (report, None),
            (report, "Bearer incorrect-viewer-token"),
            ({**report, "host": "beta"}, "Bearer " + TOKEN),
            ({**report, "host": []}, "Bearer " + TOKEN),
            ([], "Bearer " + TOKEN),
        ):
            with self.subTest(body=body), self.assertRaises(AgentAuthenticationError):
                self.service.ingest(body, credential, now=1000)
        self.service.demo = True
        with self.assertRaises(IngestionDisabledError):
            self.service.ingest(None, None, now=1000)
        self.assertEqual(self.store.latest(), {})

    def test_acknowledgement_requires_storage_and_failure_is_retryable(self):
        report = payload()
        before = self.service.overview(now=1000)
        with (
            patch.object(
                self.store, "record", side_effect=sqlite3.OperationalError("disk full")
            ),
            self.assertRaises(sqlite3.OperationalError),
        ):
            self.ingest(report)
        self.assertEqual(self.service.overview(now=1000), before)
        self.assertIsNone(self.service.last_reports["alpha"])
        self.assertEqual(self.store.latest(), {})
        self.assertEqual(
            self.ingest(report),
            {"accepted": True, "duplicate": False, "server_time": 1000},
        )
        self.assertEqual(self.store.latest()["alpha"]["report_id"], report["report_id"])
        self.assertEqual(
            self.service.history(1000, 1030, now=1030)["aggregate"]["samples"], 1
        )

    def test_duplicate_ingestion_is_atomic_under_concurrency(self):
        report = payload()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.ingest(report), range(16)))
        self.assertEqual(sum(item["accepted"] for item in results), 1)
        self.assertEqual(sum(item["duplicate"] for item in results), 15)
        self.assertEqual(
            self.service.history(1000, 1030, now=1030)["aggregate"]["samples"], 1
        )
        self.assertEqual(self.service.last_reports["alpha"], 1000)

    def test_returned_and_synthetic_snapshots_are_detached(self):
        report = payload()
        self.ingest(report)
        report["gpus"][0]["utilization"] = 100
        overview = self.service.overview(now=1000)
        overview["hosts"][0]["gpus"][0]["utilization"] = 0
        overview["hosts"][0]["warnings"].append("consumer mutation")
        snapshot = self.service.overview(now=1000)["hosts"][0]
        self.assertEqual(snapshot["gpus"][0]["utilization"], 50)
        self.assertEqual(snapshot["warnings"], [])
        self.service.accept(snapshot)
        snapshot["gpus"].clear()
        self.assertEqual(len(self.service.overview()["hosts"][0]["gpus"]), 1)
        self.assertEqual(len(self.store.latest()["alpha"]["gpus"]), 1)

    def test_ranges_host_scope_and_missing_data_are_service_rules(self):
        self.ingest(payload())
        self.assertEqual(
            {
                key: self.service.history(now=4600)[key]
                for key in ("start", "end", "bucket_seconds")
            },
            {"start": 1000, "end": 4600, "bucket_seconds": 30},
        )
        for start, end in (
            ("nan", 1030),
            (1000, "inf"),
            ("no", 1030),
            (-1, 10),
            (1000, 999),
            (0, 31 * 86400 + 1),
            (1000, 1061),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.service.history(start, end, now=1000)
        with self.assertRaisesRegex(ValueError, "未知的服务器"):
            self.service.history(1000, 1030, host="removed", now=1030)
        history = self.service.history("1000", "1030", now=1030)
        self.assertEqual([row["host"] for row in history["stats"]], ["alpha", "beta"])
        self.assertEqual(history["aggregate"]["avg_cpu"], 0)
        self.assertIsNone(history["aggregate"]["avg_memory"])
        self.assertEqual(history["aggregate"]["coverage"], 50)
        self.assertEqual(history["aggregate"]["availability"], 100)
        self.assertEqual(history["stats"][1]["samples"], 0)
        self.assertIsNone(history["stats"][1]["availability"])
        scoped = MonitoringService([self.hosts[1]], self.store)
        self.assertEqual(
            scoped.history(1000, 1030, now=1030)["aggregate"]["samples"], 0
        )
        self.assertEqual(
            MonitoringService([], self.store).history(1000, 1030, now=1030)["stats"], []
        )

    def test_partial_interval_and_query_endpoint_do_not_inflate_gpu_hours(self):
        self.ingest(payload())
        self.ingest(payload(1010), now=1010)
        history = self.service.history(1000, 1015, host="alpha", now=1030)
        self.assertEqual(history["aggregate"]["observed_seconds"], 15)
        self.assertAlmostEqual(history["aggregate"]["observed_gpu_hours"], 15 / 3600)
        self.assertAlmostEqual(history["aggregate"]["busy_gpu_hours"], 15 / 3600)
        self.assertEqual(history["points"][0]["ts"], 1007.5)
        # End is exclusive, even when another sample lies exactly on it.
        self.assertEqual(
            self.service.history(1000, 1010, now=1030)["aggregate"]["samples"], 1
        )

    def test_memory_statistics_are_capacity_weighted(self):
        report = payload()
        report["gpus"].append(
            {
                **report["gpus"][0],
                "index": 1,
                "utilization": None,
                "memory_used_mb": 2700,
                "memory_total_mb": 3000,
            }
        )
        self.ingest(report)
        result = self.service.history(1000, 1030, host="alpha", now=1030)
        self.assertEqual(result["aggregate"]["avg_gpu"], 50)
        self.assertEqual(result["aggregate"]["avg_gpu_memory"], 70)
        self.assertEqual(result["points"][0]["gpu_memory"], 70)
        self.assertAlmostEqual(result["aggregate"]["observed_gpu_hours"], 30 / 3600)

    def test_csv_serialization_is_pure_and_preserves_null_cells(self):
        history = self.service.history(1000, 1030, now=1030)
        original = copy.deepcopy(history)
        for name in ("=SUM(1,1)", "+1", "-1", "@name", "\tname", "\rname", "normal"):
            history["stats"][0]["host"] = name
            encoded = history_csv(history)
            self.assertTrue(encoded.startswith(b"\xef\xbb\xbf"))
            row = list(csv.reader(io.StringIO(encoded.decode("utf-8-sig"))))[1]
            self.assertEqual(row[0], name if name == "normal" else "'" + name)
            self.assertEqual(row[5:11], [""] * 6)
            self.assertEqual(history["stats"][0]["host"], name)
        history["stats"][0]["host"] = "alpha"
        self.assertEqual(history, original)

    def test_latest_and_samples_rollback_together_on_serialization_failure(self):
        self.ingest(payload())
        original = self.store.latest()
        broken = copy.deepcopy(original["alpha"])
        broken["checked_at"] = 1030
        broken["warnings"] = [float("nan")]
        with self.assertRaises(ValueError):
            self.store.record(broken)
        self.assertEqual(self.store.latest(), original)
        self.assertEqual(
            self.service.history(1000, 1060, now=1060)["aggregate"]["samples"], 1
        )
        with self.store.connection() as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_history_graph_rows_and_totals_share_one_read_snapshot(self):
        self.ingest(payload())
        writer = Store(self.store.path)
        new_snapshot = self.store.latest()["alpha"]
        new_snapshot["checked_at"] = 1010
        connection = self.store.connection
        wrote = False

        @contextmanager
        def concurrent_connection():
            def after_series(statement):
                nonlocal wrote
                if "WITH ordered" in statement and not wrote:
                    wrote = True
                    writer.record(new_snapshot)

            with connection() as db:
                db.set_trace_callback(after_series)
                yield db

        with patch.object(self.store, "connection", concurrent_connection):
            history = self.service.history(1000, 1030, now=1030)
        self.assertTrue(wrote)
        self.assertEqual(history["stats"][0]["samples"], 1)
        self.assertEqual(history["aggregate"]["samples"], 1)
        self.assertEqual(
            self.service.history(1000, 1030, now=1030)["aggregate"]["samples"], 2
        )


if __name__ == "__main__":
    unittest.main()
