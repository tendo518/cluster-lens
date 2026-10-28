"""Viewer environment overrides are explicit and validated before starting services."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from monitor import __main__ as entrypoint


class StartupTokenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "server.json"
        self.config = {
            "version": 1,
            "viewer_token": "startup-config-viewer-token",
            "hosts": [
                {
                    "name": "node-a",
                    "address": "192.0.2.1",
                    "agent_token": "startup-node-agent-token",
                }
            ],
        }
        self.path.write_text(json.dumps(self.config))

    def tearDown(self):
        self.temporary.cleanup()

    def start(self, environment):
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(sys, "argv", ["monitor", "--config", str(self.path)]),
            patch.object(entrypoint, "Store"),
            patch.object(entrypoint, "MonitoringService"),
            patch.object(entrypoint, "MonitorRuntime"),
            patch.object(entrypoint, "MonitorServer") as server,
            patch.object(entrypoint.signal, "signal"),
            patch.object(entrypoint.logging, "basicConfig"),
            patch.object(entrypoint, "LOG"),
        ):
            server.return_value.server_address = ("127.0.0.1", 8080)
            entrypoint.main()
            return server.call_args.args[2]

    def test_resources_are_closed_even_when_start_serve_or_stop_fails(self):
        for failing_method in ("start", "serve_forever", "stop"):
            with (
                self.subTest(failing_method=failing_method),
                patch.dict(os.environ, {}, clear=True),
                patch.object(sys, "argv", ["monitor", "--config", str(self.path)]),
                patch.object(entrypoint, "Store"),
                patch.object(entrypoint, "MonitoringService"),
                patch.object(entrypoint, "MonitorRuntime") as runtime,
                patch.object(entrypoint, "MonitorServer") as server,
                patch.object(entrypoint.signal, "signal"),
                patch.object(entrypoint.logging, "basicConfig"),
                patch.object(entrypoint, "LOG"),
            ):
                server.return_value.server_address = ("127.0.0.1", 8080)
                owner = server if failing_method == "serve_forever" else runtime
                getattr(owner.return_value, failing_method).side_effect = RuntimeError(
                    "injected failure"
                )
                with self.assertRaisesRegex(RuntimeError, "injected failure"):
                    entrypoint.main()
                runtime.return_value.start.assert_called_once_with()
                runtime.return_value.stop.assert_called_once_with()
                server.return_value.server_close.assert_called_once_with()

    def test_environment_overrides_config_and_absent_or_empty_uses_config(self):
        for value in (None, "", "startup-environment-viewer-token"):
            with self.subTest(value=value):
                environment = (
                    {} if value is None else {"CLUSTERLENS_VIEWER_TOKEN": value}
                )
                self.assertEqual(
                    self.start(environment), value or self.config["viewer_token"]
                )

    def test_invalid_or_agent_environment_token_is_rejected_without_disclosure(self):
        for value in (
            "short",
            "invalid token containing spaces",
            self.config["hosts"][0]["agent_token"],
        ):
            with self.subTest(value=value):
                error = io.StringIO()
                with redirect_stderr(error), self.assertRaises(SystemExit) as result:
                    self.start({"CLUSTERLENS_VIEWER_TOKEN": value})
                self.assertEqual(result.exception.code, 2)
                self.assertNotIn(value, error.getvalue())


if __name__ == "__main__":
    unittest.main()
