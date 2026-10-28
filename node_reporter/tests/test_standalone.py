"""Node reporter conformance uses only the standard library, not the center venv."""

import ast
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
REPORTER = PROJECT / "node_reporter/python/reporter.py"
spec = importlib.util.spec_from_file_location("standalone_reporter_test", REPORTER)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class StandaloneTests(unittest.TestCase):
    def test_source_targets_python36_and_imports_only_standard_library(self):
        tree = ast.parse(REPORTER.read_text(), feature_version=(3, 6))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                self.assertIn(name.split(".")[0], sys.stdlib_module_names)
        self.assertNotIn("monitor", sys.modules)
        self.assertNotIn("pydantic", sys.modules)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reporter.py"
            path.write_bytes(REPORTER.read_bytes())
            result = subprocess.run(
                [sys.executable, "-B", "-S", str(path), "--help"],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Node Reporter", result.stdout)
            self.assertNotIn("agent", result.stdout.lower())

    def test_invalid_configuration_reports_reporter_terminology_without_credentials(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "node.json"
            path.write_text(json.dumps({"version": True, "token": "private-token"}))
            result = subprocess.run(
                [sys.executable, "-B", "-S", str(REPORTER), "--config", str(path)],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("节点上报配置", result.stderr)
            self.assertNotIn("private-token", result.stderr)
            self.assertNotIn("agent", result.stderr.lower())

    def test_config_fixture_and_strict_versions(self):
        fixture = json.loads((PROJECT / "contracts/agent-config-v1.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent.json"
            path.write_text(json.dumps(fixture))
            self.assertEqual(
                agent.load_reporter_config(path),
                {**fixture, "gpu": {"provider": "nvidia-smi"}},
            )
            for version in (True, False, "1", 1.0, 0, 2, None):
                path.write_text(json.dumps({**fixture, "version": version}))
                with self.subTest(version=version), self.assertRaises(ValueError):
                    agent.load_reporter_config(path)

    def test_canonical_and_legacy_report_urls_are_valid_without_accepting_aliases(self):
        fixture = json.loads((PROJECT / "contracts/agent-config-v1.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "node.json"
            for endpoint in ("/api/node/report", "/api/agent/report"):
                url = "https://example.org" + endpoint
                path.write_text(json.dumps({**fixture, "url": url}))
                self.assertEqual(agent.load_reporter_config(path)["url"], url)
                for invalid in (
                    url + "/",
                    url + "?token=secret",
                    url.replace("/api/", "/%61pi/"),
                ):
                    path.write_text(json.dumps({**fixture, "url": invalid}))
                    with self.subTest(url=invalid), self.assertRaises(ValueError):
                        agent.load_reporter_config(path)

    def test_success_and_failure_reports_keep_v1_null_units_and_identity(self):
        config = json.loads((PROJECT / "contracts/agent-config-v1.json").read_text())
        fixture = json.loads((PROJECT / "contracts/report-v1.json").read_text())
        data = {key: fixture[key] for key in ("system", "gpus", "warnings")}
        with (
            patch.object(agent, "collect", return_value=data),
            patch.object(agent.time, "time", return_value=fixture["collected_at"]),
        ):
            result = agent.make_report(config)
        self.assertIs(type(result["version"]), int)
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["host"], config["host"])
        self.assertEqual(result["gpu_provider"], "nvidia-smi")
        self.assertEqual(result["gpus"][0]["memory_used_mb"], 128)
        self.assertIsNone(result["system"]["memory_percent"])
        self.assertIsNone(result["gpus"][0]["utilization"])
        self.assertNotIn("token", result)
        with patch.object(
            agent, "collect", side_effect=OSError("fixture collection failure")
        ):
            failed = agent.make_report(config)
        self.assertEqual(failed["system"], {})
        self.assertEqual(failed["gpus"], [])
        self.assertEqual(failed["error"], "fixture collection failure")
        self.assertEqual(failed["gpu_provider"], "nvidia-smi")

    def test_sender_disables_proxies_redirects_and_keeps_tls_verification(self):
        # Behavioral redirect/error/size tests live in cross-component tests too.
        config = json.loads((PROJECT / "contracts/agent-config-v1.json").read_text())
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b'{"accepted":true}'
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with (
            patch.object(agent, "build_opener", return_value=opener) as build,
            patch.object(
                agent.ssl,
                "create_default_context",
                wraps=agent.ssl.create_default_context,
            ) as tls,
        ):
            self.assertEqual(
                agent.send_report(config, {"version": 1}), {"accepted": True}
            )
        tls.assert_called_once_with(cafile=None)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[2], agent.NoRedirect)
        self.assertIsNone(
            build.call_args.args[2].redirect_request(
                None, None, 302, "", {}, "https://example.org/"
            )
        )
        request = opener.open.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"), "Bearer " + config["token"]
        )
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 8)


if __name__ == "__main__":
    unittest.main()
