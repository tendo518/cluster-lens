"""Public UI configuration is self-contained, safe, and independent of deployment."""

import base64
import json
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from monitor.config import parse_config
from monitor.models import Host
from monitor.server import MonitorServer
from monitor.service import MonitoringService
from monitor.store import Store
from monitor.ui import DEFAULTS, icon_bytes, load_ui, parse_ui
from project_tools.configure import generate


def png():
    def chunk(name, data):
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x30\x60\x90\xff"))
        + chunk(b"IEND", b"")
    )


class UIConfigTests(unittest.TestCase):
    def test_defaults_are_generic_and_do_not_mutate_input(self):
        value = {"name": "Other Site", "palette": "purple"}
        parsed = parse_ui(value)
        self.assertEqual(parsed["title"], "ClusterLens · 服务器监控")
        self.assertEqual(parsed["palette"], "purple")
        self.assertEqual(value, {"name": "Other Site", "palette": "purple"})
        self.assertEqual(parse_ui({}), DEFAULTS)

    def test_invalid_fields_fail_closed(self):
        for value in (
            None,
            [],
            {"titel": "typo"},
            {"name": ""},
            {"mark": "LONG"},
            {"title": "a\nb"},
            {"theme": []},
            {"palette": "red"},
            {"icon": "https://example.com/tracker.png"},
            {"icon": "data:image/svg+xml,<svg/>"},
            {"footer": False},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_ui(value)

    def test_icon_file_is_resolved_relative_to_ui_file_and_embedded(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "logo.png").write_bytes(png())
            path = directory / "ui.json"
            path.write_text(json.dumps({"name": "Example", "icon_file": "logo.png"}))
            ui = load_ui(path)
            self.assertNotIn("icon_file", ui)
            self.assertEqual(icon_bytes(ui["icon"]), png())
            self.assertEqual(parse_ui(ui), ui)
            path.write_text('{"icon_file":"logo.png","icon":null}')
            with self.assertRaises(ValueError):
                load_ui(path)

    def test_icon_limits_and_format_are_checked(self):
        oversized = bytearray(png())
        oversized[16:20] = struct.pack(">I", 1025)
        for data in (
            b"<script>alert(1)</script>",
            png()[:-1],
            bytes(oversized),
            png() * 2000,
        ):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                icon_bytes("data:image/png;base64," + base64.b64encode(data).decode())
        with self.assertRaises(ValueError):
            icon_bytes("data:image/png;base64,!!!")

    def test_generation_preserves_credentials_and_ui_when_omitted_and_resets_explicitly(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            nodes = [{"name": "node-a", "address": "192.0.2.1"}]
            url = "http://192.0.2.2:8080/api/agent/report"
            first = generate(
                temporary, nodes, url, ui={"name": "Example", "palette": "teal"}
            )
            self.assertEqual(generate(temporary, nodes, url), first)
            updated = generate(temporary, nodes, url, ui={})
            self.assertEqual(updated["ui"], DEFAULTS)
            self.assertEqual(updated["viewer_token"], first["viewer_token"])
            self.assertEqual(updated["hosts"], first["hosts"])
            before = (Path(temporary) / "server.json").read_bytes()
            with self.assertRaises(ValueError):
                generate(temporary, nodes, url, ui={"name": False})
            self.assertEqual((Path(temporary) / "server.json").read_bytes(), before)
            updated["ui"] = {"palette": "unknown"}
            with self.assertRaises(ValueError):
                parse_config(updated)


class UIHTTPTests(unittest.TestCase):
    def test_html_is_escaped_before_login_and_only_safe_icon_is_served(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = MonitoringService(
                [Host("node-a", "192.0.2.1")],
                Store(Path(temporary) / "history.sqlite3"),
            )
            ui = {
                "title": "</title><script>window.injected=1</script>",
                "name": 'Example "<Site>"',
                "mark": "<&",
                "palette": "blue",
                "icon": "data:image/png;base64," + base64.b64encode(png()).decode(),
            }
            server = MonitorServer(
                ("127.0.0.1", 0), service, "private-viewer-token", ui=ui
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                for path in ("/", "/index.html"):
                    with urlopen(base + path, timeout=5) as response:
                        page = response.read().decode()
                        self.assertIn("&lt;/title&gt;&lt;script&gt;", page)
                        self.assertIn("Example &quot;&lt;Site&gt;&quot;", page)
                        self.assertNotIn("<script>window.injected", page)
                        self.assertNotIn("private-viewer-token", page)
                        self.assertNotIn("{{ui.", page)
                        self.assertIn('data-default-palette="blue"', page)
                        self.assertIn('href="/brand-icon.png" type="image/png"', page)
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                with urlopen(base + "/brand-icon.png", timeout=5) as response:
                    self.assertEqual(response.read(), png())
                    self.assertEqual(response.headers["Content-Type"], "image/png")
                    self.assertEqual(
                        response.headers["X-Content-Type-Options"], "nosniff"
                    )
                with urlopen(base + "/favicon.svg", timeout=5) as response:
                    self.assertIn(b"&lt;&amp;", response.read())
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + "/api/overview"), timeout=5)
                self.assertEqual(error.exception.code, 401)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_missing_custom_icon_is_not_a_filesystem_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = MonitoringService(
                [Host("node-a", "192.0.2.1")],
                Store(Path(temporary) / "history.sqlite3"),
            )
            server = MonitorServer(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        f"http://127.0.0.1:{server.server_port}/brand-icon.png",
                        timeout=5,
                    )
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
