"""Frozen public protocol fixtures and generated schemas use the actual models."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _reporter import remote_probe
from monitor.schemas import Report, export_schemas
from project_tools.configure import generate

PROJECT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_committed_schemas_match_runtime_models_without_credentials(self):
        for name, schema in export_schemas().items():
            saved = json.loads(
                (PROJECT / "contracts" / (name + ".schema.json")).read_text()
            )
            self.assertEqual(saved, schema)
            self.assertNotIn("fixture-agent-token-not-a-secret", json.dumps(schema))

    def test_report_fixture_preserves_units_nulls_identity_and_clock_policy(self):
        fixture = json.loads((PROJECT / "contracts/report-v1.json").read_text())
        report = Report.model_validate(
            fixture, context={"now": 4000000, "interval": 30}
        )
        self.assertEqual(report.host, "fixture-node")
        self.assertEqual(report.gpus[0].memory_used_mb, 128)
        self.assertIsNone(report.system.memory_percent)
        self.assertIsNone(report.gpus[0].utilization)
        for change in (
            {"version": True},
            {"version": 1.0},
            {"interval": 60},
            {"collected_at": 0},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Report.model_validate(
                    {**fixture, **change}, context={"now": 4000000, "interval": 30}
                )

    def test_standalone_agent_output_conforms_to_backend_without_shared_imports(self):
        config = json.loads((PROJECT / "contracts/agent-config-v1.json").read_text())
        data = {"system": {"cpu_percent": 0}, "gpus": [], "warnings": []}
        with patch.object(remote_probe, "collect", return_value=data):
            payload = remote_probe.make_report(config)
        report = Report.model_validate(
            payload,
            context={"now": payload["collected_at"], "interval": config["interval"]},
        )
        self.assertEqual(report.host, config["host"])
        self.assertIsNone(report.system.memory_percent)
        self.assertNotIn("token", report.model_dump())
        with patch.object(
            remote_probe, "collect", side_effect=OSError("fixture failure")
        ):
            failed = remote_probe.make_report(config)
        self.assertEqual(Report.model_validate(failed).snapshot()["system"], {})

    def test_generated_config_and_single_file_agent_agree_on_versions_and_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = generate(
                directory,
                [{"name": "fixture-node", "address": "192.0.2.1"}],
                "https://monitor.example.org/api/agent/report",
            )
            agent = remote_probe.load_reporter_config(
                directory / "agents/fixture-node.json"
            )
            self.assertEqual(config["version"], agent["version"])
            self.assertEqual(config["hosts"][0]["agent_token"], agent["token"])
            self.assertNotEqual(config["viewer_token"], agent["token"])


if __name__ == "__main__":
    unittest.main()
