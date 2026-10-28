"""Standalone, read-only Node Reporter; Python 3.6+, standard library only.

With --config: collect once and POST to the central service. A systemd timer
repeats execution. Without arguments: print a local snapshot for diagnostics.
No sudo, command-line inspection, incoming port, or SSH connection is needed.
"""
# ruff: noqa: UP021, UP022 -- subprocess text/capture_output require Python 3.7.

import argparse
import csv
import io
import json
import os
import pwd
import re
import shutil
import ssl
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as stream:
        return stream.read()


def number(text):
    if not text:
        return None
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)", str(text))
    return float(match.group(1)) if match else None


def cpu_ticks():
    # guest / guest_nice are already included in user / nice.
    values = [int(v) for v in read_text("/proc/stat").splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def counters():
    rx, tx, reads, writes = 0, 0, 0, 0
    for line in read_text("/proc/net/dev").splitlines()[2:]:
        name, values = line.split(":", 1)
        if name.strip().startswith(("lo", "veth", "docker", "br-", "virbr")):
            continue
        fields = values.split()
        rx += int(fields[0])
        tx += int(fields[8])
    devices = {
        n for n in os.listdir("/sys/block") if not n.startswith(("loop", "ram", "dm-"))
    }
    for line in read_text("/proc/diskstats").splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[2] in devices:
            reads += int(fields[5]) * 512
            writes += int(fields[9]) * 512
    return rx, tx, reads, writes


def memory():
    values = {}
    for line in read_text("/proc/meminfo").splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0]) * 1024
    total = values["MemTotal"]
    available = values.get(
        "MemAvailable", sum(values.get(k, 0) for k in ("MemFree", "Buffers", "Cached"))
    )
    used = max(0, total - available)
    swap_total = values.get("SwapTotal", 0)
    swap_used = max(0, swap_total - values.get("SwapFree", 0))
    return {
        "memory_total_bytes": total,
        "memory_used_bytes": used,
        "memory_percent": round(used / total * 100, 2),
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_used,
        "swap_percent": round(swap_used / swap_total * 100, 2) if swap_total else 0,
    }


def disks():
    result, devices = [], set()
    mounts = []
    for row in csv.reader(io.StringIO(read_text("/proc/mounts")), delimiter=" "):
        if len(row) < 3:
            continue
        device, mount, kind = row[:3]
        if mount != "/" and kind not in (
            "ext2",
            "ext3",
            "ext4",
            "xfs",
            "btrfs",
            "zfs",
            "nfs",
            "nfs4",
            "lustre",
        ):
            continue
        mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), mount)
        mounts.append((device, mount))
    for device, mount in sorted(mounts, key=lambda item: len(item[1])):
        if device in devices:
            continue
        try:
            info = os.statvfs(mount)
            total = info.f_blocks * info.f_frsize
            used = (info.f_blocks - info.f_bfree) * info.f_frsize
            available = max(0, info.f_bavail * info.f_frsize)
            if total:
                result.append(
                    {
                        "mount": mount,
                        "total_bytes": total,
                        "used_bytes": used,
                        "available_bytes": available,
                        "percent": round(used / (used + available) * 100, 2)
                        if used + available
                        else 0,
                    }
                )
                devices.add(device)
        except OSError:
            continue
    return result


def gpu_data():
    if not shutil.which("nvidia-smi"):
        return [], []
    try:
        # capture_output / text were added in Python 3.7; some nodes still run 3.6.
        result = subprocess.run(
            ["nvidia-smi", "-q", "-x"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
        if result.returncode:
            return [], ["nvidia-smi 采集失败：" + result.stderr.strip()[:240]]
        root = ET.fromstring(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ET.ParseError) as exc:
        return [], ["GPU 信息读取失败：" + str(exc)[:240]]
    gpus = []
    for index, gpu in enumerate(root.findall("gpu")):

        def value(path, gpu=gpu):
            return number(gpu.findtext(path))

        memory_used = value("fb_memory_usage/used")
        memory_total = value("fb_memory_usage/total")
        power = value("gpu_power_readings/power_draw")
        if power is None:
            power = value("power_readings/power_draw")
        limit = value("gpu_power_readings/power_limit")
        if limit is None:
            limit = value("power_readings/power_limit")
        processes = []
        for process in gpu.findall("processes/process_info"):
            raw_pid = number(process.findtext("pid"))
            if not raw_pid or raw_pid < 0:
                continue
            pid = int(raw_pid)
            try:
                user = pwd.getpwuid(os.stat("/proc/" + str(pid)).st_uid).pw_name
            except (OSError, KeyError):
                user = None
            processes.append(
                {
                    "pid": pid,
                    "user": user,
                    "name": process.findtext("process_name") or "未知进程",
                    "type": process.findtext("type", "C"),
                    "memory_mb": number(process.findtext("used_memory")),
                }
            )
        gpus.append(
            {
                "index": int(
                    value("minor_number")
                    if value("minor_number") is not None
                    else index
                ),
                "uuid": gpu.findtext("uuid"),
                "name": gpu.findtext("product_name", "NVIDIA GPU"),
                "driver": root.findtext("driver_version"),
                "utilization": value("utilization/gpu_util"),
                "memory_used_mb": memory_used,
                "memory_total_mb": memory_total,
                "memory_percent": round(memory_used / memory_total * 100, 2)
                if memory_used is not None and memory_total
                else None,
                "temperature": value("temperature/gpu_temp"),
                "power_w": power,
                "power_limit_w": limit,
                "fan_percent": value("fan_speed"),
                "processes": processes,
            }
        )
    return gpus, []


def collect():
    first_cpu = cpu_ticks()
    first_io = counters()
    started = time.monotonic()
    time.sleep(0.25)
    last_cpu = cpu_ticks()
    last_io = counters()
    elapsed = time.monotonic() - started
    delta = last_cpu[0] - first_cpu[0]
    cpu = (1 - (last_cpu[1] - first_cpu[1]) / delta) * 100 if delta > 0 else None
    rates = [max(0, (b - a) / elapsed) for a, b in zip(first_io, last_io)]
    os_name = os.uname().sysname
    try:
        for line in read_text("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    system = {
        **memory(),
        "hostname": os.uname().nodename,
        "os": os_name,
        "kernel": os.uname().release,
        "cpu_count": os.cpu_count(),
        "cpu_percent": round(max(0, min(100, cpu)), 2) if cpu is not None else None,
        "uptime_seconds": float(read_text("/proc/uptime").split()[0]),
        "load": list(os.getloadavg()),
        "disks": disks(),
        "network_rx_bytes_s": round(rates[0]),
        "network_tx_bytes_s": round(rates[1]),
        "disk_read_bytes_s": round(rates[2]),
        "disk_write_bytes_s": round(rates[3]),
    }
    gpus, warnings = gpu_data()
    return {"system": system, "gpus": gpus, "warnings": warnings}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a node credential to a redirect target.
        return None


def load_reporter_config(path):
    config = json.loads(read_text(path))
    if (
        not isinstance(config, dict)
        or type(config.get("version")) is not int
        or config["version"] != 1
    ):
        raise ValueError("节点上报配置必须为 version=1 的 JSON 对象")
    host, token = config.get("host"), config.get("token")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", host):
        raise ValueError("节点上报 host 无效，必须与中心配置中的节点名称一致")
    if (
        not isinstance(token, str)
        or not 16 <= len(token) <= 256
        or any(ord(c) > 127 for c in token)
        or not token.isprintable()
        or any(c.isspace() for c in token)
    ):
        raise ValueError("节点上报 token 必须为至少 16 位无空白的可打印 ASCII 字符")
    endpoint = config.get("url")
    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError("节点上报 url 无效")
    url = urlsplit(endpoint)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("/api/node/report", "/api/agent/report")
    ):
        raise ValueError(
            "节点上报 url 必须是无凭据的 http(s) /api/node/report 上报接口（兼容 /api/agent/report），不允许查询参数或重定向"
        )
    for key, default, minimum, maximum in (
        ("interval", 30, 5, 3600),
        ("timeout", 8, 1, 30),
    ):
        value = config.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("节点上报 " + key + " 超出范围")
        config[key] = value
    if config.get("ca_file") is not None and not isinstance(config["ca_file"], str):
        raise ValueError("节点上报 ca_file 必须为证书路径")
    return config


def make_report(config):
    started = time.monotonic()
    try:
        data = collect()
        data["error"] = None
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        subprocess.SubprocessError,
    ) as exc:
        # Report probe failure if possible. A hung probe is bounded by systemd's timeout.
        data = {"system": {}, "gpus": [], "warnings": [], "error": str(exc)[:600]}
    return dict(
        data,
        version=1,
        host=config["host"],
        report_id=str(uuid.uuid4()),
        collected_at=time.time(),
        interval=config["interval"],
        collection_ms=round((time.monotonic() - started) * 1000),
    )


def send_report(config, report):
    context = ssl.create_default_context(cafile=config.get("ca_file"))
    # Management traffic must not accidentally follow HTTP(S)_PROXY from a user's shell.
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
    request = Request(
        config["url"],
        data=json.dumps(report, ensure_ascii=True, allow_nan=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + config["token"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with opener.open(request, timeout=config["timeout"]) as response:
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096:
            raise ValueError("中心返回异常响应")
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict) or not (
            result.get("accepted") is True or result.get("duplicate") is True
        ):
            raise ValueError("中心未确认接收上报")
        return result


def main():
    parser = argparse.ArgumentParser(
        description="ClusterLens Node Reporter（节点上报程序）"
    )
    parser.add_argument("--config", help="节点专属 JSON 配置；不指定时只打印本机快照")
    args = parser.parse_args()
    if not args.config:
        print(json.dumps(collect(), ensure_ascii=True, allow_nan=False))
        return 0
    try:
        config = load_reporter_config(args.config)
        send_report(config, make_report(config))
    except HTTPError as exc:
        # Do not log request headers, credentials, or untrusted response bodies.
        print(
            f"上报失败：HTTP {exc.code}；请检查节点令牌、采样时间及中心配置",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, URLError) as exc:
        print(f"上报失败：{str(exc)[:400]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
