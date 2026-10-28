"""Monitoring use cases, independent of HTTP handlers and background scheduling."""

import hmac
import threading
import time
from copy import deepcopy

from .history import assemble_history, history_bucket, history_range
from .protocol import validate_report


class AgentAuthenticationError(PermissionError):
    """The presented credential does not identify the report's configured node."""


class IngestionDisabledError(PermissionError):
    """Synthetic workspaces cannot receive real reports."""


def classify(snapshot):
    warnings = snapshot.setdefault("warnings", [])
    system = snapshot.get("system", {})
    if (system.get("memory_percent") or 0) >= 95:
        warnings.append("系统内存使用率达到 95%")
    for disk in system.get("disks", []):
        if (disk.get("percent") or 0) >= 90:
            warnings.append(f"磁盘 {disk['mount']} 使用率 {disk['percent']:.0f}%")
    for gpu in snapshot.get("gpus", []):
        if (gpu.get("temperature") or 0) >= 85:
            warnings.append(f"GPU {gpu['index']} 温度 {gpu['temperature']:.0f}°C")
    snapshot["status"] = "warning" if warnings else "online"
    return snapshot


class MonitoringService:
    def __init__(
        self,
        hosts,
        store,
        interval=30,
        stale_after=100,
        retention_days=30,
        demo=False,
        credentials=None,
    ):
        if type(interval) is not int or not 5 <= interval <= 3600:
            raise ValueError("采样间隔应为 5—3600 秒的整数")
        self.hosts, self.store = hosts, store
        self.host_map = {host.name: host for host in hosts}
        self.interval, self.stale_after = interval, stale_after
        self.retention_days, self.demo = retention_days, demo
        self.credentials = credentials or {}
        self.lock = threading.Lock()
        self.collecting = False
        self.last_cycle = None
        self.started_at = time.time()
        previous = store.latest()
        self.last_reports = {host.name: None for host in hosts}
        self.snapshots = {
            host.name: {
                **host.public(),
                "status": "pending",
                "system": {},
                "gpus": [],
                "checked_at": None,
                "warnings": [],
                "error": None,
                **{
                    key: previous.get(host.name, {}).get(key)
                    for key in ("last_seen", "received_at", "report_id", "collected_at")
                },
                "known_gpu_count": previous.get(host.name, {}).get(
                    "known_gpu_count", 0
                ),
            }
            for host in hosts
        }

    def overview(self, now=None):
        with self.lock:
            return {
                "hosts": deepcopy(list(self.snapshots.values())),
                "demo": self.demo,
                "mode": "demo" if self.demo else "push",
                "stale_after": self.stale_after,
                "interval": self.interval,
                "retention_days": self.retention_days,
                "collecting": self.collecting,
                "last_cycle": self.last_cycle,
                "server_time": time.time() if now is None else now,
            }

    def history(self, start=None, end=None, host=None, now=None):
        start, end = history_range(start, end, time.time() if now is None else now)
        if host and host not in self.host_map:
            raise ValueError("未知的服务器")
        names = [host] if host else list(self.host_map)
        bucket = history_bucket(start, end, self.interval)
        rows = self.store.query_history(start, end, bucket, host or None, names)
        return assemble_history(start, end, self.interval, bucket, names, **rows)

    def prune(self):
        self.store.prune(self.retention_days)

    def ensure_ingestion_enabled(self):
        if self.demo:
            raise IngestionDisabledError("演示模式不接受真实节点上报")

    def ingest(self, payload, authorization, now=None):
        """Authenticate, validate, persist, then publish and acknowledge a report."""
        self.ensure_ingestion_enabled()
        name = payload.get("host") if isinstance(payload, dict) else None
        if not self.authorized(name, authorization):
            raise AgentAuthenticationError("无效的节点上报身份")
        return self.accept_report(payload, now)

    def authorized(self, name, header):
        token = self.credentials.get(name) if isinstance(name, str) else None
        if self.demo or not token or not isinstance(header, str):
            return False
        return hmac.compare_digest(("Bearer " + token).encode(), header.encode())

    def _save(self, snapshot):
        """Caller holds lock across persistence and publishing, including dedup checks."""
        previous = self.snapshots[snapshot["name"]]
        good = snapshot["status"] in ("online", "warning")
        snapshot["last_seen"] = (
            snapshot["checked_at"] if good else previous.get("last_seen")
        )
        snapshot["known_gpu_count"] = (
            len(snapshot["gpus"]) if good else previous.get("known_gpu_count", 0)
        )
        self.store.record(snapshot, interval=self.interval)
        self.snapshots[snapshot["name"]] = snapshot
        self.last_cycle = snapshot["checked_at"]

    def accept(self, snapshot):
        """Internal synthetic snapshots only; external ingestion uses ingest()."""
        with self.lock:
            self._save(deepcopy(snapshot))

    def accept_report(self, payload, now=None):
        """Apply an already authenticated report; transports must use ingest()."""
        now = time.time() if now is None else now
        report = validate_report(payload, self.interval, now)
        host = self.host_map.get(payload.get("host"))
        if not host or self.demo:
            raise ValueError("节点未配置或当前为演示模式")
        with self.lock:
            previous = self.snapshots[host.name]
            if report["report_id"] == previous.get("report_id"):
                return {"accepted": False, "duplicate": True, "server_time": now}
            last_collected = previous.get("collected_at")
            if last_collected is not None and report["collected_at"] <= last_collected:
                raise ValueError("拒绝乱序上报，请重新采样并检查节点时钟")
            snapshot = {
                **host.public(),
                **report,
                "checked_at": now,
                "received_at": now,
            }
            if report["error"]:
                snapshot["status"] = "offline"
            else:
                classify(snapshot)
            self._save(snapshot)
            self.last_reports[host.name] = now
        return {"accepted": True, "duplicate": False, "server_time": now}

    def tick(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            for host in self.hosts:
                last = self.last_reports[host.name]
                if (
                    now - (last if last is not None else self.started_at)
                    < self.stale_after
                ):
                    continue
                previous = self.snapshots[host.name]
                if (
                    previous.get("checked_at")
                    and now - previous["checked_at"] < self.interval
                ):
                    continue
                snapshot = {
                    **host.public(),
                    "checked_at": now,
                    "status": "offline",
                    "system": {},
                    "gpus": [],
                    "warnings": [],
                    "collection_ms": None,
                    "error": f"超过 {self.stale_after} 秒未收到节点采集脚本上报，请检查脚本服务及到中心的网络",
                    **{
                        key: previous.get(key)
                        for key in ("received_at", "report_id", "collected_at")
                    },
                }
                self._save(snapshot)
