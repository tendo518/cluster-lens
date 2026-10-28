"""Application-owned configuration/CLI: no inventory, SSH, runtime or real credentials."""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from _agent import remote_probe
from monitor.config import load_config
from project_tools.configure import generate

ROOT = Path(__file__).resolve().parents[1]


class ProjectToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "config"
        self.nodes = [{"name": "node-a", "address": "192.0.2.10", "groups": ["gpu"]}]
        self.url = "http://127.0.0.1:8080/api/agent/report"

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

    def test_configuration_versions_are_integers_without_coercion(self):
        generate(self.directory, self.nodes, self.url)
        for path, load in (
            (self.directory / "server.json", load_config),
            (self.directory / "agents/node-a.json", remote_probe.load_agent_config),
        ):
            original = json.loads(path.read_text())
            for version in (True, False, 1.0, "1", None, 0, 2, [], {}):
                with self.subTest(file=path.name, version=version):
                    path.write_text(json.dumps({**original, "version": version}))
                    with self.assertRaisesRegex(ValueError, "version=1"):
                        load(path)
            path.write_text(json.dumps(original))
            load(path)

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

    def test_describe_requires_prepared_environment_and_declares_v2(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "project_tools", "describe"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "contract_version": 2,
                "application": "hqlab-monitor",
                "config_version": 1,
                "report_version": 1,
                "release_formats": [2],
                "agents": ["python"],
                "prepared_environment_required": True,
            },
        )
        self.assertFalse(self.directory.exists())

    def test_cli_retains_config_parameters_and_does_not_print_tokens(self):
        nodes = Path(self.temp.name) / "nodes.json"
        nodes.write_text(json.dumps(self.nodes))
        command = [
            sys.executable,
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
        self.assertNotIn(
            json.loads(before)["viewer_token"],
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
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/api/agent/report"
        generate(self.directory, self.nodes, url)
        path = self.directory / "agents/node-a.json"
        config = remote_probe.load_agent_config(path)
        with self.assertRaises(HTTPError) as error:
            remote_probe.send_report(config, {"version": 1})
        self.assertEqual(error.exception.code, 302)
        error.exception.close()
        self.assertEqual(followed, [])
        for changes in (
            {"version": 2},
            {"token": "short"},
            {"interval": True},
            {"timeout": 31},
            {"url": url + "?token=secret"},
            {"url": "https://user:secret@example.org/api/agent/report"},
        ):
            path.write_text(json.dumps({**config, **changes}))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                remote_probe.load_agent_config(path)


if __name__ == "__main__":
    unittest.main()
