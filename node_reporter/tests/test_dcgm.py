"""Read-only DCGM tests: mock GPU commands, use only local HTTP fixtures."""

import importlib.util
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

REPORTER = Path(__file__).resolve().parents[1] / "python/reporter.py"
spec = importlib.util.spec_from_file_location("dcgm_reporter_test", REPORTER)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

ERROR = "GPU 采集配置无效"
BASE_LABELS = 'gpu="0",UUID="GPU-one",modelName="Fixture GPU"'
BASIC_FIELDS = (
    "utilization",
    "memory_used_mb",
    "memory_total_mb",
    "memory_percent",
    "temperature",
    "power_w",
    "power_limit_w",
    "fan_percent",
)
OPTIONAL_FIELDS = {
    "sm_active_percent",
    "sm_occupancy_percent",
    "tensor_active_percent",
    "dram_active_percent",
    "pcie_rx_bytes_s",
    "pcie_tx_bytes_s",
    "nvlink_rx_bytes_s",
    "nvlink_tx_bytes_s",
    "memory_temperature",
    "sm_clock_mhz",
    "memory_clock_mhz",
    "ecc_sbe_volatile",
    "ecc_dbe_volatile",
    "xid_last",
}


def metric(name, value, labels=BASE_LABELS, timestamp=None):
    return "{}{{{}}} {}{}\n".format(
        name, labels, value, "" if timestamp is None else " " + str(timestamp)
    )


def process(pid):
    return {
        "pid": pid,
        "user": "fixture",
        "name": "python",
        "type": "C",
        "memory_mb": 12,
    }


@contextmanager
def system_probes():
    """Run real collect selection, without machine-specific probes or sleeping."""
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(agent, "cpu_ticks", side_effect=[(100, 10), (120, 15)])
        )
        stack.enter_context(
            patch.object(agent, "counters", side_effect=[(0, 0, 0, 0), (1, 2, 3, 4)])
        )
        stack.enter_context(
            patch.object(agent, "memory", return_value={"memory_percent": 20})
        )
        stack.enter_context(patch.object(agent, "disks", return_value=[]))
        stack.enter_context(patch.object(agent, "read_text", return_value="100 0"))
        stack.enter_context(
            patch.object(agent.os, "getloadavg", return_value=(0, 0, 0))
        )
        stack.enter_context(patch.object(agent.time, "sleep"))
        yield


class NoCommandsTest(unittest.TestCase):
    def setUp(self):
        # No test may accidentally execute a GPU (or other external) command.
        self.command_patch = patch.object(
            agent.subprocess, "run", side_effect=AssertionError("unexpected command")
        )
        self.command_patch.start()
        self.addCleanup(self.command_patch.stop)


class GPUConfigTests(NoCommandsTest):
    def test_defaults_normalize_without_mutation(self):
        for value in ({}, {"provider": "nvidia-smi"}):
            before = dict(value)
            self.assertEqual(
                agent.validate_gpu_config(value), {"provider": "nvidia-smi"}
            )
            self.assertEqual(value, before)
        value = {"provider": "dcgm"}
        expected = {
            "provider": "dcgm",
            "url": "http://127.0.0.1:9400/metrics",
            "timeout": 3,
            "processes": "nvidia-smi",
        }
        self.assertEqual(agent.validate_gpu_config(value), expected)
        self.assertEqual(value, {"provider": "dcgm"})
        for timeout in (1, 8):
            for processes in ("none", "nvidia-smi"):
                self.assertEqual(
                    agent.validate_gpu_config(
                        {**expected, "timeout": timeout, "processes": processes}
                    ),
                    {**expected, "timeout": timeout, "processes": processes},
                )

    def test_invalid_options_have_one_value_free_error(self):
        invalid = [
            None,
            True,
            1,
            "dcgm",
            [],
            {"provider": None},
            {"provider": False},
            {"provider": "private-token"},
            {"url": agent.DEFAULT_DCGM_URL},
            {"provider": "dcgm", "private-token": "private-value"},
        ]
        for key, default in (
            ("url", agent.DEFAULT_DCGM_URL),
            ("timeout", 3),
            ("processes", "nvidia-smi"),
        ):
            invalid.append({"provider": "nvidia-smi", key: default})
        for timeout in (None, True, False, 0, -1, 9, 1.5, "3", [], {}):
            invalid.append({"provider": "dcgm", "timeout": timeout})
        for processes in (None, True, "", "auto", "private-token", [], {}):
            invalid.append({"provider": "dcgm", "processes": processes})
        for url in (None, True, 123, [], {}):
            invalid.append({"provider": "dcgm", "url": url})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                agent.validate_gpu_config(value)
            self.assertEqual(str(caught.exception), ERROR)

    def test_only_literal_loopback_http_metrics_urls(self):
        for url in (
            "http://127.0.0.1/metrics",
            "http://127.0.0.1:1/metrics",
            "http://127.0.0.1:65535/metrics",
            "http://[::1]/metrics",
            "http://[::1]:9400/metrics",
            "HTTP://127.0.0.1:80/metrics",
            "http://127.0.0.1:00080/metrics",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    agent.validate_gpu_config({"provider": "dcgm", "url": url})["url"],
                    url,
                )
        invalid = (
            "https://127.0.0.1:9400/metrics",
            "ftp://127.0.0.1/metrics",
            "file:///metrics",
            "http://localhost:9400/metrics",
            "http://127.1/metrics",
            "http://127.0.0.2/metrics",
            "http://2130706433/metrics",
            "http://0177.0.0.1/metrics",
            "http://0x7f000001/metrics",
            "http://example.org/metrics",
            "http://0.0.0.0/metrics",
            "http://[::ffff:127.0.0.1]/metrics",
            "http://[0:0:0:0:0:0:0:1]/metrics",
            "http://[::1%25lo]/metrics",
            "http://%31%32%37.0.0.1/metrics",
            "http://127.0.0.1/%6Detrics",
            "http://127.0.0.1/metrics/",
            "http://127.0.0.1/other/../metrics",
            "http://127.0.0.1:9400",
            "http://127.0.0.1/metrics?private-token",
            "http://127.0.0.1/metrics?",
            "http://127.0.0.1/metrics#",
            "http://127.0.0.1/metrics#private-token",
            "http://@127.0.0.1/metrics",
            "http://private-token@127.0.0.1/metrics",
            "http://:private-token@127.0.0.1/metrics",
            "http://127.0.0.1:/metrics",
            "http://[::1]:/metrics",
            "http://127.0.0.1:0/metrics",
            "http://127.0.0.1:-1/metrics",
            "http://127.0.0.1:65536/metrics",
            "http://127.0.0.1:private-token/metrics",
            "http://127.0.0.1:1.0/metrics",
            "http://[::1/metrics",
            "http://::1/metrics",
            "http://[::1]extra/metrics",
            "http://[::1]@example.org/metrics",
            "http://127.0.0.1\\@example.org/metrics",
            "http://127.0.0.1/metrics\\",
        )
        invalid += tuple(
            prefix + "http://127.0.0.1/metrics" for prefix in (" ", "\t", "\n", "\x00")
        )
        invalid += tuple(
            "http://127.0.0.1/metrics" + suffix
            for suffix in (" ", "\t", "\r\n", "\x00", "\x7f", "\x85")
        )
        invalid += ("http://127.0.0.\t1/metrics", "http://127.0.0.1/met\nrics")
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                agent.validate_gpu_config({"provider": "dcgm", "url": url})
            self.assertEqual(str(caught.exception), ERROR)

    def test_loader_uses_same_normalization_and_rejects_explicit_null(self):
        base = {
            "version": 1,
            "host": "fixture",
            "token": "private-token-long",
            "url": "https://example.org/api/node/report",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "node.json"
            path.write_text(json.dumps(base))
            self.assertEqual(
                agent.load_reporter_config(path)["gpu"], {"provider": "nvidia-smi"}
            )
            for value in (
                {},
                {"provider": "nvidia-smi"},
                {"provider": "dcgm"},
                {
                    "provider": "dcgm",
                    "processes": "none",
                    "timeout": 8,
                    "url": "http://[::1]/metrics",
                },
            ):
                path.write_text(json.dumps({**base, "gpu": value}))
                self.assertEqual(
                    agent.load_reporter_config(path)["gpu"],
                    agent.validate_gpu_config(value),
                )
            for value in (
                None,
                {"provider": "dcgm", "timeout": True},
                {"provider": "nvidia-smi", "timeout": 3},
            ):
                path.write_text(json.dumps({**base, "gpu": value}))
                with self.assertRaises(ValueError) as caught:
                    agent.load_reporter_config(path)
                self.assertEqual(str(caught.exception), ERROR)


class ParserTests(NoCommandsTest):
    def parse_one(self, text, **kwargs):
        gpus, warnings = agent.parse_dcgm_metrics(text, **kwargs)
        self.assertEqual(len(gpus), 1)
        return gpus[0], warnings

    def test_basic_units_zeros_and_fixed_optional_shape(self):
        values = {
            "GPU_UTIL": 0,
            "FB_USED": 0,
            "FB_TOTAL": 8192,
            "GPU_TEMP": 55,
            "POWER_USAGE": 35.5,
            "POWER_MGMT_LIMIT": 300,
            "FAN_SPEED": 0,
        }
        text = "# HELP ignored\n# TYPE ignored gauge\nunrelated{broken not valid\n"
        text += "".join(
            metric("DCGM_FI_DEV_" + name, value) for name, value in values.items()
        )
        gpu, warnings = self.parse_one(text)
        self.assertFalse(warnings)
        self.assertEqual(gpu["index"], 0)
        self.assertEqual(gpu["uuid"], "GPU-one")
        self.assertEqual(gpu["name"], "Fixture GPU")
        self.assertIsNone(gpu["driver"])
        self.assertEqual(
            [gpu[name] for name in BASIC_FIELDS], [0, 0, 8192, 0, 55, 35.5, 300, 0]
        )
        self.assertEqual(set(gpu["dcgm"]), OPTIONAL_FIELDS)
        self.assertTrue(all(value is None for value in gpu["dcgm"].values()))
        self.assertEqual(gpu["processes"], [])
        self.assertFalse(gpu["processes_available"])
        json.dumps(gpu, allow_nan=False)

    def test_physical_identity_boundaries_and_optional_driver_merge(self):
        labels = 'gpu="1023",UUID="GPU-' + "x" * 124 + '",modelName="' + "x" * 255 + '"'
        text = metric("DCGM_FI_DEV_GPU_UTIL", 0, labels)
        text += metric(
            "DCGM_FI_DEV_GPU_TEMP", 200, labels + ',driver="' + "x" * 128 + '"'
        )
        gpu, warnings = self.parse_one(text)
        self.assertFalse(warnings)
        self.assertEqual(gpu["index"], 1023)
        self.assertEqual(len(gpu["uuid"]), 128)
        self.assertEqual(len(gpu["name"]), 255)
        self.assertEqual(len(gpu["driver"]), 128)
        self.assertEqual(gpu["temperature"], 200)

    def test_all_optional_fields_units_and_integer_counters(self):
        metrics = (
            ("DCGM_FI_PROF_SM_ACTIVE", ".25", "sm_active_percent", 25),
            ("DCGM_FI_PROF_SM_OCCUPANCY", "1", "sm_occupancy_percent", 100),
            ("DCGM_FI_PROF_PIPE_TENSOR_ACTIVE", "0", "tensor_active_percent", 0),
            ("DCGM_FI_PROF_DRAM_ACTIVE", "5e-1", "dram_active_percent", 50),
            ("DCGM_FI_PROF_PCIE_RX_BYTES", "1e16", "pcie_rx_bytes_s", 10**16),
            ("DCGM_FI_PROF_PCIE_TX_BYTES", "123", "pcie_tx_bytes_s", 123),
            ("DCGM_FI_PROF_NVLINK_RX_BYTES", "456", "nvlink_rx_bytes_s", 456),
            ("DCGM_FI_PROF_NVLINK_TX_BYTES", "789", "nvlink_tx_bytes_s", 789),
            ("DCGM_FI_DEV_MEMORY_TEMP", "200", "memory_temperature", 200),
            ("DCGM_FI_DEV_SM_CLOCK", "1500", "sm_clock_mhz", 1500),
            ("DCGM_FI_DEV_MEM_CLOCK", "1000000", "memory_clock_mhz", 10**6),
            (
                "DCGM_FI_DEV_ECC_SBE_VOL_TOTAL",
                str(2**53 - 1),
                "ecc_sbe_volatile",
                2**53 - 1,
            ),
            ("DCGM_FI_DEV_ECC_DBE_VOL_TOTAL", "2e3", "ecc_dbe_volatile", 2000),
            ("DCGM_FI_DEV_XID_ERRORS", "0", "xid_last", 0),
        )
        gpu, warnings = self.parse_one(
            "".join(metric(name, value) for name, value, _, _ in metrics)
        )
        self.assertFalse(warnings)
        self.assertEqual(
            gpu["dcgm"], {field: expected for _, _, field, expected in metrics}
        )
        for field in ("ecc_sbe_volatile", "ecc_dbe_volatile", "xid_last"):
            self.assertIs(type(gpu["dcgm"][field]), int)
        self.assertTrue(all(gpu[field] is None for field in BASIC_FIELDS))

    def test_memory_total_requires_valid_used_free_and_reserved(self):
        used = metric("DCGM_FI_DEV_FB_USED", 50)
        free = metric("DCGM_FI_DEV_FB_FREE", 150)
        for suffix, expected in (
            ("", None),
            (metric("DCGM_FI_DEV_FB_RESERVED", "NaN"), None),
            (metric("DCGM_FI_DEV_FB_RESERVED", 0), 200),
            (metric("DCGM_FI_DEV_FB_RESERVED", 50), 250),
            (metric("DCGM_FI_DEV_FB_TOTAL", 400), 400),
        ):
            with self.subTest(suffix=suffix):
                gpu, _ = self.parse_one(used + free + suffix)
                self.assertEqual(gpu["memory_total_mb"], expected)
                self.assertEqual(
                    gpu["memory_percent"],
                    None if expected is None else round(50 / expected * 100, 2),
                )
        for text in (
            metric("DCGM_FI_DEV_FB_USED", 0) + metric("DCGM_FI_DEV_FB_TOTAL", 0),
            used + metric("DCGM_FI_DEV_FB_TOTAL", 10),
            metric("DCGM_FI_DEV_FB_FREE", 5) + metric("DCGM_FI_DEV_FB_RESERVED", 0),
        ):
            gpu, _ = self.parse_one(text)
            self.assertIsNone(gpu["memory_percent"])
        gpu, warnings = self.parse_one(
            metric("DCGM_FI_DEV_FB_USED", 10**9)
            + metric("DCGM_FI_DEV_FB_FREE", 1)
            + metric("DCGM_FI_DEV_FB_RESERVED", 0)
        )
        self.assertIsNone(gpu["memory_total_mb"])
        self.assertTrue(warnings)

    def test_never_substitutes_engine_or_profile_activity_for_gpu_util(self):
        gpu, _ = self.parse_one(
            metric("DCGM_FI_PROF_GR_ENGINE_ACTIVE", 0.99)
            + metric("DCGM_FI_PROF_SM_ACTIVE", 0.25)
            + metric("DCGM_FI_PROF_DRAM_ACTIVE", 0.5)
        )
        self.assertIsNone(gpu["utilization"])
        self.assertEqual(gpu["dcgm"]["sm_active_percent"], 25)

    def test_sentinels_negative_nonfinite_and_out_of_range_are_null_not_zero(self):
        invalid = (
            "NaN",
            "Inf",
            "+Inf",
            "-Inf",
            "-1",
            "1e309",
            "1e-9999",
            "2147483632",
            "2147483647",
            "9223372036854775792",
            "9223372036854775807",
            "140737488355328",
            "140737488355329",
            "140737488355330",
            "140737488355343",
        )
        for raw in invalid:
            with self.subTest(value=raw):
                gpu, warnings = self.parse_one(
                    metric("DCGM_FI_PROF_PCIE_RX_BYTES", raw)
                )
                self.assertIsNone(gpu["dcgm"]["pcie_rx_bytes_s"])
                self.assertTrue(warnings)
                json.dumps(gpu, allow_nan=False)
        for name, raw, field in (
            ("DCGM_FI_DEV_GPU_UTIL", 101, "utilization"),
            ("DCGM_FI_DEV_FB_USED", 10**9 + 1, "memory_used_mb"),
            ("DCGM_FI_DEV_FB_TOTAL", 10**9 + 1, "memory_total_mb"),
            ("DCGM_FI_DEV_GPU_TEMP", 201, "temperature"),
            ("DCGM_FI_DEV_POWER_USAGE", 100001, "power_w"),
            ("DCGM_FI_DEV_POWER_MGMT_LIMIT", 100001, "power_limit_w"),
            ("DCGM_FI_DEV_FAN_SPEED", 101, "fan_percent"),
            ("DCGM_FI_PROF_SM_ACTIVE", 1.01, "sm_active_percent"),
            ("DCGM_FI_PROF_SM_OCCUPANCY", 100, "sm_occupancy_percent"),
            ("DCGM_FI_PROF_PIPE_TENSOR_ACTIVE", 1.1, "tensor_active_percent"),
            ("DCGM_FI_PROF_DRAM_ACTIVE", 1.1, "dram_active_percent"),
            ("DCGM_FI_PROF_NVLINK_TX_BYTES", 10**16 + 1, "nvlink_tx_bytes_s"),
            ("DCGM_FI_DEV_MEMORY_TEMP", 201, "memory_temperature"),
            ("DCGM_FI_DEV_SM_CLOCK", 1000001, "sm_clock_mhz"),
            ("DCGM_FI_DEV_MEM_CLOCK", 1000001, "memory_clock_mhz"),
            ("DCGM_FI_DEV_ECC_SBE_VOL_TOTAL", 2**53, "ecc_sbe_volatile"),
            ("DCGM_FI_DEV_ECC_DBE_VOL_TOTAL", "1.5", "ecc_dbe_volatile"),
            ("DCGM_FI_DEV_XID_ERRORS", 2**31, "xid_last"),
        ):
            with self.subTest(metric=name):
                gpu, warnings = self.parse_one(metric(name, raw))
                self.assertIsNone(gpu[field] if field in gpu else gpu["dcgm"][field])
                self.assertTrue(warnings)

    def test_optional_millisecond_timestamps_and_interval_threshold(self):
        now = 100000
        for interval, age, valid in (
            (30, 60, True),
            (30, 60.001, False),
            (45, 90, True),
            (45, 90.001, False),
            (30, -60, True),
            (30, -60.001, False),
        ):
            with self.subTest(interval=interval, age=age):
                gpu, warnings = self.parse_one(
                    metric(
                        "DCGM_FI_DEV_GPU_UTIL", 25, timestamp=round((now - age) * 1000)
                    ),
                    now=now,
                    interval=interval,
                )
                self.assertEqual(gpu["utilization"], 25 if valid else None)
                self.assertEqual(bool(warnings), not valid)
        gpu, warnings = self.parse_one(metric("DCGM_FI_DEV_GPU_UTIL", 0), now=now)
        self.assertEqual(gpu["utilization"], 0)
        self.assertFalse(warnings)
        self.assertIn("observation time ONLY", agent.parse_dcgm_metrics.__doc__)
        text = metric("DCGM_FI_DEV_GPU_UTIL", 10, timestamp=(now - 61) * 1000)
        text += metric("DCGM_FI_DEV_GPU_TEMP", 30, timestamp=now * 1000)
        gpu, _ = self.parse_one(text, now=now)
        self.assertIsNone(gpu["utilization"])
        self.assertEqual(gpu["temperature"], 30)

    def test_label_escaping_unknown_labels_and_optional_driver(self):
        labels = 'gpu="0",UUID="GPU-one",modelName="A\\\\B\\"C\\nD",job="one",container="two",driver="555.12",'
        gpu, warnings = self.parse_one(metric("DCGM_FI_DEV_GPU_UTIL", 1, labels))
        self.assertFalse(warnings)
        self.assertEqual(gpu["name"], 'A\\B"C\nD')
        self.assertEqual(gpu["driver"], "555.12")
        gpu, _ = self.parse_one(
            metric(
                "DCGM_FI_DEV_GPU_UTIL",
                1,
                BASE_LABELS + ',DCGM_FI_DRIVER_VERSION="560.1"',
            )
        )
        self.assertEqual(gpu["driver"], "560.1")

    def test_exact_duplicates_ignore_extra_exporter_labels_but_conflicts_fail(self):
        row = metric("DCGM_FI_DEV_GPU_UTIL", 25, BASE_LABELS + ',job="one"')
        gpu, warnings = self.parse_one(
            row
            + metric(
                "DCGM_FI_DEV_GPU_UTIL",
                "25.0",
                BASE_LABELS + ',job="two",container="three"',
            )
        )
        self.assertEqual(gpu["utilization"], 25)
        self.assertFalse(warnings)
        self.parse_one(metric("DCGM_FI_DEV_GPU_UTIL", "NaN") * 2)
        for conflict in (
            metric("DCGM_FI_DEV_GPU_UTIL", 26),
            metric(
                "DCGM_FI_DEV_GPU_TEMP", 30, BASE_LABELS.replace("GPU-one", "GPU-two")
            ),
            metric(
                "DCGM_FI_DEV_GPU_TEMP", 30, BASE_LABELS.replace('gpu="0"', 'gpu="1"')
            ),
            metric(
                "DCGM_FI_DEV_GPU_TEMP",
                30,
                BASE_LABELS.replace("Fixture GPU", "Different GPU"),
            ),
            metric("DCGM_FI_DEV_GPU_UTIL", 25, timestamp=1000),
        ):
            with self.subTest(conflict=conflict), self.assertRaises(ValueError):
                agent.parse_dcgm_metrics(row + conflict)
        with self.assertRaises(ValueError):
            agent.parse_dcgm_metrics(
                metric("DCGM_FI_DEV_GPU_UTIL", 25, BASE_LABELS + ',driver="1"')
                + metric(
                    "DCGM_FI_DEV_GPU_TEMP",
                    30,
                    BASE_LABELS + ',DCGM_FI_DRIVER_VERSION="2"',
                )
            )

    def test_mig_entities_never_merge_into_physical_gpus(self):
        text = metric("DCGM_FI_DEV_GPU_UTIL", 25)
        for labels in (
            BASE_LABELS + ',GPU_I_ID="0"',
            BASE_LABELS + ',GPU_I_PROFILE="1g.10gb"',
            BASE_LABELS.replace("GPU-one", "MIG-GPU-one/1/0"),
        ):
            text += metric("DCGM_FI_DEV_GPU_UTIL", 80, labels)
        gpu, warnings = self.parse_one(text)
        self.assertEqual(gpu["utilization"], 25)
        self.assertTrue(any("MIG" in warning for warning in warnings))
        gpus, warnings = agent.parse_dcgm_metrics(
            metric("DCGM_FI_DEV_GPU_UTIL", 80, 'UUID="MIG-one"')
        )
        self.assertEqual(gpus, [])
        self.assertTrue(warnings)

    def test_malformed_known_rows_and_identities_are_rejected(self):
        invalid = (
            "DCGM_FI_DEV_GPU_UTIL{",
            "DCGM_FI_DEV_GPU_UTIL 1",
            metric("DCGM_FI_DEV_GPU_UTIL", "private-body"),
            metric("DCGM_FI_DEV_GPU_UTIL", "1 123.5"),
            metric("DCGM_FI_DEV_GPU_UTIL", "1 trailing"),
            metric("DCGM_FI_DEV_GPU_UTIL", "1_000"),
            metric("DCGM_FI_DEV_GPU_UTIL", "0x10"),
            metric("DCGM_FI_DEV_GPU_UTIL", "1e9999999999999999999999"),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ',gpu="0"'),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ',bad="bad\\q"'),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ',bad="bad\\t"'),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ',bad="unterminated'),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ',bad="raw\x00"'),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ",unquoted=1"),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ',bad="a" bad2="b"'),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS.replace('gpu="0",', "")),
            metric(
                "DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS.replace('UUID="GPU-one",', "")
            ),
            metric(
                "DCGM_FI_DEV_GPU_UTIL",
                1,
                BASE_LABELS.replace(',modelName="Fixture GPU"', ""),
            ),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS.replace("GPU-one", "other")),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS.replace("GPU-one", "GPU-")),
            metric(
                "DCGM_FI_DEV_GPU_UTIL",
                1,
                BASE_LABELS.replace("GPU-one", "GPU-" + "x" * 125),
            ),
            metric(
                "DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS.replace("Fixture GPU", "x" * 256)
            ),
            metric(
                "DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ',driver="' + "x" * 129 + '"'
            ),
            metric(
                "DCGM_FI_DEV_GPU_UTIL",
                1,
                BASE_LABELS + ',driver="1",DCGM_FI_DRIVER_VERSION="2"',
            ),
        )
        invalid += tuple(
            metric(
                "DCGM_FI_DEV_GPU_UTIL",
                1,
                BASE_LABELS.replace('gpu="0"', 'gpu="' + index + '"'),
            )
            for index in ("-1", "1024", "1.0", "1e0", " 1", "one", "")
        )
        for text in invalid:
            with self.subTest(text=text[:120]), self.assertRaises(ValueError) as caught:
                agent.parse_dcgm_metrics(text)
            self.assertEqual(str(caught.exception), "DCGM metrics invalid")

    def test_parser_body_row_label_and_gpu_count_bounds(self):
        text = "".join(
            metric(
                "DCGM_FI_DEV_GPU_UTIL",
                1,
                BASE_LABELS.replace('gpu="0"', f'gpu="{index}"').replace(
                    "GPU-one", f"GPU-{index}"
                ),
            )
            for index in range(128)
        )
        self.assertEqual(len(agent.parse_dcgm_metrics(text)[0]), 128)
        excess = metric(
            "DCGM_FI_DEV_GPU_UTIL",
            1,
            'gpu="128",UUID="GPU-extra",modelName="Fixture GPU"',
        )
        for invalid in (
            text + excess,
            "#" * (agent.DCGM_MAX_BYTES + 1),
            "#" + "é" * (agent.DCGM_MAX_BYTES // 2),
            metric(
                "DCGM_FI_DEV_GPU_UTIL", 1, BASE_LABELS + ',job="' + "x" * 16384 + '"'
            ),
            metric(
                "DCGM_FI_DEV_GPU_UTIL",
                1,
                BASE_LABELS + "".join(f',label{i}="x"' for i in range(64)),
            ),
            metric("DCGM_FI_DEV_GPU_UTIL", "1" * 129),
            metric("DCGM_FI_DEV_GPU_UTIL", 1, timestamp="1" * 21),
            metric("DCGM_FI_DEV_GPU_UTIL", 1) * 32769,
        ):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                agent.parse_dcgm_metrics(invalid)
        self.assertEqual(agent.parse_dcgm_metrics("#" * agent.DCGM_MAX_BYTES)[0], [])


class ProviderTests(NoCommandsTest):
    def test_default_diagnostics_and_explicit_nvidia_keep_existing_collector(self):
        for gpu in (None, {}, {"provider": "nvidia-smi"}):
            with (
                self.subTest(gpu=gpu),
                system_probes(),
                patch.object(
                    agent, "gpu_data", return_value=([{"index": 0}], [])
                ) as nvidia,
                patch.object(agent, "dcgm_gpu_data") as dcgm,
            ):
                data = agent.collect(gpu)
            nvidia.assert_called_once_with()
            dcgm.assert_not_called()
            self.assertEqual(data["gpu_provider"], "nvidia-smi")
            self.assertEqual(data["gpus"], [{"index": 0}])
            self.assertEqual(data["system"]["memory_percent"], 20)

    def test_selection_and_no_primary_fallback_with_system_metrics(self):
        config = {"provider": "dcgm", "processes": "none"}
        for failure in (
            ValueError("private-exporter-body"),
            OSError("private-url-token"),
        ):
            with (
                system_probes(),
                patch.object(agent, "gpu_data") as nvidia,
                patch.object(agent, "read_dcgm_metrics", side_effect=failure),
            ):
                data = agent.collect(config)
            nvidia.assert_not_called()
            self.assertEqual(data["gpu_provider"], "dcgm")
            self.assertEqual(data["gpus"], [])
            self.assertEqual(data["system"]["memory_percent"], 20)
            self.assertTrue(data["warnings"])
            self.assertNotIn("private-", str(data))

    def test_default_process_policy_is_not_a_primary_fallback_on_scrape_failure(self):
        with (
            system_probes(),
            patch.object(
                agent, "read_dcgm_metrics", side_effect=ValueError("private-body")
            ),
            patch.object(agent, "gpu_data") as nvidia,
        ):
            data = agent.collect({"provider": "dcgm"})
        nvidia.assert_not_called()
        self.assertEqual(data["system"]["memory_percent"], 20)
        self.assertEqual(data["gpus"], [])
        self.assertTrue(data["warnings"])
        self.assertNotIn("private-body", str(data))

    def test_make_report_passes_interval_provider_on_success_and_failure(self):
        config = {
            "host": "fixture",
            "interval": 90,
            "gpu": {"provider": "dcgm", "processes": "none"},
        }
        with patch.object(
            agent, "collect", return_value={"system": {}, "gpus": [], "warnings": []}
        ) as collect:
            report = agent.make_report(config)
        self.assertEqual(report["gpu_provider"], "dcgm")
        self.assertIsNone(report["error"])
        collect.assert_called_once_with(
            agent.validate_gpu_config(config["gpu"]), interval=90
        )
        with patch.object(
            agent, "collect", side_effect=OSError("system probe unavailable")
        ):
            report = agent.make_report(config)
        self.assertEqual(report["gpu_provider"], "dcgm")
        self.assertEqual(report["error"], "system probe unavailable")
        self.assertEqual(report["gpus"], [])

    def test_scrape_failures_malformed_empty_and_no_caching(self):
        config = {"provider": "dcgm", "processes": "none"}
        with (
            patch.object(agent, "gpu_data") as nvidia,
            patch.object(
                agent,
                "read_dcgm_metrics",
                side_effect=[
                    metric("DCGM_FI_DEV_GPU_UTIL", 75),
                    "DCGM_FI_DEV_GPU_UTIL{malformed",
                    "",
                    metric("DCGM_FI_DEV_GPU_UTIL", 0),
                ],
            ),
        ):
            first, _ = agent.dcgm_gpu_data(config)
            failed, warnings = agent.dcgm_gpu_data(config)
            empty, empty_warnings = agent.dcgm_gpu_data(config)
            last, _ = agent.dcgm_gpu_data(config)
        self.assertEqual(first[0]["utilization"], 75)
        self.assertEqual(failed, [])
        self.assertTrue(warnings)
        self.assertEqual(empty, [])
        self.assertTrue(empty_warnings)
        self.assertEqual(last[0]["utilization"], 0)
        nvidia.assert_not_called()

    def test_processes_uuid_only_reorder_and_no_base_metric_copy(self):
        text = metric("DCGM_FI_DEV_GPU_UTIL", 7)
        text += metric(
            "DCGM_FI_DEV_GPU_TEMP",
            42,
            BASE_LABELS.replace("GPU-one", "GPU-two").replace('gpu="0"', 'gpu="1"'),
        )
        sources = [
            {
                "index": 0,
                "uuid": "GPU-two",
                "utilization": 99,
                "memory_total_mb": 1234,
                "name": "not copied",
                "processes": [process(2)],
            },
            {
                "index": 1,
                "uuid": "GPU-one",
                "utilization": 98,
                "memory_total_mb": 5678,
                "name": "not copied",
                "processes": [process(1)],
            },
        ]
        with (
            patch.object(agent, "read_dcgm_metrics", return_value=text),
            patch.object(agent, "gpu_data", return_value=(sources, [])) as nvidia,
        ):
            gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
        nvidia.assert_called_once_with()
        self.assertFalse(warnings)
        self.assertEqual([gpu["uuid"] for gpu in gpus], ["GPU-one", "GPU-two"])
        self.assertEqual(
            [gpu["processes"] for gpu in gpus], [[process(1)], [process(2)]]
        )
        self.assertTrue(all(gpu["processes_available"] for gpu in gpus))
        self.assertEqual(gpus[0]["utilization"], 7)
        self.assertIsNone(gpus[1]["utilization"])
        self.assertTrue(all(gpu["memory_total_mb"] is None for gpu in gpus))
        self.assertTrue(all(gpu["name"] == "Fixture GPU" for gpu in gpus))

    def test_no_matching_uuid_never_joins_by_index_and_absence_is_unknown(self):
        for result in (
            ([], []),
            ([], ["private-command-stderr"]),
            ([{"index": 0, "uuid": "GPU-wrong", "processes": [process(3)]}], []),
            ([{"index": 0, "processes": [process(3)]}], []),
        ):
            with (
                self.subTest(result=result),
                patch.object(
                    agent,
                    "read_dcgm_metrics",
                    return_value=metric("DCGM_FI_DEV_GPU_UTIL", 5),
                ),
                patch.object(agent, "gpu_data", return_value=result),
            ):
                gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
            self.assertEqual(gpus[0]["processes"], [])
            self.assertFalse(gpus[0]["processes_available"])
            self.assertEqual(gpus[0]["utilization"], 5)
            self.assertTrue(warnings)
            self.assertNotIn("private-command-stderr", str(warnings))

    def test_empty_process_list_is_available_only_with_successful_uuid_match(self):
        source = {"uuid": "GPU-one", "processes": []}
        with (
            patch.object(
                agent,
                "read_dcgm_metrics",
                return_value=metric("DCGM_FI_DEV_GPU_UTIL", 0),
            ),
            patch.object(agent, "gpu_data", return_value=([source], [])),
        ):
            gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
        self.assertTrue(gpus[0]["processes_available"])
        self.assertFalse(warnings)

    def test_processes_none_never_invokes_nvidia(self):
        with (
            patch.object(
                agent,
                "read_dcgm_metrics",
                return_value=metric("DCGM_FI_DEV_GPU_UTIL", 0),
            ),
            patch.object(agent, "gpu_data") as nvidia,
        ):
            gpus, warnings = agent.dcgm_gpu_data(
                {"provider": "dcgm", "processes": "none"}
            )
        nvidia.assert_not_called()
        self.assertFalse(gpus[0]["processes_available"])
        self.assertFalse(warnings)  # Explicitly disabled is not a health fault.

    def test_process_limits_ambiguity_and_command_exceptions_do_not_replace_primary(
        self,
    ):
        source = {"uuid": "GPU-one", "processes": [process(1)] * 4096}
        with (
            patch.object(
                agent,
                "read_dcgm_metrics",
                return_value=metric("DCGM_FI_DEV_GPU_UTIL", 0),
            ),
            patch.object(agent, "gpu_data", return_value=([source], [])),
        ):
            gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
        self.assertTrue(gpus[0]["processes_available"])
        self.assertEqual(len(gpus[0]["processes"]), 4096)
        self.assertFalse(warnings)
        for sources in (
            [{**source, "processes": source["processes"] + [process(1)]}],
            [source, source],
            [{**source, "processes": [{**process(1), "pid": 0}]}],
            [{**source, "processes": [{**process(1), "memory_mb": float("nan")}]}],
        ):
            with (
                self.subTest(rows=len(sources)),
                patch.object(
                    agent,
                    "read_dcgm_metrics",
                    return_value=metric("DCGM_FI_DEV_GPU_UTIL", 12),
                ),
                patch.object(agent, "gpu_data", return_value=(sources, [])),
            ):
                gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
            self.assertFalse(gpus[0]["processes_available"])
            self.assertEqual(gpus[0]["processes"], [])
            self.assertEqual(gpus[0]["utilization"], 12)
            self.assertTrue(warnings)
        with (
            patch.object(
                agent,
                "read_dcgm_metrics",
                return_value=metric("DCGM_FI_DEV_GPU_UTIL", 0),
            ),
            patch.object(
                agent,
                "gpu_data",
                side_effect=subprocess.TimeoutExpired("private-command", 8),
            ),
        ):
            gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
        self.assertEqual(gpus[0]["utilization"], 0)
        self.assertFalse(gpus[0]["processes_available"])
        self.assertNotIn("private-command", str(warnings))

    def test_absent_process_command_and_partial_uuid_matches_are_unknown(self):
        text = metric("DCGM_FI_DEV_GPU_UTIL", 0)
        with (
            patch.object(agent, "read_dcgm_metrics", return_value=text),
            patch.object(agent.shutil, "which", return_value=None),
        ):
            gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
        self.assertFalse(gpus[0]["processes_available"])
        self.assertTrue(warnings)
        text += metric(
            "DCGM_FI_DEV_GPU_UTIL",
            1,
            BASE_LABELS.replace("GPU-one", "GPU-two").replace('gpu="0"', 'gpu="1"'),
        )
        with (
            patch.object(agent, "read_dcgm_metrics", return_value=text),
            patch.object(
                agent,
                "gpu_data",
                return_value=([{"uuid": "GPU-one", "processes": []}], []),
            ),
        ):
            gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
        self.assertEqual([gpu["processes_available"] for gpu in gpus], [True, False])
        self.assertTrue(warnings)

    def test_process_limit_applies_across_gpus(self):
        text = metric("DCGM_FI_DEV_GPU_UTIL", 1)
        text += metric(
            "DCGM_FI_DEV_GPU_UTIL",
            2,
            BASE_LABELS.replace("GPU-one", "GPU-two").replace('gpu="0"', 'gpu="1"'),
        )
        sources = [
            {"uuid": "GPU-one", "processes": [process(1)] * 2048},
            {"uuid": "GPU-two", "processes": [process(2)] * 2049},
        ]
        with (
            patch.object(agent, "read_dcgm_metrics", return_value=text),
            patch.object(agent, "gpu_data", return_value=(sources, [])),
        ):
            gpus, warnings = agent.dcgm_gpu_data({"provider": "dcgm"})
        self.assertTrue(warnings)
        self.assertTrue(
            all(not gpu["processes_available"] and not gpu["processes"] for gpu in gpus)
        )


class NvidiaCompatibilityTests(NoCommandsTest):
    XML = """<nvidia_smi_log><driver_version>555.1</driver_version><gpu>
      <uuid>GPU-one</uuid><minor_number>3</minor_number><product_name>Fixture GPU</product_name>
      <utilization><gpu_util>0 %</gpu_util></utilization>
      <fb_memory_usage><used>0 MiB</used><total>8192 MiB</total></fb_memory_usage>
      <power_readings><power_draw>20.5 W</power_draw><power_limit>300 W</power_limit></power_readings>
      <fan_speed>N/A</fan_speed><temperature><gpu_temp>50 C</gpu_temp></temperature>
      <processes><process_info><pid>123</pid><process_name>python</process_name>
      <type>C</type><used_memory>N/A</used_memory></process_info></processes>
      </gpu></nvidia_smi_log>"""

    def test_unchanged_nvidia_xml_collector_signature_units_and_process_rows(self):
        with (
            patch.object(agent.shutil, "which", return_value="nvidia-smi"),
            patch.object(
                agent.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, self.XML, ""),
            ) as run,
            patch.object(agent.os, "stat", side_effect=PermissionError),
        ):
            gpus, warnings = agent.gpu_data()
        self.assertFalse(warnings)
        self.assertEqual(gpus[0]["index"], 3)
        self.assertEqual(gpus[0]["utilization"], 0)
        self.assertEqual(gpus[0]["memory_used_mb"], 0)
        self.assertEqual(gpus[0]["memory_percent"], 0)
        self.assertEqual(gpus[0]["power_w"], 20.5)
        self.assertEqual(gpus[0]["driver"], "555.1")
        self.assertIsNone(gpus[0]["fan_percent"])
        self.assertEqual(gpus[0]["processes"][0]["pid"], 123)
        self.assertIsNone(gpus[0]["processes"][0]["user"])
        self.assertIsNone(gpus[0]["processes"][0]["memory_mb"])
        self.assertEqual(run.call_args.args, (["nvidia-smi", "-q", "-x"],))
        self.assertEqual(run.call_args.kwargs["timeout"], 8)
        self.assertTrue(run.call_args.kwargs["universal_newlines"])

    def test_nvidia_absent_and_malformed_xml_are_bounded_without_commands(self):
        with patch.object(agent.shutil, "which", return_value=None):
            self.assertEqual(agent.gpu_data(), ([], []))
        with (
            patch.object(agent.shutil, "which", return_value="nvidia-smi"),
            patch.object(
                agent.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "not xml", ""),
            ),
        ):
            gpus, warnings = agent.gpu_data()
        self.assertEqual(gpus, [])
        self.assertTrue(warnings)


class LoopbackHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, dict(self.headers)))
        try:
            self.server.respond(self)
        except (OSError, ValueError):
            # Expected when a bounded client closes a slow or oversized reply.
            pass


@contextmanager
def exporter(respond, ipv6=False):
    class IPv6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    server_type = IPv6Server if ipv6 else ThreadingHTTPServer
    host = "::1" if ipv6 else "127.0.0.1"
    server = server_type((host, 0), LoopbackHandler)
    server.respond, server.requests = respond, []
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}
    )
    thread.start()
    try:
        url = "http://{}:{}/metrics".format(
            "[::1]" if ipv6 else host, server.server_address[1]
        )
        yield url, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def send_body(handler, body, length=True, status=200, headers=None):
    handler.send_response(status)
    if length:
        handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


class HTTPTests(NoCommandsTest):
    def test_real_loopback_scrape_ignores_proxy_environment_and_has_no_credentials(
        self,
    ):
        body = metric("DCGM_FI_DEV_GPU_UTIL", 0).encode()
        with (
            exporter(lambda handler: send_body(handler, body)) as (url, server),
            patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "http_proxy": "http://127.0.0.1:1",
                    "ALL_PROXY": "http://127.0.0.1:1",
                    "all_proxy": "http://127.0.0.1:1",
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
            ),
            patch.object(agent, "gpu_data") as nvidia,
        ):
            gpus, warnings = agent.dcgm_gpu_data(
                {"provider": "dcgm", "url": url, "processes": "none"}
            )
        self.assertEqual(gpus[0]["utilization"], 0)
        self.assertFalse(warnings)  # Process information explicitly disabled.
        nvidia.assert_not_called()
        self.assertEqual(len(server.requests), 1)
        path, headers = server.requests[0]
        self.assertEqual(path, "/metrics")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Accept-Encoding"], "identity")

    def test_ipv6_loopback(self):
        if not socket.has_ipv6:
            self.skipTest("IPv6 not available")
        body = metric("DCGM_FI_DEV_GPU_UTIL", 50).encode()
        try:
            with exporter(lambda handler: send_body(handler, body), ipv6=True) as (
                url,
                _,
            ):
                data = agent.read_dcgm_metrics(
                    agent.validate_gpu_config({"provider": "dcgm", "url": url})
                )
        except OSError:
            self.skipTest("IPv6 loopback not available")
        self.assertEqual(data, body.decode())

    def test_redirect_is_not_followed_even_to_another_loopback_exporter(self):
        for status in (301, 302, 303, 307, 308):
            with (
                self.subTest(status=status),
                exporter(
                    lambda handler: send_body(handler, b"private-target-body")
                ) as (target, target_server),
            ):
                with (
                    exporter(
                        lambda handler, status=status: send_body(
                            handler,
                            b"private-redirect-body",
                            status=status,
                            headers={"Location": target},
                        )
                    ) as (url, _),
                    self.assertRaises(ValueError) as caught,
                ):
                    agent.read_dcgm_metrics(
                        agent.validate_gpu_config({"provider": "dcgm", "url": url})
                    )
                self.assertEqual(target_server.requests, [])
                self.assertEqual(str(caught.exception), "DCGM metrics unavailable")

    def test_invalid_url_rejected_before_any_http_or_nvidia_call(self):
        for url in (
            "https://127.0.0.1/metrics",
            "http://example.org/metrics",
            "http://@127.0.0.1/metrics",
        ):
            with (
                self.subTest(url=url),
                patch.object(agent, "build_opener") as opener,
                patch.object(agent, "gpu_data") as nvidia,
                self.assertRaises(ValueError),
            ):
                agent.dcgm_gpu_data({"provider": "dcgm", "url": url})
            opener.assert_not_called()
            nvidia.assert_not_called()

    def test_bad_http_status_encoding_and_partial_body_errors_are_generic(self):
        responses = [
            lambda handler: send_body(handler, b"private-error-body", status=503),
            lambda handler: send_body(handler, b"private-error-body", status=204),
            lambda handler: send_body(handler, b"\xffprivate-error-body"),
            lambda handler: send_body(
                handler, b"private-error-body", headers={"Content-Encoding": "gzip"}
            ),
            lambda handler: send_body(
                handler,
                b"private-error-body",
                length=False,
                headers={"Content-Length": "1000"},
            ),
        ]
        for respond in responses:
            with (
                self.subTest(respond=respond),
                exporter(respond) as (url, _),
                self.assertRaises(ValueError) as caught,
            ):
                agent.read_dcgm_metrics(
                    agent.validate_gpu_config({"provider": "dcgm", "url": url})
                )
            self.assertEqual(str(caught.exception), "DCGM metrics unavailable")

    def test_response_body_limit_with_and_without_content_length(self):
        exact = b"#" * agent.DCGM_MAX_BYTES
        for length in (True, False):
            for body, valid in ((exact, True), (exact + b"x", False)):
                with (
                    self.subTest(length=length, valid=valid),
                    exporter(
                        lambda handler, body=body, length=length: send_body(
                            handler, body, length=length
                        )
                    ) as (url, _),
                ):
                    config = agent.validate_gpu_config({"provider": "dcgm", "url": url})
                    if valid:
                        self.assertEqual(
                            len(agent.read_dcgm_metrics(config)), agent.DCGM_MAX_BYTES
                        )
                    else:
                        with self.assertRaises(ValueError):
                            agent.read_dcgm_metrics(config)

    def test_configured_socket_and_whole_request_deadline(self):
        def slow_body(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", "100")
            handler.end_headers()
            for _ in range(30):
                handler.wfile.write(b"x")
                handler.wfile.flush()
                time.sleep(0.08)

        def slow_headers(handler):
            # Trickle a header: checking only response.read would not bound this.
            handler.wfile.write(b"HTTP/1.0 200 OK\r\nX-Slow: ")
            handler.wfile.flush()
            for _ in range(30):
                handler.wfile.write(b"x")
                handler.wfile.flush()
                time.sleep(0.08)

        def slow_chunked(handler):
            handler.send_response(200)
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            # Incomplete chunk-size line must share the same absolute deadline.
            for _ in range(30):
                handler.wfile.write(b"1")
                handler.wfile.flush()
                time.sleep(0.08)

        for respond in (slow_body, slow_headers, slow_chunked):
            with self.subTest(respond=respond.__name__), exporter(respond) as (url, _):
                config = agent.validate_gpu_config(
                    {"provider": "dcgm", "url": url, "timeout": 1}
                )
                start = time.monotonic()
                with self.assertRaises(ValueError) as caught:
                    agent.read_dcgm_metrics(config)
                elapsed = time.monotonic() - start
            self.assertGreater(elapsed, 0.7)
            self.assertLess(elapsed, 1.8)
            self.assertEqual(str(caught.exception), "DCGM metrics unavailable")

    def test_handler_selection_read_bounds_and_configured_timeout(self):
        response = Mock()
        response.status = 200
        response.headers = {}
        response.read.side_effect = [b"# local", b""]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(agent, "build_opener", return_value=opener) as build:
            value = agent.read_dcgm_metrics(
                agent.validate_gpu_config({"provider": "dcgm", "timeout": 8})
            )
        self.assertEqual(value, "# local")
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], agent.NoRedirect)
        self.assertIsInstance(build.call_args.args[2], agent._DeadlineHTTPHandler)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 8)
        self.assertTrue(
            all(0 < call.args[0] <= 65536 for call in response.read.call_args_list)
        )

    def test_open_failures_are_safe_and_http_error_responses_are_closed(self):
        stream = io.BytesIO(b"private-error-body")
        http_error = HTTPError("http://private-url", 500, "private-message", {}, stream)
        for error in (
            http_error,
            URLError("private-url"),
            OSError("private-error-body"),
        ):
            opener = Mock()
            opener.open.side_effect = error
            with (
                patch.object(agent, "build_opener", return_value=opener),
                self.assertRaises(ValueError) as caught,
            ):
                agent.read_dcgm_metrics({"provider": "dcgm"})
            self.assertEqual(str(caught.exception), "DCGM metrics unavailable")
        self.assertTrue(stream.closed)

    def test_deadline_reader_sets_remaining_budget_per_raw_read(self):
        sock = Mock()
        with patch.object(
            agent.time, "monotonic", side_effect=[10, 10.1, 10.5, 10.6, 12]
        ):
            stream = agent._DeadlineReader(io.BytesIO(b"ab"), sock, 11)
            self.assertEqual(stream.readinto(bytearray(1)), 1)
            self.assertEqual(stream.readinto(bytearray(1)), 1)
            with self.assertRaises(OSError):
                stream.readinto(bytearray(1))
        self.assertEqual(
            [call.args[0] for call in sock.settimeout.call_args_list], [1, 0.5]
        )
        stream.close()


if __name__ == "__main__":
    unittest.main()
