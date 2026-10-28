"""Additive DCGM snapshot fields: strict input, no historical metric substitution."""

import copy
import tempfile
import unittest
from pathlib import Path

from monitor.models import Host
from monitor.protocol import validate_report
from monitor.service import MonitoringService
from monitor.store import Store


def dcgm_report():
    return {
        "version": 1,
        "host": "node-a",
        "report_id": "00000000-0000-0000-0000-000000000001",
        "collected_at": 1000,
        "interval": 30,
        "system": {"cpu_percent": 0},
        "gpu_provider": "dcgm",
        "gpus": [
            {
                "index": 0,
                "uuid": "GPU-unit-test",
                "name": "Test GPU",
                "utilization": 40,
                "memory_percent": 25,
                "memory_used_mb": 25,
                "memory_total_mb": 100,
                "processes": [],
                "processes_available": False,
                "dcgm": {
                    "sm_active_percent": 99,
                    "tensor_active_percent": 0,
                    "pcie_rx_bytes_s": 1024,
                    "ecc_sbe_volatile": 0,
                    "xid_last": None,
                },
            }
        ],
    }


class DCGMReportTests(unittest.TestCase):
    def test_additive_fields_preserve_zero_null_and_legacy_shape(self):
        original = dcgm_report()
        before = copy.deepcopy(original)
        result = validate_report(original, 30, 1000)
        self.assertEqual(original, before)
        self.assertEqual(result["gpu_provider"], "dcgm")
        gpu = result["gpus"][0]
        self.assertFalse(gpu["processes_available"])
        self.assertEqual(gpu["dcgm"]["tensor_active_percent"], 0)
        self.assertEqual(gpu["dcgm"]["ecc_sbe_volatile"], 0)
        self.assertIsNone(gpu["dcgm"]["sm_occupancy_percent"])
        self.assertIsNone(gpu["dcgm"]["xid_last"])
        legacy = dcgm_report()
        del legacy["gpu_provider"]
        del legacy["gpus"][0]["dcgm"]
        del legacy["gpus"][0]["processes_available"]
        parsed = validate_report(legacy, 30, 1000)
        self.assertNotIn("gpu_provider", parsed)
        self.assertNotIn("dcgm", parsed["gpus"][0])
        self.assertNotIn("processes_available", parsed["gpus"][0])

    def test_source_and_process_coverage_are_not_coerced_or_contradictory(self):
        for provider in ("auto", "nvml", 1, True, []):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                validate_report({**dcgm_report(), "gpu_provider": provider}, 30, 1000)
        with self.assertRaisesRegex(ValueError, "DCGM 指标必须注明"):
            validate_report({**dcgm_report(), "gpu_provider": "nvidia-smi"}, 30, 1000)
        for coverage in (0, 1, "false", None):
            value = dcgm_report()
            value["gpus"][0]["processes_available"] = coverage
            with self.subTest(coverage=coverage), self.assertRaises(ValueError):
                validate_report(value, 30, 1000)
        value = dcgm_report()
        value["gpus"][0]["processes"] = [{"pid": 1, "name": "p"}]
        with self.assertRaisesRegex(ValueError, "不可读取的进程列表"):
            validate_report(value, 30, 1000)

    def test_metrics_reject_nonfinite_coercion_and_out_of_range_values(self):
        for key, maximum in (
            ("sm_active_percent", 100),
            ("sm_occupancy_percent", 100),
            ("tensor_active_percent", 100),
            ("dram_active_percent", 100),
            ("pcie_rx_bytes_s", 10**16),
            ("pcie_tx_bytes_s", 10**16),
            ("nvlink_rx_bytes_s", 10**16),
            ("nvlink_tx_bytes_s", 10**16),
            ("memory_temperature", 200),
            ("sm_clock_mhz", 10**6),
            ("memory_clock_mhz", 10**6),
            ("ecc_sbe_volatile", 2**53 - 1),
            ("ecc_dbe_volatile", 2**53 - 1),
            ("xid_last", 2**31 - 1),
        ):
            for invalid in (True, "0", -1, maximum + 1, float("nan"), float("inf")):
                value = dcgm_report()
                value["gpus"][0]["dcgm"][key] = invalid
                with (
                    self.subTest(key=key, value=invalid),
                    self.assertRaises(ValueError),
                ):
                    validate_report(value, 30, 1000)
        for key in ("ecc_sbe_volatile", "ecc_dbe_volatile", "xid_last"):
            value = dcgm_report()
            value["gpus"][0]["dcgm"][key] = 1.5
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_report(value, 30, 1000)

    def test_persistence_history_and_offline_keep_existing_semantics(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "history.sqlite3", mode="push")
            hosts = [Host("node-a", "192.0.2.1")]
            service = MonitoringService(hosts, store, interval=30, stale_after=100)
            service.accept_report(dcgm_report(), now=1000)
            self.assertEqual(
                store.latest()["node-a"]["gpus"][0]["dcgm"]["sm_active_percent"], 99
            )
            current = service.overview(now=1000)["hosts"][0]
            self.assertEqual(current["gpu_provider"], "dcgm")
            current["gpus"][0]["dcgm"]["sm_active_percent"] = 0
            self.assertEqual(
                service.overview(now=1000)["hosts"][0]["gpus"][0]["dcgm"][
                    "sm_active_percent"
                ],
                99,
            )
            history = service.history(start=990, end=1030, now=1030)
            self.assertEqual(history["aggregate"]["avg_gpu"], 40)
            self.assertEqual(history["aggregate"]["avg_gpu_memory"], 25)
            # Advanced ratios/counters remain current snapshot data; no DB schema
            # migration, extra synthetic samples, or changed GPU-hour semantics.
            self.assertEqual(history["aggregate"]["samples"], 1)
            service.tick(now=1101)
            offline = service.overview(now=1101)["hosts"][0]
            self.assertEqual(offline["gpu_provider"], "dcgm")
            self.assertEqual((offline["status"], offline["gpus"]), ("offline", []))
            restarted = MonitoringService(hosts, store)
            self.assertEqual(restarted.overview()["hosts"][0]["gpu_provider"], "dcgm")
            self.assertEqual(restarted.overview()["hosts"][0]["gpus"], [])


if __name__ == "__main__":
    unittest.main()
