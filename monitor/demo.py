"""Explicitly synthetic data. Demo mode never reads inventory or connects to SSH."""

import math
import time

from .models import Host
from .service import classify

SPECS = [
    ("demo-p100-01", "192.0.2.11", "Tesla P100-PCIE-16GB", 8, 16384),
    ("demo-v100-02", "192.0.2.12", "Tesla V100-SXM2-32GB", 4, 32768),
    ("demo-v100-03", "192.0.2.13", "Tesla V100-SXM2-32GB", 7, 32768),
    ("demo-v100s-04", "192.0.2.14", "Tesla V100S-PCIE-32GB", 4, 32768),
    ("demo-titan-05", "192.0.2.15", "NVIDIA TITAN Xp", 7, 12288),
    ("demo-titan-06", "192.0.2.16", "NVIDIA TITAN Xp", 5, 12288),
    ("demo-2080ti-07", "192.0.2.17", "GeForce RTX 2080 Ti", 6, 11264),
    ("demo-offline-08", "192.0.2.18", "NVIDIA TITAN Xp", 4, 12288),
]
USERS = ["chen.yu", "wang.jie", "li.ming", "zhang.rui", "sun.yi"]


def demo_hosts():
    return [Host(name, address, groups=("demo",)) for name, address, *_rest in SPECS]


def demo_snapshot(host, ts):
    index = next(i for i, spec in enumerate(SPECS) if spec[0] == host.name)
    _, _, model, count, capacity = SPECS[index]
    wave = ts / 2700 + index * 0.8
    snapshot = {
        **host.public(),
        "checked_at": ts,
        "collection_ms": 320 + index * 81,
        "system": {},
        "gpus": [],
        "warnings": [],
        "error": None,
        "last_seen": ts,
        "known_gpu_count": count,
    }
    if index == 7:
        snapshot.update(
            status="offline",
            last_seen=None,
            error="演示：超过 100 秒未收到节点采集脚本上报，请检查脚本服务及网络连接。",
        )
        return snapshot
    cpu = round(
        max(
            3, min(99, 43 + 18 * math.sin(ts / 1600) + 14 * math.sin(wave) + index * 2)
        ),
        1,
    )
    memory_percent = round(42 + index * 4 + 8 * math.sin(wave / 2), 1)
    total = (256 if index < 4 else 128) * 1024**3
    disk_percent = 92 if index == 5 else 40 + index * 6
    snapshot["system"] = {
        "hostname": host.name,
        "os": "Ubuntu 22.04.5 LTS",
        "kernel": "5.15.0-130-generic",
        "cpu_count": 64 if index < 4 else 32,
        "cpu_percent": cpu,
        "memory_total_bytes": total,
        "memory_used_bytes": total * memory_percent / 100,
        "memory_percent": memory_percent,
        "swap_total_bytes": 8 * 1024**3,
        "swap_used_bytes": 0,
        "swap_percent": 0,
        "uptime_seconds": (18 + index * 12) * 86400 + ts % 86400,
        "load": [round(cpu * 0.35, 2), 16.21, 14.87],
        "network_rx_bytes_s": int((12 + 8 * math.sin(wave)) * 1024**2),
        "network_tx_bytes_s": int((4 + 3 * math.sin(wave)) * 1024**2),
        "disk_read_bytes_s": 46 * 1024**2,
        "disk_write_bytes_s": 18 * 1024**2,
        "disks": [
            {
                "mount": "/",
                "total_bytes": 512 * 1024**3,
                "used_bytes": 512 * 1024**3 * 0.43,
                "available_bytes": 512 * 1024**3 * 0.57,
                "percent": 43,
            },
            {
                "mount": "/data",
                "total_bytes": 4 * 1024**4,
                "used_bytes": 4 * 1024**4 * disk_percent / 100,
                "available_bytes": 4 * 1024**4 * (100 - disk_percent) / 100,
                "percent": disk_percent,
            },
        ],
    }
    for gpu_index in range(count):
        idle = (index + gpu_index) % 5 == 0
        utilization = (
            0
            if idle
            else round(
                max(
                    4,
                    min(
                        100,
                        62
                        + 20 * math.sin(ts / 1200)
                        + 12 * math.sin(wave + gpu_index / 3),
                    ),
                ),
                1,
            )
        )
        used = (
            12
            if idle
            else round(capacity * (0.5 + 0.3 * math.sin(wave / 3 + gpu_index) ** 2))
        )
        processes = (
            []
            if idle
            else [
                {
                    "pid": 18200 + index * 100 + gpu_index,
                    "user": USERS[(index * 2 + gpu_index) % len(USERS)],
                    "name": "/opt/conda/envs/research/bin/python",
                    "type": "C",
                    "memory_mb": used - 128,
                }
            ]
        )
        snapshot["gpus"].append(
            {
                "index": gpu_index,
                "uuid": f"GPU-demo-{index}-{gpu_index}",
                "name": model,
                "driver": "535.183.01",
                "utilization": utilization,
                "memory_used_mb": used,
                "memory_total_mb": capacity,
                "memory_percent": round(used / capacity * 100, 1),
                "temperature": 88
                if index == 5 and gpu_index == 1
                else round(36 + utilization * 0.4),
                "power_w": round(35 + utilization * 2.2),
                "power_limit_w": 250,
                "fan_percent": 30 if idle else 65,
                "processes": processes,
            }
        )
    return classify(snapshot)


def seed_demo(store, hosts):
    if store.latest():
        return
    now = time.time()
    # One day of history at the actual configured sample interval.
    store.record_many(
        demo_snapshot(host, ts)
        for ts in range(int(now - 86400), int(now), store.interval)
        for host in hosts
    )
