"""Installed backend, independent agent and application/controller ownership boundaries."""

import ast
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _release_fixture import PROJECT, install_runtime
from monitor.demo import demo_hosts

PACKAGE = PROJECT / "backend/src/monitor"


class RuntimeBoundaryTests(unittest.TestCase):
    def test_runtime_has_no_infrastructure_imports_or_source_root_searches(self):
        for path in PACKAGE.rglob("*.py"):
            if "static" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                self.assertFalse(
                    any(
                        n.split(".")[0]
                        in {"ansible", "monitor_deploy", "inventory", "project_tools"}
                        for n in names
                    ),
                    str(path),
                )
        for name in (
            "configure",
            "cleanup",
            "inventory",
            "user_service",
            "migrate_history",
            "remote_probe",
        ):
            self.assertFalse((PACKAGE / (name + ".py")).exists())
        self.assertFalse((PROJECT / "monitor").exists())
        self.assertNotIn("ROOT", (PACKAGE / "paths.py").read_text())
        self.assertTrue((PROJECT / "frontend/package-lock.json").is_file())
        self.assertTrue((PROJECT / "agents/python/agent.py").is_file())

    def test_business_layer_does_not_depend_on_http_or_scheduling(self):
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import monitor.service, sys; assert not {'http.server','monitor.server','monitor.lifecycle','fastapi'} & sys.modules.keys()",
            ],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (PACKAGE / "api").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(
                        node.attr, ("store", "query_history", "execute", "executemany")
                    )

    def test_installed_backend_and_copied_public_tools_do_not_use_original_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = install_runtime(root / "runtime")
            project = root / "standalone-project"
            project.mkdir()
            shutil.copytree(
                PROJECT / "project_tools",
                project / "project_tools",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            shutil.copytree(PROJECT / "examples", project / "examples")
            shutil.copy2(PROJECT / "pyproject.toml", project)
            # This installed environment has runtime deps, not Ansible/build deps.
            check = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import monitor,sys; from pathlib import Path; assert Path(monitor.__file__).is_relative_to(Path(sys.prefix)); assert 'ansible' not in sys.modules; import monitor.server,monitor.schemas",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            for arguments in (
                ["-m", "project_tools", "describe"],
                [
                    "-m",
                    "project_tools",
                    "configure",
                    "--nodes",
                    "examples/nodes.json",
                    "--url",
                    "http://127.0.0.1:8080/api/agent/report",
                ],
                ["-m", "project_tools", "check-config", "config/local/server.json"],
                ["-I", "-m", "monitor", "--help"],
            ):
                result = subprocess.run(
                    [str(python), *arguments],
                    cwd=project,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((project / "data").exists())
            self.assertTrue((project / "config/local/server.json").is_file())
            preflight = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "monitor",
                    "--check-config",
                    str(project / "config/local/server.json"),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(preflight.returncode, 0, preflight.stderr)
            self.assertEqual(json.loads(preflight.stdout)["status"], "ok")
            self.assertFalse(list(root.rglob("*.sqlite3")))

    def test_demo_and_project_commands_have_no_cluster_specific_state(self):
        for host in demo_hosts():
            self.assertTrue(host.address.startswith("192.0.2."))
            self.assertTrue(host.name.startswith("demo-"))
            self.assertFalse(hasattr(host, "variables"))
        for relative in ("Justfile", "frontend/tests/browser-smoke.mjs"):
            source = (PROJECT / relative).read_text()
            for forbidden in (".monitor-deploy", "../../../", "repo /", "join(repo,"):
                self.assertNotIn(forbidden, source)

    def test_real_mode_requires_an_explicit_runtime_config(self):
        import sys

        result = subprocess.run(
            [sys.executable, "-I", "-m", "monitor"],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--config", result.stderr)


if __name__ == "__main__":
    unittest.main()
