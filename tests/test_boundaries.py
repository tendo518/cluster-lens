"""Application/infrastructure separation and the current source-release boundary."""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from monitor.demo import demo_hosts
from monitor.paths import ROOT


class RuntimeBoundaryTests(unittest.TestCase):
    def test_runtime_has_no_infrastructure_imports(self):
        for path in (ROOT / "monitor").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(
                    any(
                        name.split(".")[0]
                        in ("ansible", "monitor_deploy", "inventory", "project_tools")
                        for name in names
                    ),
                    str(path),
                )
        for name in (
            "configure",
            "cleanup",
            "inventory",
            "user_service",
            "migrate_history",
        ):
            self.assertFalse((ROOT / "monitor" / (name + ".py")).exists())

    def test_business_layer_is_independent_of_transport_and_scheduling(self):
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                (
                    "import monitor.service, sys; "
                    "assert not {'http.server', 'monitor.server', 'monitor.lifecycle'} "
                    "& sys.modules.keys()"
                ),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        transport = ast.parse((ROOT / "monitor/server.py").read_text())
        for node in ast.walk(transport):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(
                    node.attr, ("store", "query_history", "execute", "executemany")
                )

    def test_imports_work_with_site_packages_disabled(self):
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                (
                    "import monitor.service, monitor.lifecycle, monitor.config, "
                    "monitor.demo, monitor.history, monitor.exports, "
                    "monitor.server, monitor.store; import sys; "
                    "assert not any(n == 'ansible' or n.startswith('monitor_deploy') "
                    "for n in sys.modules)"
                ),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_host_script_has_only_stdlib_imports_and_runs_outside_backend(self):
        source = (ROOT / "agents/python/agent.py").read_text()
        tree = ast.parse(source, feature_version=(3, 6))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                names = [node.module]
            else:
                continue
            for name in names:
                self.assertIn(name.split(".")[0], sys.stdlib_module_names)
        with tempfile.TemporaryDirectory() as temporary:
            agent = Path(temporary) / "agent.py"
            agent.write_text(source)
            result = subprocess.run(
                [sys.executable, "-B", "-S", str(agent), "--help"],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--config", result.stdout)
        self.assertFalse((ROOT / "monitor/remote_probe.py").exists())

    def test_standalone_project_copy_builds_and_configures_without_parent_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "standalone"
            shutil.copytree(
                ROOT,
                project,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "__pycache__",
                    "data",
                    "dist",
                    "config",
                    ".ruff_cache",
                ),
            )
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("PYTHONPATH", "PYTHONHOME", "ANSIBLE_CONFIG")
            }
            for arguments in (
                ["-m", "project_tools", "build"],
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
                ["-m", "monitor", "--help"],
            ):
                result = subprocess.run(
                    [sys.executable, "-S", *arguments],
                    cwd=project,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((project / "dist/agent.py").is_file())
            self.assertFalse((project / "data").exists())
            self.assertTrue((project / "pyproject.toml").is_file())

    def test_demo_and_project_commands_have_no_cluster_specific_state(self):
        for host in demo_hosts():
            self.assertTrue(host.address.startswith("192.0.2."))
            self.assertTrue(host.name.startswith("demo-"))
            self.assertFalse(hasattr(host, "variables"))
        for relative in ("Justfile", "tests/browser-smoke.mjs"):
            text = (ROOT / relative).read_text()
            for forbidden in (".monitor-deploy", "../../../", "repo /", "join(repo,"):
                self.assertNotIn(forbidden, text)

    def test_real_mode_requires_an_explicit_runtime_config(self):
        result = subprocess.run(
            [sys.executable, "-S", "-m", "monitor"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--config", result.stderr)


if __name__ == "__main__":
    unittest.main()
