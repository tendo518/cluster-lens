"""Open a pre-refactor database fixture without changing schema or existing rows."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from monitor.models import Host
from monitor.service import MonitoringService
from monitor.store import Store

# Historical schema captured before the service/storage separation. Deliberately
# independent of Store's current DDL and sample_row implementation.
SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE latest (host TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE samples (
    host TEXT NOT NULL, ts REAL NOT NULL, status TEXT NOT NULL,
    cpu REAL, memory REAL, gpu_util_sum REAL, gpu_valid_count INTEGER NOT NULL,
    gpu_peak REAL, gpu_memory_used REAL, gpu_memory_total REAL,
    gpu_count INTEGER NOT NULL, gpu_busy_count INTEGER NOT NULL,
    sample_seconds INTEGER NOT NULL, PRIMARY KEY (host, ts)
);
CREATE INDEX samples_ts ON samples(ts);
INSERT INTO metadata VALUES ('mode', 'live');
INSERT INTO samples VALUES ('node-a', 1000, 'online', 25, NULL, 50, 1, 50,
    100, 1000, 1, 1, 30);
"""


class StorageCompatibilityTests(unittest.TestCase):
    def test_existing_wal_history_identity_and_receipts_survive_open_and_append(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.sqlite3"
            old = {
                "name": "node-a",
                "address": "192.0.2.1",
                "groups": [],
                "checked_at": 1000,
                "status": "online",
                "last_seen": 1000,
                "received_at": 1000,
                "report_id": "d9093d1c-ebf7-498f-bcc8-5e0acb8f2dcc",
                "collected_at": 990,
                "known_gpu_count": 1,
                "gpus": [{"index": 0, "utilization": 50}],
                "system": {"cpu_percent": 25},
            }
            # Keep the original writer connected: the fixture includes WAL data,
            # not just a checkpointed copy of the main database file.
            with closing(sqlite3.connect(path)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.executescript(SCHEMA)
                writer.execute(
                    "INSERT INTO latest VALUES (?, ?)", ("node-a", json.dumps(old))
                )
                writer.commit()
                original_rows = writer.execute("SELECT * FROM samples").fetchall()
                original_schema = writer.execute(
                    "SELECT name, sql FROM sqlite_master ORDER BY name"
                ).fetchall()
                inode = path.stat().st_ino
                store = Store(path, mode="live")
                service = MonitoringService([Host("node-a", "192.0.2.1")], store)
                node = service.overview()["hosts"][0]
                self.assertEqual(node["status"], "pending")
                self.assertEqual(node["gpus"], [])
                self.assertEqual(node["last_seen"], 1000)
                self.assertEqual(node["report_id"], old["report_id"])
                self.assertEqual(store.latest()["node-a"], old)
                result = service.history(1000, 1030, now=1030)
                self.assertEqual(result["aggregate"]["avg_gpu"], 50)
                self.assertIsNone(result["aggregate"]["avg_memory"])
                self.assertAlmostEqual(result["aggregate"]["busy_gpu_hours"], 30 / 3600)
                self.assertEqual(
                    writer.execute("SELECT * FROM samples").fetchall(), original_rows
                )
                self.assertEqual(
                    writer.execute(
                        "SELECT name, sql FROM sqlite_master ORDER BY name"
                    ).fetchall(),
                    original_schema,
                )
                service.accept({**old, "checked_at": 1030})
                self.assertEqual(
                    writer.execute("SELECT * FROM samples WHERE ts = 1000").fetchall(),
                    original_rows,
                )
                self.assertEqual(
                    writer.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 2
                )
                self.assertEqual(
                    writer.execute("PRAGMA quick_check").fetchone()[0], "ok"
                )
                self.assertEqual(path.stat().st_ino, inode)


if __name__ == "__main__":
    unittest.main()
