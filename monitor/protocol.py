"""Bound and validate agent reports before they reach SQLite or the dashboard."""

import math
import uuid

MAX_REPORT_BYTES = 1024 * 1024
SYSTEM_NUMBERS = {
    "cpu_percent": 100,
    "memory_percent": 100,
    "swap_percent": 100,
    "memory_total_bytes": 2**60,
    "memory_used_bytes": 2**60,
    "swap_total_bytes": 2**60,
    "swap_used_bytes": 2**60,
    "uptime_seconds": 10**12,
    "cpu_count": 65536,
    "network_rx_bytes_s": 10**16,
    "network_tx_bytes_s": 10**16,
    "disk_read_bytes_s": 10**16,
    "disk_write_bytes_s": 10**16,
}
GPU_NUMBERS = {
    "utilization": 100,
    "memory_percent": 100,
    "fan_percent": 100,
    "memory_used_mb": 10**9,
    "memory_total_mb": 10**9,
    "temperature": 200,
    "power_w": 100000,
    "power_limit_w": 100000,
}


def text(value, field, limit=1024, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"无效的字符串字段：{field}")
    return value


def number(value, field, maximum, nullable=True):
    if value is None and nullable:
        return None
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= maximum
    ):
        raise ValueError(f"无效的数值字段：{field}")
    return value


def array(value, field, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"无效的列表字段：{field}")
    return value


def validate_report(report, interval, now):
    if (
        not isinstance(report, dict)
        or type(report.get("version")) is not int
        or report["version"] != 1
    ):
        raise ValueError("上报必须是 version=1 的 JSON 对象")
    report_id = text(report.get("report_id"), "report_id", 36)
    try:
        uuid.UUID(report_id)
    except ValueError:
        raise ValueError("report_id 必须是 UUID") from None
    collected = number(
        report.get("collected_at"), "collected_at", 10**12, nullable=False
    )
    if abs(now - collected) > max(60, interval * 2):
        raise ValueError("采样时间过旧或位于未来，请同步节点和中心服务器时钟后重新采样")
    if type(report.get("interval")) is not int or report["interval"] != interval:
        raise ValueError("节点 interval 与中心配置不一致，请重新生成配置并部署")
    result = {
        "report_id": report_id,
        "collected_at": collected,
        "collection_ms": number(report.get("collection_ms"), "collection_ms", 3600000),
        "error": text(report.get("error"), "error", 600, nullable=True),
        "warnings": [
            text(w, "warnings", 300)
            for w in array(report.get("warnings", []), "warnings", 32)
        ],
        "system": {},
        "gpus": [],
    }
    if result["error"]:
        return result
    system = report.get("system")
    if not isinstance(system, dict) or not system:
        raise ValueError("成功采集的上报必须包含 system")
    result["system"] = {
        key: number(system.get(key), key, maximum)
        for key, maximum in SYSTEM_NUMBERS.items()
    }
    for key in ("hostname", "os", "kernel"):
        result["system"][key] = text(system.get(key, "—"), key, 255)
    result["system"]["load"] = [
        number(v, "load", 10**9, nullable=False)
        for v in array(system.get("load", []), "load", 3)
    ]
    disks = []
    for disk in array(system.get("disks", []), "disks", 256):
        if not isinstance(disk, dict) or not disk:
            raise ValueError("无效的 disk")
        disks.append(
            {
                "mount": text(disk.get("mount"), "mount", 1024),
                **{
                    key: number(
                        disk.get(key),
                        key,
                        2**60 if key != "percent" else 100,
                        nullable=False,
                    )
                    for key in (
                        "total_bytes",
                        "used_bytes",
                        "available_bytes",
                        "percent",
                    )
                },
            }
        )
    result["system"]["disks"] = disks
    indexes = set()
    process_count = 0
    for gpu in array(report.get("gpus"), "gpus", 128):
        if (
            not isinstance(gpu, dict)
            or type(gpu.get("index")) is not int
            or not 0 <= gpu["index"] < 1024
            or gpu["index"] in indexes
        ):
            raise ValueError("GPU index 无效或重复")
        indexes.add(gpu["index"])
        item = {
            "index": gpu["index"],
            "name": text(gpu.get("name"), "GPU name", 255),
            "uuid": text(gpu.get("uuid"), "GPU uuid", 128, nullable=True),
            "driver": text(gpu.get("driver"), "driver", 128, nullable=True),
            **{
                key: number(gpu.get(key), key, maximum)
                for key, maximum in GPU_NUMBERS.items()
            },
            "processes": [],
        }
        for process in array(gpu.get("processes"), "processes", 4096):
            process_count += 1
            if not isinstance(process, dict) or process_count > 4096:
                raise ValueError("进程数据无效或超过上限")
            pid = process.get("pid")
            if type(pid) is not int or not 1 <= pid <= 2**31:
                raise ValueError("无效的进程 PID")
            item["processes"].append(
                {
                    "pid": pid,
                    "name": text(process.get("name"), "process name", 2048),
                    "user": text(
                        process.get("user"), "process user", 255, nullable=True
                    ),
                    "type": text(process.get("type", "—"), "process type", 32),
                    "memory_mb": number(
                        process.get("memory_mb"), "process memory", 10**9
                    ),
                }
            )
        result["gpus"].append(item)
    return result
