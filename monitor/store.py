"""SQLite history. Missing / failed measurements are NULL, never zero utilization."""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ONLINE = ("online", "warning")
INSERT = """INSERT OR REPLACE INTO samples
    (host, ts, status, cpu, memory, gpu_util_sum, gpu_valid_count, gpu_peak,
     gpu_memory_used, gpu_memory_total, gpu_count, gpu_busy_count, sample_seconds)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def sample_row(snapshot, interval=30):
    available = snapshot["status"] in ONLINE
    system = snapshot.get("system", {}) if available else {}
    gpus = snapshot.get("gpus", []) if available else []
    valid = [g["utilization"] for g in gpus if g.get("utilization") is not None]
    memory = [
        g
        for g in gpus
        if g.get("memory_used_mb") is not None and g.get("memory_total_mb")
    ]
    return (
        snapshot["name"],
        snapshot["checked_at"],
        snapshot["status"],
        system.get("cpu_percent"),
        system.get("memory_percent"),
        sum(valid) if valid else None,
        len(valid),
        max(valid) if valid else None,
        sum(g["memory_used_mb"] for g in memory) if memory else None,
        sum(g["memory_total_mb"] for g in memory) if memory else None,
        len(gpus),
        sum(value >= 10 for value in valid),
        interval,
    )


class Store:
    def __init__(self, path, interval=30, mode=None):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        self.interval = interval
        self.write_lock = threading.Lock()
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS latest (host TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples (
                    host TEXT NOT NULL, ts REAL NOT NULL, status TEXT NOT NULL,
                    cpu REAL, memory REAL, gpu_util_sum REAL, gpu_valid_count INTEGER NOT NULL,
                    gpu_peak REAL, gpu_memory_used REAL, gpu_memory_total REAL,
                    gpu_count INTEGER NOT NULL, gpu_busy_count INTEGER NOT NULL,
                    sample_seconds INTEGER NOT NULL,
                    PRIMARY KEY (host, ts)
                );
                CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
            """)
            if mode is not None:
                previous = db.execute(
                    "SELECT value FROM metadata WHERE key = 'mode'"
                ).fetchone()
                if previous and previous[0] != mode:
                    raise ValueError(
                        "演示和真实监控不能共用数据库，请使用不同的 --db 路径"
                    )
                db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('mode', ?)", (mode,)
                )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def latest(self):
        with self.connection() as db:
            return {
                row["host"]: json.loads(row["payload"])
                for row in db.execute("SELECT * FROM latest")
            }

    def record_many(self, snapshots):
        latest = {}

        def rows():
            for snapshot in snapshots:
                latest[snapshot["name"]] = snapshot
                yield sample_row(snapshot, self.interval)

        with self.write_lock, self.connection() as db:
            db.executemany(INSERT, rows())
            db.executemany(
                "INSERT OR REPLACE INTO latest VALUES (?, ?)",
                (
                    (name, json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
                    for name, snapshot in latest.items()
                ),
            )

    def record(self, snapshot):
        self.record_many([snapshot])

    def prune(self, retention_days):
        with self.write_lock, self.connection() as db:
            db.execute(
                "DELETE FROM samples WHERE ts < ?",
                (time.time() - retention_days * 86400,),
            )

    def query_history(self, start, end, bucket, host=None, names=()):
        """Return aggregate facts from one read snapshot; service owns range policy."""
        where = "ts >= ? AND ts < ?"
        params = [start, end]
        if host:
            where += " AND host = ?"
            params.append(host)
        elif names:
            where += " AND host IN (" + ",".join("?" for _ in names) + ")"
            params.extend(names)
        else:
            where += " AND 0"
        with self.connection() as db:
            # Keep the graph, per-host rows and totals on the same WAL snapshot.
            db.execute("BEGIN")
            series = db.execute(
                f"""
                SELECT CAST((ts - ?) / ? AS INTEGER) AS bucket,
                    AVG(cpu) AS cpu, AVG(memory) AS memory,
                    SUM(gpu_util_sum) / NULLIF(SUM(gpu_valid_count), 0) AS gpu,
                    100.0 * SUM(gpu_memory_used) / NULLIF(SUM(gpu_memory_total), 0) AS gpu_memory,
                    COUNT(*) AS samples
                FROM samples WHERE {where} GROUP BY bucket ORDER BY bucket
            """,
                [start, bucket, *params],
            ).fetchall()
            # A sample represents at most one configured interval. Long collection gaps,
            # downtime and service restarts must not turn into fictitious GPU-hours.
            base = f"""WITH ordered AS (
                SELECT *, LEAD(ts, 1, ts + sample_seconds) OVER (PARTITION BY host ORDER BY ts) AS next_ts
                FROM samples WHERE {where}
            ), weighted AS (
                SELECT *, MAX(0, MIN(next_ts - ts, sample_seconds, ? - ts)) AS duration FROM ordered
            )"""
            base_params = [*params, end]
            measures = """
                COUNT(*) AS samples,
                SUM(CASE WHEN status IN ('online', 'warning') THEN 1 ELSE 0 END) AS online_samples,
                SUM(duration) AS observed_seconds,
                SUM(CASE WHEN status IN ('online', 'warning') THEN duration ELSE 0 END) AS online_seconds,
                AVG(cpu) AS avg_cpu, AVG(memory) AS avg_memory,
                SUM(gpu_util_sum) / NULLIF(SUM(gpu_valid_count), 0) AS avg_gpu,
                MAX(gpu_peak) AS peak_gpu,
                100.0 * SUM(gpu_memory_used) / NULLIF(SUM(gpu_memory_total), 0) AS avg_gpu_memory,
                SUM(gpu_busy_count * duration) / 3600.0 AS busy_gpu_hours,
                SUM(gpu_valid_count * duration) / 3600.0 AS observed_gpu_hours
            """
            rows = db.execute(
                base
                + f" SELECT host, {measures} FROM weighted GROUP BY host ORDER BY host",
                base_params,
            ).fetchall()
            total = dict(
                db.execute(
                    base + f" SELECT {measures} FROM weighted", base_params
                ).fetchone()
            )

        return {
            "series": [dict(row) for row in series],
            "rows": [dict(row) for row in rows],
            "total": total,
        }
