"""Shared Pydantic models validate runtime input, not merely exported documents."""

import copy
import json
import unittest
from decimal import Decimal

from monitor.config import parse_config, valid_token
from monitor.protocol import validate_report
from monitor.schemas import Report, ServerConfig, export_schemas
from pydantic import BaseModel, ValidationError


def report():
    return {
        "version": 1,
        "host": "alpha",
        "report_id": "00000000-0000-0000-0000-000000000001",
        "collected_at": 1000,
        "interval": 30,
        "system": {"cpu_percent": 0},
        "gpus": [{"index": 0, "name": "GPU", "processes": []}],
    }


def config():
    return {
        "version": 1,
        "viewer_token": "unit-test-viewer-token",
        "hosts": [
            {
                "name": "alpha",
                "address": "192.0.2.1",
                "agent_token": "unit-test-agent-token",
            }
        ],
    }


class SchemaTests(unittest.TestCase):
    def test_models_are_used_at_both_runtime_boundaries_and_export_offline(self):
        self.assertTrue(issubclass(Report, BaseModel))
        self.assertTrue(issubclass(ServerConfig, BaseModel))
        self.assertEqual(
            validate_report(report(), 30, 1000),
            Report.model_validate(
                report(), context={"interval": 30, "now": 1000}
            ).snapshot(),
        )
        parsed, hosts, credentials = parse_config(config())
        model = ServerConfig.model_validate(config())
        self.assertEqual(hosts[0].name, model.hosts[0].name)
        self.assertEqual(credentials[hosts[0].name], model.hosts[0].agent_token)
        self.assertEqual(parsed, config())
        schemas = export_schemas()
        json.dumps(schemas, allow_nan=False)
        self.assertEqual(
            schemas["agent-report"]["properties"]["version"]["type"], "integer"
        )
        self.assertEqual(
            schemas["server-config"]["properties"]["version"]["maximum"], 1
        )
        self.assertEqual(
            schemas["agent-report"]["$defs"]["GPU"]["properties"]["index"]["maximum"],
            1023,
        )
        self.assertEqual(schemas["agent-report"]["properties"]["gpus"]["maxItems"], 128)
        numeric = schemas["agent-report"]["$defs"]["GPU"]["properties"]["utilization"]
        for branch in numeric["anyOf"]:
            if branch.get("type") != "null":
                self.assertEqual((branch["minimum"], branch["maximum"]), (0, 100))
        # JSON Schema cannot distinguish integer 1 from numeric 1.0: strict
        # version representation and context-dependent policy require runtime tests.

    def test_versions_are_strict_in_both_models_and_wrappers(self):
        for version in (True, False, 1.0, "1", None, 0, 2, [], {}):
            for model, fixture, wrapper in (
                (Report, report, lambda v: validate_report(v, 30, 1000)),
                (ServerConfig, config, parse_config),
            ):
                with self.subTest(model=model, version=version):
                    value = {**fixture(), "version": version}
                    with self.assertRaises(ValidationError):
                        model.model_validate(value)
                    with self.assertRaisesRegex(ValueError, "version=1"):
                        wrapper(value)
        for fixture, wrapper in (
            (report, lambda v: validate_report(v, 30, 1000)),
            (config, parse_config),
        ):
            value = fixture()
            del value["version"]
            with self.assertRaisesRegex(ValueError, "version=1"):
                wrapper(value)

    def test_missing_metrics_are_null_and_integers_not_coerced_to_floats(self):
        result = validate_report(report(), 30, 1000)
        self.assertEqual(result["system"]["cpu_percent"], 0)
        self.assertIs(type(result["system"]["cpu_percent"]), int)
        self.assertIsNone(result["system"]["memory_percent"])
        self.assertIsNone(result["collection_ms"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["system"]["hostname"], "—")
        for value in (None, 0, 0.5, 100):
            source = report()
            source["gpus"][0]["utilization"] = value
            self.assertEqual(
                validate_report(source, 30, 1000)["gpus"][0]["utilization"], value
            )

    def test_invalid_nested_metrics_and_missing_required_fields_are_not_coerced(self):
        for value in (
            True,
            False,
            "0",
            Decimal(0),
            float("nan"),
            float("inf"),
            -1,
            101,
        ):
            source = report()
            source["gpus"][0]["utilization"] = value
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "无效的数值字段：utilization"),
            ):
                validate_report(source, 30, 1000)
        for key, value in (
            ("warnings", None),
            ("gpus", None),
            ("system", None),
            ("system", {}),
            ("collected_at", None),
            ("interval", 30.0),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_report({**report(), key: value}, 30, 1000)
        for key in (
            "version",
            "report_id",
            "collected_at",
            "interval",
            "system",
            "gpus",
        ):
            source = report()
            del source[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_report(source, 30, 1000)
        for key in ("hostname", "load", "disks"):
            source = report()
            source["system"][key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_report(source, 30, 1000)
        source = report()
        del source["gpus"][0]["processes"]
        with self.assertRaisesRegex(ValueError, "无效的列表字段：processes"):
            validate_report(source, 30, 1000)

    def test_large_integer_bounds_are_checked_before_float_union_rounding(self):
        for field, maximum in (
            ("memory_total_bytes", 2**60),
            ("network_rx_bytes_s", 10**16),
        ):
            value = report()
            value["system"][field] = maximum
            self.assertEqual(validate_report(value, 30, 1000)["system"][field], maximum)
            value["system"][field] = maximum + 1
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                validate_report(value, 30, 1000)

    def test_failure_reports_ignore_metrics_but_not_envelope_errors(self):
        for metrics in ({}, {"system": None, "gpus": "discard"}):
            source = {
                key: value
                for key, value in report().items()
                if key not in ("system", "gpus")
            }
            source.update(metrics, error="probe failed")
            original = copy.deepcopy(source)
            result = validate_report(source, 30, 1000)
            self.assertEqual((result["system"], result["gpus"]), ({}, []))
            self.assertEqual(source, original)
            source["warnings"] = None
            with self.assertRaisesRegex(ValueError, "warnings"):
                validate_report(source, 30, 1000)
        with self.assertRaises(ValueError):
            validate_report({**report(), "error": "", "system": {}}, 30, 1000)

    def test_uuid_time_interval_and_total_process_limits(self):
        for ts in (940, 1060):
            self.assertEqual(
                validate_report({**report(), "collected_at": ts}, 30, 1000)[
                    "collected_at"
                ],
                ts,
            )
        for ts in (939.99, 1060.01):
            with self.assertRaisesRegex(ValueError, "采样时间过旧"):
                validate_report({**report(), "collected_at": ts}, 30, 1000)
        with self.assertRaisesRegex(ValueError, "interval"):
            validate_report({**report(), "interval": 60}, 30, 1000)
        with self.assertRaisesRegex(ValueError, "report_id 必须是 UUID"):
            validate_report({**report(), "report_id": "invalid"}, 30, 1000)
        source = report()
        source["gpus"] = [
            {
                "index": index,
                "name": "GPU",
                "processes": [{"pid": 1, "name": "p"}] * 2049,
            }
            for index in range(2)
        ]
        with self.assertRaisesRegex(ValueError, "进程数据无效或超过上限"):
            validate_report(source, 30, 1000)
        for pid in (True, 1.0, "1", 0, 2**31 + 1):
            source = report()
            source["gpus"][0]["processes"] = [{"pid": pid, "name": "p"}]
            with self.subTest(pid=pid), self.assertRaisesRegex(ValueError, "PID"):
                validate_report(source, 30, 1000)

    def test_config_bounds_uniqueness_and_credential_safe_errors(self):
        for changes in (
            {"interval": True},
            {"interval": "30"},
            {"interval": 30.0},
            {"stale_after": None},
            {"interval": 60},
            {"hosts": []},
            {"ui": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_config({**config(), **changes})
        for duplicate_name in (True, False):
            source = config()
            source["hosts"].append(
                {**source["hosts"][0], "name": "alpha" if duplicate_name else "beta"}
            )
            with self.assertRaises(ValueError):
                parse_config(source)
        source = config()
        source["hosts"][0]["agent_token"] = source["viewer_token"]
        with self.assertRaises(ValueError) as error:
            parse_config(source)
        self.assertNotIn(source["viewer_token"], str(error.exception))
        source["viewer_token"] = "invalid secret token"
        with self.assertRaises(ValidationError) as error:
            ServerConfig.model_validate(source)
        self.assertNotIn("invalid secret token", str(error.exception))
        for token in (
            None,
            True,
            "short",
            "a" * 257,
            "a b" * 10,
            "中" * 20,
            "x" * 16 + "\n",
        ):
            with self.subTest(token=token):
                self.assertFalse(valid_token(token))
        self.assertTrue(valid_token("x" * 16))


if __name__ == "__main__":
    unittest.main()
