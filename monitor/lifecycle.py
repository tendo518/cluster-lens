"""Background maintenance lifecycle, separate from monitoring use cases and HTTP."""

import logging
import threading
import time

LOG = logging.getLogger(__name__)


class MonitorRuntime:
    def __init__(self, service):
        self.service = service
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.last_prune = None
        self.next_demo = None

    def start(self):
        with self.lock:
            if self.stop_event.is_set():
                raise RuntimeError("已关闭的监控运行实例不能再次启动")
            if self.thread is not None:
                return
            thread = threading.Thread(
                target=self.run, daemon=True, name="monitor-maintenance"
            )
            thread.start()
            self.thread = thread

    def stop(self):
        with self.lock:
            self.stop_event.set()
            thread = self.thread
        if thread is not None:
            # Do not report shutdown complete while a maintenance write is running.
            thread.join()

    def step(self, now=None):
        """One deterministic maintenance cycle; scheduling is owned by run()."""
        now = time.time() if now is None else now
        service = self.service
        try:
            if service.demo and (self.next_demo is None or now >= self.next_demo):
                from .demo import demo_snapshot

                service.collecting = True
                for host in service.hosts:
                    service.accept(demo_snapshot(host, now))
                self.next_demo = now + service.interval
            elif not service.demo:
                service.tick(now)
            if self.last_prune is None or now - self.last_prune > 3600:
                service.prune()
                self.last_prune = now
        finally:
            service.collecting = False

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.step()
            except Exception:
                LOG.exception("处理节点上报 / 历史维护失败")
            self.stop_event.wait(min(5, self.service.interval))
