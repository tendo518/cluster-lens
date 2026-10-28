"""Single-process Uvicorn host and a small synchronous embedding/test facade.

There is no stdlib HTTP handler or custom wire parser. Uvicorn/h11 owns HTTP
framing, connection backpressure, disconnects, and the request-drain barrier.
"""

import asyncio
import socket
import threading
from contextlib import contextmanager

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from .api import create_app
from .api.app import CSP

__all__ = ["CSP", "MonitorServer"]


class _QuietH11Protocol(H11Protocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Uvicorn 0.54 checks logger.hasHandlers(), not config.access_log, when
        # opening a connection. Host applications may attach handlers later.
        # Disable request-target logging even then; never alter HTTP parsing.
        self.access_log = False


class _Server(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The CLI owns signals; embedded servers must not replace host handlers.
        # Repeated termination signals still drain writes, never force-cancel them.
        yield

    async def serve(self, sockets=None):
        try:
            await super().serve(sockets=sockets)
        finally:
            # Uvicorn normally performs this itself. Also clean up if listener
            # startup or the main loop raises after lifespan startup succeeded.
            lifespan = getattr(self, "lifespan", None)
            if lifespan is not None and not lifespan.shutdown_event.is_set():
                if hasattr(self, "servers"):
                    await self.shutdown(sockets=sockets)
                else:
                    await lifespan.shutdown()
        if self.lifespan.startup_failed or self.lifespan.shutdown_failed:
            raise RuntimeError("监控服务生命周期失败")


class MonitorServer:
    def __init__(
        self, address, service, token=None, ui=None, *, runtime=None, assets=None
    ):
        self.app = create_app(service, token, ui, runtime=runtime, assets=assets)
        self.config = uvicorn.Config(
            self.app,
            host=address[0],
            port=address[1],
            workers=1,
            loop="asyncio",
            http=_QuietH11Protocol,
            ws="none",
            lifespan="on",
            proxy_headers=False,
            access_log=False,
            log_config=None,
            server_header=False,
            timeout_graceful_shutdown=None,
            timeout_keep_alive=5,
            backlog=32,
        )
        self.config.load()
        self._server = _Server(self.config)
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._done.set()
        self._thread = None
        self._used = False
        self._closed = False
        family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.socket = socket.socket(family, socket.SOCK_STREAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(address)
            self.socket.listen(32)
            self.socket.setblocking(False)
            self.server_address = self.socket.getsockname()
            self.server_port = self.server_address[1]
        except BaseException:
            self.socket.close()
            raise

    def serve_forever(self, poll_interval=None):
        # poll_interval is retained solely for legacy embedders; Uvicorn owns its
        # scheduling. A server/runtime instance is deliberately single-use.
        with self._lock:
            if self._used or self._closed:
                raise RuntimeError("监控服务器不能再次启动")
            self._used = True
            self._thread = threading.current_thread()
            self._done.clear()
        try:
            asyncio.run(self._server.serve(sockets=[self.socket]))
        finally:
            self.socket.close()
            self._done.set()

    def request_shutdown(self):
        """Nonblocking signal-safe intent; the serving loop performs the drain."""
        self._server.should_exit = True

    def shutdown(self):
        self.request_shutdown()
        if threading.current_thread() is not self._thread:
            self._done.wait()

    def server_close(self):
        self.shutdown()
        with self._lock:
            self._closed = True
            self.socket.close()
