"""Installed resource boundaries and authenticated installer identity."""

import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.api.app import runtime_identity
from monitor.assets import MAX_ASSET_BYTES, Assets
from monitor.models import Host
from monitor.server import MonitorServer
from monitor.service import MonitoringService
from monitor.store import Store

VIEWER = "resources-private-viewer-token"
AGENT = "resources-private-agent-token"


class ResourceReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = MonitoringService(
            [Host("alpha", "192.0.2.1")],
            Store(self.root / "history.sqlite3"),
            credentials={"alpha": AGENT},
        )

    def start(self, assets=None, token=VIEWER):
        server = MonitorServer(("127.0.0.1", 0), self.service, token, assets=assets)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        return server

    def get(self, server, path, token=None, method="GET", headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        try:
            fields = {} if token is None else {"Authorization": "Bearer " + token}
            fields.update(headers or {})
            connection.request(method, path, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def fixture_assets(self):
        assets = self.root / "public"
        assets.mkdir()
        for name in (
            "index.html",
            "app.js",
            "appearance.js",
            "styles.css",
            "palettes.css",
        ):
            (assets / name).write_bytes(Assets().read(name))
        (assets / "assets").mkdir()
        (assets / "assets/chunk-123.js").write_text("export const ready = true;")
        return assets

    def test_runtime_is_authenticated_minimal_and_bound_to_process_release(self):
        release_id = "a" * 64
        with patch.dict(os.environ, {"CLUSTERLENS_RELEASE_ID": release_id}):
            server = self.start()
        # Readiness is fixed at app creation, not controlled by request metadata.
        status, _, body = self.get(
            server,
            "/api/runtime",
            VIEWER,
            headers={"X-Forwarded-For": "192.0.2.99", "X-Forwarded-Proto": "https"},
        )
        self.assertEqual(status, 200)
        expected = {
            "application": "hqlab-monitor",
            "version": "0.2.0",
            "pid": os.getpid(),
            "release_id": release_id,
        }
        self.assertEqual(json.loads(body), expected)
        for token in (None, AGENT, "invalid"):
            with self.subTest(token=token):
                self.assertEqual(self.get(server, "/api/runtime", token)[0], 401)
        status, headers, head = self.get(server, "/api/runtime", VIEWER, method="HEAD")
        self.assertEqual((status, head), (200, b""))
        self.assertEqual(int(headers["content-length"]), len(body))
        self.assertEqual(json.loads(self.get(server, "/healthz")[2]), {"status": "ok"})
        self.assertNotIn(str(self.root), body.decode())
        self.assertNotIn(VIEWER, body.decode())
        self.assertNotIn(AGENT, body.decode())

    def test_release_id_is_null_when_unset_and_invalid_values_fail_before_bind(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(runtime_identity()["release_id"])
        for value in ("", "a" * 63, "A" * 64, "g" * 64, "token or/path"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"CLUSTERLENS_RELEASE_ID": value}),
                patch.object(
                    socket,
                    "socket",
                    side_effect=AssertionError("must validate before bind"),
                ),
                self.assertRaisesRegex(ValueError, "CLUSTERLENS_RELEASE_ID") as error,
            ):
                MonitorServer(("127.0.0.1", 0), self.service)
            self.assertNotIn("token or/path", str(error.exception))
        server = self.start(token=None)
        self.assertEqual(self.get(server, "/api/overview")[0], 200)
        self.assertEqual(self.get(server, "/api/runtime")[0], 401)

    def test_docs_openapi_and_schema_routes_are_never_public(self):
        server = self.start()
        for path in (
            "/docs",
            "/redoc",
            "/openapi.json",
            "/docs/oauth2-redirect",
            "/schemas/agent-report.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.get(server, path, VIEWER)[0], 404)
        self.assertIsNone(server.app.docs_url)
        self.assertIsNone(server.app.openapi_url)

    def test_explicit_assets_serve_generated_chunks_not_arbitrary_files_or_links(self):
        assets = self.fixture_assets()
        secret = self.root / "secret.js"
        secret.write_text("DO_NOT_SERVE")
        (assets / "assets/secret.json").write_text("DO_NOT_SERVE")
        (assets / "assets/.env").write_text("DO_NOT_SERVE")
        (assets / "private.js").write_text("DO_NOT_SERVE")
        (assets / "assets/link.js").symlink_to(secret)
        (assets / "assets/directory").symlink_to(self.root, target_is_directory=True)
        (assets / "assets/oversized.js").write_bytes(b"x" * (MAX_ASSET_BYTES + 1))
        os.mkfifo(assets / "assets/pipe.js")
        server = self.start(assets=assets)
        self.assertEqual(
            self.get(server, "/assets/chunk-123.js")[2], b"export const ready = true;"
        )
        for path in (
            "/private.js",
            "/assets/secret.json",
            "/assets/.env",
            "/assets/link.js",
            "/assets/directory/secret.js",
            "/assets/oversized.js",
            "/assets/pipe.js",
            "/assets/../secret.js",
            "/assets/%2e%2e/secret.js",
            "/assets//chunk-123.js",
            "/assets/chunk-123.js/",
            "/%61pp.js",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.get(server, path)[0], 404)
        _, _, body = self.get(server, "/app.js")
        status, headers, head = self.get(server, "/app.js", method="HEAD")
        self.assertEqual((status, head), (200, b""))
        self.assertEqual(int(headers["content-length"]), len(body))

    def test_missing_or_linked_required_assets_fail_before_socket_allocation(self):
        assets = self.fixture_assets()
        (assets / "app.js").unlink()
        with (
            patch.object(socket, "socket", side_effect=AssertionError("no bind")),
            self.assertRaises(OSError),
        ):
            MonitorServer(("127.0.0.1", 0), self.service, assets=assets)
        (assets / "app.js").symlink_to(assets / "appearance.js")
        with self.assertRaises(OSError):
            Assets(assets).validate()
        link = self.root / "linked"
        link.symlink_to(assets, target_is_directory=True)
        with self.assertRaises(OSError):
            Assets(link).read("index.html")

    def test_disabled_access_logs_never_log_query_or_authorization(self):
        server = self.start()
        with self.assertNoLogs("uvicorn.access", level="DEBUG"):
            self.assertEqual(
                self.get(server, "/api/overview?token=" + VIEWER, AGENT)[0], 401
            )


if __name__ == "__main__":
    unittest.main()
