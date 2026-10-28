"""Installed/isolated CLI preflight creates no state and discloses no credentials."""

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from monitor import __main__ as entrypoint
from monitor.paths import default_database, state_directory

EXPECTED = {
    "application": "hqlab-monitor",
    "version": "0.2.0",
    "config_version": 1,
    "report_version": 1,
    "status": "ok",
}


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "server.json"
        self.config = {
            "version": 1,
            "viewer_token": "preflight-private-viewer-token",
            "hosts": [
                {
                    "name": "alpha",
                    "address": "192.0.2.1",
                    "agent_token": "preflight-private-node-token",
                }
            ],
        }
        self.path.write_text(json.dumps(self.config))

    def test_preflight_calls_no_store_connect_bind_runtime_or_write(self):
        output = io.StringIO()
        before = self.path.read_bytes()
        with (
            patch.object(sys, "argv", ["monitor", "--check-config", str(self.path)]),
            patch.dict(os.environ, {}, clear=True),
            redirect_stdout(output),
            patch.object(entrypoint, "Store", side_effect=AssertionError("no Store")),
            patch.object(
                sqlite3, "connect", side_effect=AssertionError("no SQLite connection")
            ),
            patch("socket.socket", side_effect=AssertionError("no socket")),
            patch.object(
                entrypoint, "MonitorRuntime", side_effect=AssertionError("no runtime")
            ),
            patch.object(Path, "mkdir", side_effect=AssertionError("no mkdir")),
        ):
            entrypoint.main()
        self.assertEqual(json.loads(output.getvalue()), EXPECTED)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        self.assertNotIn(self.config["viewer_token"], output.getvalue())

    def test_installed_isolated_module_works_outside_source_without_pythonpath(self):
        environment = {
            **os.environ,
            "HOME": str(self.root),
            "XDG_STATE_HOME": str(self.root / "state"),
            "PYTHONPATH": "/does-not-exist",
        }
        environment.pop("CLUSTERLENS_RELEASE_ID", None)
        environment.pop("CLUSTERLENS_VIEWER_TOKEN", None)
        result = subprocess.run(
            [sys.executable, "-I", "-m", "monitor", "--check-config", str(self.path)],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), EXPECTED)
        self.assertEqual(result.stderr, "")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_invalid_config_resources_sqlite_and_release_identity_fail_without_secrets(
        self,
    ):
        cases = [
            patch.object(
                entrypoint,
                "load_config",
                side_effect=ValueError(self.config["viewer_token"]),
            ),
            patch.object(entrypoint.sqlite3, "sqlite_version_info", (3, 24, 0)),
            patch.object(
                entrypoint.Assets, "validate", side_effect=ValueError("missing assets")
            ),
            patch.dict(
                os.environ, {"CLUSTERLENS_RELEASE_ID": self.config["viewer_token"]}
            ),
        ]
        for failure in cases:
            output, error = io.StringIO(), io.StringIO()
            with (
                failure,
                patch.object(
                    sys, "argv", ["monitor", "--check-config", str(self.path)]
                ),
                redirect_stdout(output),
                redirect_stderr(error),
                patch.object(
                    entrypoint, "Store", side_effect=AssertionError("no Store")
                ),
                self.assertRaises(SystemExit) as result,
            ):
                entrypoint.main()
            self.assertEqual(result.exception.code, 1)
            self.assertEqual(output.getvalue(), "")
            self.assertNotIn(self.config["viewer_token"], error.getvalue())
            self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_preflight_rejects_database_option_without_creating_it(self):
        database = self.root / "forbidden.sqlite3"
        with (
            patch.object(
                sys,
                "argv",
                ["monitor", "--check-config", str(self.path), "--db", str(database)],
            ),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as result,
        ):
            entrypoint.main()
        self.assertEqual(result.exception.code, 2)
        self.assertFalse(database.exists())

    def test_default_state_paths_are_user_writable_distinct_and_not_created(self):
        with patch.dict(os.environ, {"HOME": str(self.root)}, clear=True):
            self.assertEqual(state_directory(), self.root / ".local/state/clusterlens")
            self.assertEqual(default_database().name, "monitor.sqlite3")
            self.assertEqual(default_database(demo=True).name, "monitor-demo.sqlite3")
        with patch.dict(
            os.environ, {"XDG_STATE_HOME": str(self.root / "state")}, clear=True
        ):
            self.assertEqual(state_directory(), self.root / "state/clusterlens")
        with patch.dict(
            os.environ,
            {"HOME": str(self.root), "XDG_STATE_HOME": "relative"},
            clear=True,
        ):
            self.assertEqual(state_directory(), self.root / ".local/state/clusterlens")
        self.assertEqual(list(self.root.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
