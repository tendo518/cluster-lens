"""Standalone config generation, release artifacts and application-owned agent tests."""

import ast
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from monitor import remote_probe
from monitor.config import load_config
from monitor.paths import ROOT
from project_tools.configure import generate
from project_tools.release import build, server_bundle


class ProjectToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "config"
        self.nodes = [{"name": "node-a", "address": "192.0.2.10", "groups": ["gpu"]}]
        self.url = "http://127.0.0.1:8080/api/agent/report"

    def tearDown(self):
        self.temp.cleanup()

    def test_configs_are_valid_private_and_stable_without_inventory(self):
        with patch("subprocess.run", side_effect=AssertionError("No external tools")):
            config = generate(self.directory, self.nodes, self.url)
        self.assertEqual(load_config(self.directory / "server.json")[0], config)
        agent = remote_probe.load_agent_config(
            str(self.directory / "agents/node-a.json")
        )
        self.assertEqual(agent["token"], config["hosts"][0]["agent_token"])
        self.assertNotEqual(agent["token"], config["viewer_token"])
        files = [p for p in self.directory.rglob("*") if p.is_file()]
        before = {p: p.read_bytes() for p in files}
        generate(self.directory, self.nodes, self.url)
        self.assertEqual(before, {p: p.read_bytes() for p in files})
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for path in files:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_node_changes_preserve_surviving_credentials(self):
        before = generate(self.directory, self.nodes, self.url)
        nodes = self.nodes + [{"name": "node-b", "address": "192.0.2.11"}]
        after = generate(self.directory, nodes, self.url)
        self.assertEqual(after["viewer_token"], before["viewer_token"])
        self.assertEqual(after["hosts"][0], before["hosts"][0])
        self.assertNotEqual(
            after["hosts"][1]["agent_token"], after["hosts"][0]["agent_token"]
        )
        generate(self.directory, self.nodes, self.url)
        self.assertFalse((self.directory / "agents/node-b.json").exists())

    def test_bad_input_never_replaces_good_config(self):
        generate(self.directory, self.nodes, self.url)
        before = {
            str(p): p.read_bytes() for p in self.directory.rglob("*") if p.is_file()
        }
        for nodes, url, options in (
            ([], self.url, {}),
            ([self.nodes[0], self.nodes[0]], self.url, {}),
            ([{**self.nodes[0], "name": "../escape"}], self.url, {}),
            ([{**self.nodes[0], "ssh_key": "PRIVATE"}], self.url, {}),
            (self.nodes, "https://user:secret@example.org/api/agent/report", {}),
            (self.nodes, "https://example.org:99999/api/agent/report", {}),
            (self.nodes, self.url + "?token=secret", {}),
            (self.nodes, self.url, {"interval": True}),
            (self.nodes, self.url, {"stale_after": 10}),
        ):
            with (
                self.subTest(nodes=nodes, url=url, options=options),
                self.assertRaises(ValueError),
            ):
                generate(self.directory, nodes, url, **options)
            self.assertEqual(
                before,
                {
                    str(p): p.read_bytes()
                    for p in self.directory.rglob("*")
                    if p.is_file()
                },
            )

    def test_invalid_existing_credentials_are_not_silently_rotated(self):
        config = generate(self.directory, self.nodes, self.url)
        config["hosts"][0]["agent_token"] = config["viewer_token"]
        path = self.directory / "server.json"
        path.write_text(json.dumps(config))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            generate(self.directory, self.nodes, self.url)
        self.assertEqual(path.read_bytes(), before)

    def test_release_is_deterministic_self_contained_and_has_checksums(self):
        output = Path(self.temp.name) / "dist"
        with patch(
            "subprocess.run", side_effect=AssertionError("No external build tool")
        ):
            manifest = build(output)
        self.assertEqual((output / "server.tar.gz").read_bytes(), server_bundle())
        for name, metadata in manifest["files"].items():
            self.assertEqual(
                metadata["sha256"],
                hashlib.sha256((output / name).read_bytes()).hexdigest(),
            )
        ast.parse((output / "agent.py").read_text(), feature_version=(3, 6))
        with tarfile.open(output / "server.tar.gz") as archive:
            names = archive.getnames()
            self.assertIn("monitor/static/app.js", names)
            self.assertIn("monitor/__main__.py", names)
            for member in archive.getmembers():
                self.assertTrue(member.isfile())
                self.assertEqual(member.mtime, 0)
                self.assertTrue(member.name.startswith("monitor/"))
                self.assertFalse(
                    set(Path(member.name).parts)
                    & {
                        "project_tools",
                        "tests",
                        "docs",
                        "config",
                        "data",
                        "__pycache__",
                    }
                )
                if member.name.endswith(".py"):
                    ast.parse(
                        archive.extractfile(member).read().decode(),
                        feature_version=(3, 9),
                    )
            self.assertEqual(
                archive.extractfile("monitor/remote_probe.py").read(),
                (output / "agent.py").read_bytes(),
            )

    def test_release_does_not_follow_links_or_package_local_secrets(self):
        project = Path(self.temp.name) / "project"
        package = project / "monitor"
        (package / "static").mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "static/index.html").write_text("<!doctype html>")
        (package / "static/credentials.json").write_text("SECRET")
        (package / "static/.env").write_text("SECRET")
        with patch("project_tools.release.PROJECT", project):
            with tarfile.open(fileobj=io.BytesIO(server_bundle())) as archive:
                self.assertEqual(
                    archive.getnames(),
                    ["monitor/__init__.py", "monitor/static/index.html"],
                )
            (package / "static/linked.js").symlink_to(
                package / "static/credentials.json"
            )
            with self.assertRaises(ValueError):
                server_bundle()

    def test_cli_retains_config_parameters_and_does_not_print_tokens(self):
        nodes = Path(self.temp.name) / "nodes.json"
        nodes.write_text(json.dumps(self.nodes))
        command = [
            sys.executable,
            "-S",
            "-m",
            "project_tools",
            "configure",
            "--nodes",
            str(nodes),
            "--output",
            str(self.directory),
            "--url",
            self.url,
        ]
        first = subprocess.run(
            command + ["--interval", "60", "--stale-after", "190"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        before = (self.directory / "server.json").read_bytes()
        second = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, check=False
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.directory / "server.json").read_bytes(), before)
        config = json.loads(before)
        self.assertNotIn(
            config["viewer_token"],
            first.stdout + first.stderr + second.stdout + second.stderr,
        )

    def test_agent_rejects_invalid_config_and_post_redirects(self):
        followed = []

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "/trap")
                self.end_headers()

            def do_GET(self):
                followed.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            generate(
                self.directory,
                self.nodes,
                f"http://127.0.0.1:{server.server_port}/api/agent/report",
            )
            config = remote_probe.load_agent_config(
                str(self.directory / "agents/node-a.json")
            )
            with self.assertRaises(HTTPError) as error:
                remote_probe.send_report(config, {"version": 1, "host": "node-a"})
            self.assertEqual(error.exception.code, 302)
            self.assertEqual(followed, [])
            config["token"] = "bad\nsecret"
            path = self.directory / "invalid.json"
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                remote_probe.load_agent_config(str(path))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
