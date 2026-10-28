"""GPU source settings cross the public CLI boundary, not application imports on nodes."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from _reporter import remote_probe
from monitor.schemas.gpu import parse_gpu_config
from project_tools.configure import generate

VALID = [
    {},
    {"provider": "nvidia-smi"},
    {"provider": "dcgm"},
    {
        "provider": "dcgm",
        "url": "http://[::1]:9401/metrics",
        "timeout": 1,
        "processes": "none",
    },
    {
        "provider": "dcgm",
        "url": "http://127.0.0.1/metrics",
        "timeout": 8,
        "processes": "nvidia-smi",
    },
]
INVALID = [
    None,
    [],
    "dcgm",
    True,
    1,
    {"provider": "auto"},
    {"provider": None},
    {"provider": []},
    {"token": "unit-test-secret-value"},
    {"provider": "nvidia-smi", "timeout": 3},
    {"provider": "nvidia-smi", "url": "http://127.0.0.1:9400/metrics"},
    {"provider": "nvidia-smi", "processes": "nvidia-smi"},
    *[{"provider": "dcgm", "timeout": value} for value in (None, True, 0, 9, "3", 3.0)],
    *[
        {"provider": "dcgm", "processes": value}
        for value in (None, False, "auto", "nvml", [])
    ],
    *[
        {"provider": "dcgm", "url": value}
        for value in (
            None,
            1,
            [],
            "http://localhost:9400/metrics",
            "http://192.0.2.1:9400/metrics",
            "https://127.0.0.1:9400/metrics",
            "http://127.0.0.1:0/metrics",
            "http://127.0.0.1:65536/metrics",
            "http://127.0.0.1:bad/metrics",
            "http://127.0.0.1:/metrics",
            "http://127.0.0.1:9400/metrics/",
            "http://127.0.0.1:9400/%6detrics",
            "http://127.0.0.1:9400/metrics?",
            "http://127.0.0.1:9400/metrics#",
            "http://@127.0.0.1:9400/metrics",
            "http://user:secret@127.0.0.1:9400/metrics",
            " http://127.0.0.1:9400/metrics",
            "http://127.0.0.1:9400/met\nrics",
            "http://[::1/metrics",
            "http://[::1]extra/metrics",
            "http://127.0.0.1/metrics\x7f",
        )
    ],
]


class GPUConfigTests(unittest.TestCase):
    def test_backend_and_independent_reporter_validate_identical_settings(self):
        for value in VALID:
            original = copy.deepcopy(value)
            with self.subTest(value=value):
                normalized = parse_gpu_config(value)
                self.assertEqual(remote_probe.validate_gpu_config(value), normalized)
                self.assertEqual(value, original)
                self.assertEqual(parse_gpu_config(normalized), normalized)
        for value in INVALID:
            for parser in (parse_gpu_config, remote_probe.validate_gpu_config):
                with (
                    self.subTest(value=value, parser=parser),
                    self.assertRaises(ValueError) as error,
                ):
                    parser(value)
                self.assertNotIn("unit-test-secret-value", str(error.exception))
                self.assertNotIn("user:secret", str(error.exception))

    def test_optional_per_node_settings_do_not_leak_to_center_or_rotate_credentials(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = [
                {"name": "default", "address": "192.0.2.1"},
                {"name": "dcgm", "address": "192.0.2.2", "gpu": {"provider": "dcgm"}},
            ]
            before = copy.deepcopy(nodes)
            server = generate(root, nodes, "http://127.0.0.1:8080/api/node/report")
            self.assertEqual(nodes, before)
            self.assertNotIn("gpu", server["hosts"][1])
            default = json.loads((root / "agents/default.json").read_text())
            self.assertNotIn("gpu", default)
            self.assertEqual(
                remote_probe.load_reporter_config(root / "agents/default.json")["gpu"],
                {"provider": "nvidia-smi"},
            )
            dcgm = remote_probe.load_reporter_config(root / "agents/dcgm.json")
            self.assertEqual(dcgm["gpu"], parse_gpu_config({"provider": "dcgm"}))
            self.assertEqual(
                generate(root, nodes, "http://127.0.0.1:8080/api/node/report"), server
            )
            # The public node input is authoritative, not stale private output.
            nodes[1]["gpu"] = {"provider": "nvidia-smi"}
            self.assertEqual(
                generate(root, nodes, "http://127.0.0.1:8080/api/node/report"), server
            )
            changed = remote_probe.load_reporter_config(root / "agents/dcgm.json")
            self.assertEqual(changed["gpu"], {"provider": "nvidia-smi"})
            self.assertEqual(changed["token"], dcgm["token"])
            del nodes[1]["gpu"]
            generate(root, nodes, "http://127.0.0.1:8080/api/node/report")
            self.assertNotIn("gpu", json.loads((root / "agents/dcgm.json").read_text()))

    def test_invalid_settings_fail_before_replacing_any_private_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generate(
                root,
                [{"name": "node-a", "address": "192.0.2.1"}],
                "http://127.0.0.1:8080/api/node/report",
            )
            before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            for settings in INVALID:
                with self.subTest(settings=settings), self.assertRaises(ValueError):
                    generate(
                        root,
                        [{"name": "node-a", "address": "192.0.2.1", "gpu": settings}],
                        "http://127.0.0.1:8080/api/node/report",
                    )
                self.assertEqual(
                    before,
                    {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()},
                )


if __name__ == "__main__":
    unittest.main()
