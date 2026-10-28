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
from decimal import Decimal, InvalidOperation
from http.client import HTTPConnection, HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPHandler,
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


GPU_CONFIG_ERROR = "GPU 采集配置无效"
DEFAULT_DCGM_URL = "http://127.0.0.1:9400/metrics"
DCGM_MAX_BYTES = 2 * 1024 * 1024


def validate_gpu_config(value):
    """Normalize the per-reporter opt-in; errors never include supplied values."""
    if not isinstance(value, dict):
        raise ValueError(GPU_CONFIG_ERROR)  # noqa: TRY004 -- shared config contract
    provider = value.get("provider", "nvidia-smi")
    if provider == "nvidia-smi" and not set(value) - {"provider"}:
        return {"provider": "nvidia-smi"}
    if provider != "dcgm" or set(value) - {"provider", "url", "timeout", "processes"}:
        raise ValueError(GPU_CONFIG_ERROR)
    endpoint = value.get("url", DEFAULT_DCGM_URL)
    timeout = value.get("timeout", 3)
    processes = value.get("processes", "nvidia-smi")
    if (
        not isinstance(endpoint, str)
        or type(timeout) is not int
        or not 1 <= timeout <= 8
        or processes not in ("nvidia-smi", "none")
    ):
        raise ValueError(GPU_CONFIG_ERROR)
    try:
        url = urlsplit(endpoint)
        port = url.port  # Access can itself fail for malformed/overflowing ports.
        valid = (
            url.scheme == "http"
            and url.hostname in ("127.0.0.1", "::1")
            and url.username is None
            and url.password is None
            and url.path == "/metrics"
            and "?" not in endpoint
            and "#" not in endpoint
            and not any(
                c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in endpoint
            )
            and (port is None or 1 <= port <= 65535)
            # urlsplit accepts suffixes after a closing IPv6 bracket. An exact
            # authority also rules out encoded aliases, zones and empty ports.
            and re.fullmatch(r"(?:127\.0\.0\.1|\[::1\])(?::[0-9]+)?", url.netloc)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(GPU_CONFIG_ERROR)
    return {
        "provider": "dcgm",
        "url": endpoint,
        "timeout": timeout,
        "processes": processes,
    }


def _deadline_left(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OSError("DCGM request deadline exceeded")
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Set the remaining whole-request budget before *every* raw socket read."""

    def __init__(self, stream, sock, deadline):
        self.stream, self.sock, self.deadline = stream, sock, deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(_deadline_left(self.deadline))
        size = self.stream.readinto(buffer)
        _deadline_left(self.deadline)
        return size

    def close(self):
        try:
            self.stream.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def sendall(self, data):
        self.sock.settimeout(_deadline_left(self.deadline))
        return self.sock.sendall(data)

    def makefile(self, mode):
        # Use an unbuffered SocketIO: a buffered read could do many receives
        # with the same timeout. This also bounds headers and chunk framing.
        raw = self.sock.makefile(mode, buffering=0)
        return io.BufferedReader(_DeadlineReader(raw, self.sock, self.deadline))


class _DeadlineHTTPConnection(HTTPConnection):
    def __init__(self, host, deadline, **kwargs):
        self.deadline = deadline
        super().__init__(host, **kwargs)

    def connect(self):
        self.timeout = min(self.timeout, _deadline_left(self.deadline))
        super().connect()
        self.sock = _DeadlineSocket(self.sock, self.deadline)


class _DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, deadline):
        self.deadline = deadline
        super().__init__()

    def http_open(self, request):
        def connection(host, **kwargs):
            return _DeadlineHTTPConnection(host, self.deadline, **kwargs)

        return self.do_open(connection, request)


def read_dcgm_metrics(config):
    """Read one bounded UTF-8 scrape from an already running local exporter."""
    config = validate_gpu_config(config)
    if config["provider"] != "dcgm":
        raise ValueError(GPU_CONFIG_ERROR)
    deadline = time.monotonic() + config["timeout"]
    try:
        opener = build_opener(
            ProxyHandler({}), NoRedirect(), _DeadlineHTTPHandler(deadline)
        )
        request = Request(
            config["url"],
            headers={"Accept": "text/plain", "Accept-Encoding": "identity"},
            method="GET",
        )
        with opener.open(request, timeout=config["timeout"]) as response:
            if (
                response.status != 200
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("DCGM response invalid")
            length = response.headers.get("Content-Length")
            if length is not None and (
                not re.fullmatch(r"[0-9]{1,10}", length) or int(length) > DCGM_MAX_BYTES
            ):
                raise ValueError("DCGM response too large")
            body = bytearray()
            while True:
                _deadline_left(deadline)
                chunk = response.read(min(65536, DCGM_MAX_BYTES + 1 - len(body)))
                _deadline_left(deadline)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > DCGM_MAX_BYTES:
                    raise ValueError("DCGM response too large")
            if length is not None and len(body) != int(length):
                raise ValueError("DCGM response incomplete")
            return body.decode("utf-8")
    except HTTPError as exc:
        # opener.open raised before entering the response context manager.
        exc.close()
        raise ValueError("DCGM metrics unavailable") from None
    except (OSError, ValueError, HTTPException, URLError):
        # Never expose an exporter response body, URL or underlying exception.
        raise ValueError("DCGM metrics unavailable") from None


# metric -> (output field, maximum, integer, multiplier). FB_FREE/RESERVED are
# internal inputs, never guesses: deriving a total requires all three parts.
_DCGM_BASIC = {
    "DCGM_FI_DEV_GPU_UTIL": ("utilization", 100, False, 1),
    "DCGM_FI_DEV_FB_USED": ("memory_used_mb", 10**9, False, 1),
    "DCGM_FI_DEV_FB_TOTAL": ("memory_total_mb", 10**9, False, 1),
    "DCGM_FI_DEV_FB_FREE": ("free_mb", 10**9, False, 1),
    "DCGM_FI_DEV_FB_RESERVED": ("reserved_mb", 10**9, False, 1),
    "DCGM_FI_DEV_GPU_TEMP": ("temperature", 200, False, 1),
    "DCGM_FI_DEV_POWER_USAGE": ("power_w", 100000, False, 1),
    "DCGM_FI_DEV_POWER_MGMT_LIMIT": ("power_limit_w", 100000, False, 1),
    "DCGM_FI_DEV_FAN_SPEED": ("fan_percent", 100, False, 1),
}
_DCGM_OPTIONAL = {
    "DCGM_FI_PROF_SM_ACTIVE": ("sm_active_percent", 1, False, 100),
    "DCGM_FI_PROF_SM_OCCUPANCY": ("sm_occupancy_percent", 1, False, 100),
    "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE": ("tensor_active_percent", 1, False, 100),
    "DCGM_FI_PROF_DRAM_ACTIVE": ("dram_active_percent", 1, False, 100),
    "DCGM_FI_PROF_PCIE_RX_BYTES": ("pcie_rx_bytes_s", 10**16, False, 1),
    "DCGM_FI_PROF_PCIE_TX_BYTES": ("pcie_tx_bytes_s", 10**16, False, 1),
    "DCGM_FI_PROF_NVLINK_RX_BYTES": ("nvlink_rx_bytes_s", 10**16, False, 1),
    "DCGM_FI_PROF_NVLINK_TX_BYTES": ("nvlink_tx_bytes_s", 10**16, False, 1),
    "DCGM_FI_DEV_MEMORY_TEMP": ("memory_temperature", 200, False, 1),
    "DCGM_FI_DEV_SM_CLOCK": ("sm_clock_mhz", 10**6, False, 1),
    "DCGM_FI_DEV_MEM_CLOCK": ("memory_clock_mhz", 10**6, False, 1),
    "DCGM_FI_DEV_ECC_SBE_VOL_TOTAL": ("ecc_sbe_volatile", 2**53 - 1, True, 1),
    "DCGM_FI_DEV_ECC_DBE_VOL_TOTAL": ("ecc_dbe_volatile", 2**53 - 1, True, 1),
    "DCGM_FI_DEV_XID_ERRORS": ("xid_last", 2**31 - 1, True, 1),
}
_DCGM_METRICS = dict(_DCGM_BASIC, **_DCGM_OPTIONAL)
_DCGM_ROW = re.compile(
    r"([A-Za-z_:][A-Za-z0-9_:]*)(?:\{(.*)\})?[ \t]+"
    r"([+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|Inf)|NaN)"
    r"(?:[ \t]+([+-]?[0-9]+))?[ \t]*"
)
_DCGM_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DCGM_INVALID = "DCGM metrics invalid"


def _dcgm_labels(text):
    labels, position = {}, 0
    while position < len(text):
        while position < len(text) and text[position] in " \t":
            position += 1
        match = _DCGM_LABEL.match(text, position)
        if not match or len(labels) >= 64:
            raise ValueError(_DCGM_INVALID)
        key = match.group()
        position = match.end()
        while position < len(text) and text[position] in " \t":
            position += 1
        if text[position : position + 1] != "=":
            raise ValueError(_DCGM_INVALID)
        position += 1
        while position < len(text) and text[position] in " \t":
            position += 1
        if text[position : position + 1] != '"' or key in labels:
            raise ValueError(_DCGM_INVALID)
        position += 1
        value = []
        while position < len(text) and text[position] != '"':
            char = text[position]
            position += 1
            if char == "\\":
                if position == len(text) or text[position] not in ("\\", '"', "n"):
                    raise ValueError(_DCGM_INVALID)
                char = {"\\": "\\", '"': '"', "n": "\n"}[text[position]]
                position += 1
            elif ord(char) < 32 or ord(char) == 127:
                raise ValueError(_DCGM_INVALID)
            value.append(char)
        if position == len(text):
            raise ValueError(_DCGM_INVALID)
        labels[key] = "".join(value)
        position += 1
        while position < len(text) and text[position] in " \t":
            position += 1
        if position == len(text):
            break
        if text[position] != ",":
            raise ValueError(_DCGM_INVALID)
        position += 1
        # A trailing comma is part of the Prometheus text label grammar.
        if not text[position:].strip(" \t"):
            break
    return labels


def _dcgm_number(value, spec):
    _, maximum, integer, multiplier = spec
    if (
        not value.is_finite()
        or value < 0
        or value > maximum
        or 2147483632 <= value <= 2147483647
        or 9223372036854775792 <= value <= 9223372036854775807
        or 140737488355328 <= value <= 140737488355343
        or (integer and value != value.to_integral_value())
    ):
        return None
    if integer:
        return int(value)
    converted = float(value) * multiplier
    # Do not turn numeric underflow into a measured idle/zero value.
    return None if converted == 0 and value != 0 else converted


def _warn_once(warnings, message):
    if message not in warnings:
        warnings.append(message)


def parse_dcgm_metrics(text, interval=30, now=None):
    """Parse only known physical-GPU metrics; never reuse a prior scrape.

    Optional Prometheus timestamps are milliseconds. Without a timestamp a
    sample establishes scrape observation time ONLY, not freshness of DCGM's
    underlying measurement (an exporter can keep exposing an old value).
    """
    if (
        not isinstance(text, str)
        or len(text) > DCGM_MAX_BYTES
        or len(text.encode("utf-8")) > DCGM_MAX_BYTES
    ):
        raise ValueError(_DCGM_INVALID)
    now = time.time() if now is None else now
    by_uuid, by_index, samples, warnings = {}, {}, {}, []
    rows = 0
    for line in text.split("\n"):
        line = line.strip(" \t\r")
        name = re.match(r"[A-Za-z_:][A-Za-z0-9_:]*", line)
        if not name or name.group() not in _DCGM_METRICS:
            continue
        rows += 1
        match = _DCGM_ROW.fullmatch(line) if len(line) <= 16384 else None
        if not match or rows > 32768 or len(match.group(3)) > 128:
            raise ValueError(_DCGM_INVALID)
        metric, raw_labels, raw_value, raw_timestamp = match.groups()
        labels = _dcgm_labels(raw_labels or "")
        try:
            value = Decimal(raw_value)
        except InvalidOperation:
            raise ValueError(_DCGM_INVALID) from None
        if raw_timestamp is not None and len(raw_timestamp) > 20:
            raise ValueError(_DCGM_INVALID)
        timestamp = int(raw_timestamp) if raw_timestamp is not None else None
        identifier = labels.get("UUID", "")
        if (
            "GPU_I_ID" in labels
            or "GPU_I_PROFILE" in labels
            or identifier.startswith("MIG-")
        ):
            _warn_once(warnings, "DCGM: MIG entities skipped")
            continue
        raw_index, model = labels.get("gpu", ""), labels.get("modelName", "")
        if (
            not re.fullmatch(r"[0-9]{1,4}", raw_index)
            or int(raw_index) > 1023
            or not re.fullmatch(r"GPU-[A-Za-z0-9-]+", identifier)
            or len(identifier) > 128
            or not model
            or len(model) > 255
        ):
            raise ValueError(_DCGM_INVALID)
        index = int(raw_index)
        drivers = [
            labels[k] for k in ("DCGM_FI_DRIVER_VERSION", "driver") if labels.get(k)
        ]
        if any(len(driver) > 128 for driver in drivers) or len(set(drivers)) > 1:
            raise ValueError(_DCGM_INVALID)
        driver = drivers[0] if drivers else None
        if index in by_index and by_index[index] != identifier:
            raise ValueError(_DCGM_INVALID)
        if identifier not in by_uuid:
            if len(by_uuid) >= 128:
                raise ValueError(_DCGM_INVALID)
            by_uuid[identifier] = {
                "index": index,
                "uuid": identifier,
                "name": model,
                "driver": driver,
            }
            by_index[index] = identifier
        gpu = by_uuid[identifier]
        if (
            gpu["index"] != index
            or gpu["name"] != model
            or (gpu["driver"] and driver and gpu["driver"] != driver)
        ):
            raise ValueError(_DCGM_INVALID)
        if driver:
            gpu["driver"] = driver
        key = (identifier, metric)
        sample = ("NaN" if value.is_nan() else value, timestamp)
        if key in samples and samples[key] != sample:
            raise ValueError(_DCGM_INVALID)
        samples[key] = sample
        converted = _dcgm_number(value, _DCGM_METRICS[metric])
        if converted is None:
            _warn_once(
                warnings, "DCGM: invalid or unsupported metric values are unknown"
            )
        if timestamp is not None and (
            now - timestamp / 1000 > max(60, 2 * interval)
            or timestamp / 1000 - now > 60
        ):
            converted = None
            _warn_once(warnings, "DCGM: stale or future metric timestamps are unknown")
        gpu[_DCGM_METRICS[metric][0]] = converted
    result = []
    for gpu in sorted(by_uuid.values(), key=lambda item: item["index"]):
        used, total = gpu.get("memory_used_mb"), gpu.get("memory_total_mb")
        parts = [used, gpu.get("free_mb"), gpu.get("reserved_mb")]
        if total is None and all(part is not None for part in parts):
            total = sum(parts)
            if total > 10**9:
                total = None
                _warn_once(
                    warnings, "DCGM: invalid or unsupported metric values are unknown"
                )
        gpu["memory_total_mb"] = total
        gpu["memory_percent"] = (
            round(used / total * 100, 2)
            if used is not None and total and used <= total
            else None
        )
        for field, _, _, _ in _DCGM_BASIC.values():
            gpu.setdefault(field, None)
        gpu.pop("free_mb")
        gpu.pop("reserved_mb")
        gpu["dcgm"] = {
            spec[0]: gpu.pop(spec[0], None) for spec in _DCGM_OPTIONAL.values()
        }
        gpu["processes"], gpu["processes_available"] = [], False
        result.append(gpu)
    if not result:
        _warn_once(warnings, "DCGM: no physical GPU metrics")
    return result, warnings


def _dcgm_processes(gpus, warnings):
    """Supplement by UUID only; no nvidia-smi base metric is ever copied."""
    try:
        supplementary, errors = gpu_data()
        if errors or len(supplementary) > 128:
            raise ValueError("processes unavailable")
        matches, total = {}, 0
        identifiers = {gpu["uuid"] for gpu in gpus}
        for source in supplementary:
            identifier = source.get("uuid")
            if identifier not in identifiers:
                continue
            if identifier in matches:
                raise ValueError("process UUID ambiguous")
            rows = source["processes"]
            if not isinstance(rows, list):
                raise TypeError("processes invalid")
            total += len(rows)
            if total > 4096:
                raise ValueError("process limit exceeded")
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or type(row.get("pid")) is not int
                    or not 1 <= row["pid"] <= 2**31
                    or not isinstance(row.get("name"), str)
                    or len(row["name"]) > 2048
                    or (
                        row.get("user") is not None
                        and (not isinstance(row["user"], str) or len(row["user"]) > 255)
                    )
                    or not isinstance(row.get("type", "C"), str)
                    or len(row.get("type", "C")) > 32
                ):
                    raise ValueError("processes invalid")
                memory_mb = row.get("memory_mb")
                if memory_mb is not None and (
                    type(memory_mb) not in (int, float) or not 0 <= memory_mb <= 10**9
                ):
                    raise ValueError("processes invalid")
            matches[identifier] = rows
        # Only attach once all supplementary rows passed bounds/identity checks.
        for gpu in gpus:
            if gpu["uuid"] in matches:
                gpu["processes"] = matches[gpu["uuid"]]
                gpu["processes_available"] = True
        if any(not gpu["processes_available"] for gpu in gpus):
            _warn_once(warnings, "DCGM: process information is unknown (no UUID match)")
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        OverflowError,
        subprocess.SubprocessError,
    ):
        _warn_once(
            warnings, "DCGM: process information is unknown (nvidia-smi unavailable)"
        )


def dcgm_gpu_data(config, interval=30):
    config = validate_gpu_config(config)
    if config["provider"] != "dcgm":
        raise ValueError(GPU_CONFIG_ERROR)
    try:
        gpus, warnings = parse_dcgm_metrics(
            read_dcgm_metrics(config), interval=interval
        )
    except (OSError, ValueError, HTTPException):
        return [], ["DCGM GPU metrics unavailable"]
    # Deliberately disabled process collection is represented by the explicit
    # availability flag/UI, not a permanent node-health warning.
    if config["processes"] == "nvidia-smi" and gpus:
        _dcgm_processes(gpus, warnings)
    return gpus, warnings


def collect(gpu=None, interval=30):
    gpu = validate_gpu_config({} if gpu is None else gpu)
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
    gpus, warnings = (
        dcgm_gpu_data(gpu, interval=interval)
        if gpu["provider"] == "dcgm"
        else gpu_data()
    )
    return {
        "system": system,
        "gpus": gpus,
        "warnings": warnings,
        "gpu_provider": gpu["provider"],
    }


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
    config["gpu"] = validate_gpu_config(config.get("gpu", {}))
    return config


def make_report(config):
    gpu = validate_gpu_config(config.get("gpu", {}))
    started = time.monotonic()
    try:
        data = collect(gpu, interval=config["interval"])
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
        gpu_provider=gpu["provider"],
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
