"""Format2 packaging gates: deterministic, complete, credential-free, explicitly offline."""

import ast
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from _release_fixture import PROJECT, shared_release
from project_tools.assets import public_files, verify_receipt
from project_tools.release import (
    archive_bytes,
    build,
    runtime_files,
    verify_dependencies,
)
from project_tools.wheels import locked_wheels, sha256, verify_cache


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_deterministic_complete_release_and_exact_independent_reporter(self):
        first = shared_release()
        second = self.root / "release"
        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("Build must be offline"),
        ):
            metadata = build(second)
        self.assertEqual(
            (first / "server.tar.gz").read_bytes(),
            (second / "server.tar.gz").read_bytes(),
        )
        self.assertEqual(metadata["format_version"], 2)
        self.assertEqual(metadata["config_version"], 1)
        self.assertEqual(metadata["report_version"], 1)
        self.assertEqual(metadata["center"]["target"]["python"], "3.13")
        self.assertEqual(set(metadata["agents"]), {"python"})
        agent = (second / "agent.py").read_bytes()
        self.assertEqual(
            agent, (PROJECT / "node_reporter/python/reporter.py").read_bytes()
        )
        ast.parse(agent.decode(), feature_version=(3, 6))
        for name, entry in metadata["files"].items():
            self.assertEqual(sha256((second / name).read_bytes()), entry["sha256"])
            self.assertEqual((second / name).stat().st_size, entry["size"])
        with tarfile.open(second / "server.tar.gz") as archive:
            self.assertEqual(
                set(archive.getnames()), set(metadata["center"]["members"])
            )
            for member in archive.getmembers():
                self.assertTrue(member.isfile())
                self.assertEqual(member.mtime, 0)
                self.assertEqual(member.mode, 0o644)
                body = archive.extractfile(member).read()
                self.assertEqual(
                    sha256(body), metadata["center"]["members"][member.name]["sha256"]
                )
                self.assertEqual(
                    len(body), metadata["center"]["members"][member.name]["size"]
                )
            wheel = next(
                n for n in archive.getnames() if n.startswith("wheels/cluster_lens-")
            )
            with zipfile.ZipFile(
                io.BytesIO(archive.extractfile(wheel).read())
            ) as package:
                names = package.namelist()
                for required in (
                    "monitor/__main__.py",
                    "monitor/api/app.py",
                    "monitor/schemas/report.py",
                    "monitor/static/index.html",
                    "monitor/static/app.js",
                    "monitor/static/appearance.js",
                ):
                    self.assertIn(required, names)
                for name in names:
                    self.assertFalse(
                        set(Path(name).parts)
                        & {
                            "project_tools",
                            "agents",
                            "node_reporter",
                            "tests",
                            "frontend",
                            "config",
                            "data",
                            "node_modules",
                            "__pycache__",
                        }
                    )
                    if name.endswith(".py"):
                        ast.parse(package.read(name).decode(), feature_version=(3, 13))
            requirements = archive.extractfile("requirements.txt").read().decode()
            self.assertIn("cluster-lens==0.2.0 --hash=sha256:", requirements)
            self.assertIn("fastapi==", requirements)
            self.assertNotIn("hatchling", requirements)
            self.assertNotIn("pip==", requirements)

    def test_missing_corrupt_or_linked_wheel_fails_before_publication(self):
        source = PROJECT / ".build/wheels"
        missing = self.root / "empty"
        missing.mkdir()
        with self.assertRaisesRegex(ValueError, "缺少已准备"):
            build(self.root / "not-published", wheelhouse=missing)
        self.assertFalse((self.root / "not-published").exists())
        cache = self.root / "cache"
        shutil.copytree(source, cache)
        name = next(iter(locked_wheels()))
        path = cache / name
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        with self.assertRaisesRegex(ValueError, "摘要"):
            verify_cache(cache)
        path.unlink()
        path.symlink_to(source / name)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            verify_cache(cache)

    def test_transitive_dependency_omission_is_rejected_before_archive_creation(self):
        files, _ = verify_cache(PROJECT / ".build/wheels")
        runtime = {
            name: body for name, body in files.items() if name.startswith("wheels/")
        }
        verify_dependencies(runtime)
        del runtime[next(name for name in runtime if "/pydantic_core-" in name)]
        with self.assertRaisesRegex(ValueError, "不完整"):
            verify_dependencies(runtime)

    def test_nested_modules_are_walked_without_private_or_linked_directories(self):
        package = self.root / "monitor"
        (package / "api").mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "api/routes.py").write_text("VALUE = 1\n")
        for name in ("tests", "docs", "data", "config", "__pycache__", ".private"):
            (package / name).mkdir()
            (package / name / "secret.py").write_text("SECRET = True\n")
        self.assertEqual(
            [
                p.relative_to(package).as_posix()
                for p in runtime_files(package, {".py"})
            ],
            ["__init__.py", "api/routes.py"],
        )
        (package / "linked").symlink_to(package / "data", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            list(runtime_files(package, {".py"}))

    def test_sdist_contains_reporter_source_and_not_the_old_source_path(self):
        output = self.root / "sdist"
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "hatchling",
                "build",
                "-t",
                "sdist",
                "-d",
                str(output),
            ],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            check=True,
        )
        with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
            names = archive.getnames()
            self.assertTrue(
                any(n.endswith("/node_reporter/python/reporter.py") for n in names)
            )
            self.assertFalse(any("/agents/python/agent.py" in n for n in names))

    def test_source_subdirectories_cannot_be_release_outputs(self):
        project = self.root / "project"
        project.mkdir()
        with patch("project_tools.release.PROJECT", project):
            for component in ("backend", "frontend", "node_reporter", "project_tools"):
                output = project / component / "release"
                with (
                    self.subTest(component=component),
                    self.assertRaisesRegex(ValueError, "组件源码目录"),
                ):
                    build(output)
                self.assertFalse(output.exists())

    def test_reporter_links_and_missing_runtime_fail_before_any_output(self):
        project = self.root / "project"
        package = project / "backend/src/monitor"
        package.mkdir(parents=True)
        agent = project / "node_reporter/python/reporter.py"
        agent.parent.mkdir(parents=True)
        agent.write_text("pass\n")
        with patch("project_tools.release.PROJECT", project):
            with self.assertRaisesRegex(ValueError, "运行入口"):
                build(self.root / "bad-release")
            (package / "__init__.py").write_text("")
            (package / "__main__.py").write_text("pass\n")
            agent.unlink()
            agent.symlink_to(PROJECT / "node_reporter/python/reporter.py")
            with self.assertRaisesRegex(ValueError, "符号链接"):
                build(self.root / "bad-release")
        self.assertFalse((self.root / "bad-release").exists())

    def test_frontend_receipt_detects_stale_inputs_and_changed_assets(self):
        frontend = self.root / "frontend"
        shutil.copytree(
            PROJECT / "frontend",
            frontend,
            ignore=shutil.ignore_patterns("node_modules"),
        )
        assets = public_files(frontend / "dist")
        verify_receipt(frontend, assets)
        changed = dict(assets, **{"app.js": assets["app.js"] + b"\nchanged"})
        with self.assertRaisesRegex(ValueError, "已变化"):
            verify_receipt(frontend, changed)
        path = frontend / "src/app.ts"
        path.write_text(path.read_text() + "\n// changed source\n")
        with self.assertRaisesRegex(ValueError, "已变化"):
            verify_receipt(frontend, assets)
        (frontend / "dist/secret.json").write_text('{"secret":"fixture"}')
        with self.assertRaisesRegex(ValueError, "非公开"):
            public_files(frontend / "dist")

    def test_tar_builder_is_stable(self):
        self.assertEqual(
            archive_bytes({"b": b"2", "a": b"1"}), archive_bytes({"a": b"1", "b": b"2"})
        )


if __name__ == "__main__":
    unittest.main()
