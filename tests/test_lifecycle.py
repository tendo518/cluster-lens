"""Maintenance scheduling and cleanup without a listening HTTP server."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from monitor.demo import demo_hosts
from monitor.lifecycle import MonitorRuntime


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.service = SimpleNamespace(
            demo=False,
            interval=30,
            collecting=False,
            tick=Mock(),
            prune=Mock(),
            accept=Mock(),
            hosts=demo_hosts(),
        )
        self.runtime = MonitorRuntime(self.service)
        self.addCleanup(self.runtime.stop)

    def test_service_maintenance_and_pruning_have_separate_schedules(self):
        self.runtime.step(1000)
        self.runtime.step(1005)
        self.runtime.step(4600)
        self.assertEqual(self.service.tick.call_count, 3)
        self.service.prune.assert_called_once_with()
        self.runtime.step(4601)
        self.assertEqual(self.service.prune.call_count, 2)
        self.assertIsNone(self.runtime.thread, "a deterministic step starts no worker")

    def test_failed_prune_is_retried_and_does_not_advance_schedule(self):
        self.service.prune.side_effect = [OSError("disk unavailable"), None]
        with self.assertRaises(OSError):
            self.runtime.step(1000)
        self.assertIsNone(self.runtime.last_prune)
        self.runtime.step(1005)
        self.assertEqual(self.runtime.last_prune, 1005)
        self.assertEqual(self.service.prune.call_count, 2)

    def test_demo_generation_is_local_and_runs_at_the_configured_interval(self):
        self.service.demo = True
        self.service.accept.side_effect = lambda _: self.assertTrue(
            self.service.collecting
        )
        with patch(
            "subprocess.run", side_effect=AssertionError("No demo subprocesses")
        ):
            self.runtime.step(1000)
            self.assertEqual(self.service.accept.call_count, 8)
            self.assertFalse(self.service.collecting)
            self.runtime.step(1029)
            self.assertEqual(self.service.accept.call_count, 8)
            self.runtime.step(1030)
            self.assertEqual(self.service.accept.call_count, 16)
        self.service.tick.assert_not_called()

    def test_failed_demo_resets_flag_and_is_retryable(self):
        self.service.demo = True
        self.service.accept.side_effect = OSError("write failed")
        with self.assertRaises(OSError):
            self.runtime.step(1000)
        self.assertFalse(self.service.collecting)
        self.assertIsNone(self.runtime.next_demo)
        self.service.accept.side_effect = None
        self.runtime.step(1005)
        self.assertEqual(self.runtime.next_demo, 1035)

    def test_background_error_does_not_end_maintenance(self):
        self.service.tick.side_effect = [OSError("temporary failure"), None]
        event = Mock()
        event.is_set.side_effect = [False, False, True]
        self.runtime.stop_event = event
        with self.assertLogs("monitor.lifecycle", level="ERROR") as logs:
            self.runtime.run()
        self.assertEqual(self.service.tick.call_count, 2)
        self.assertEqual(event.wait.call_count, 2)
        self.assertIn("temporary failure", "\n".join(logs.output))
        self.service.prune.assert_called_once_with()

    def test_start_is_idempotent_and_stop_joins_inflight_maintenance(self):
        entered, release, stopped = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.addCleanup(release.set)

        def step():
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test failed to release maintenance")

        def stop():
            self.runtime.stop()
            stopped.set()

        with patch.object(self.runtime, "step", side_effect=step):
            self.runtime.start()
            self.assertTrue(entered.wait(2))
            worker = self.runtime.thread
            self.runtime.start()
            self.assertIs(self.runtime.thread, worker)
            stopper = threading.Thread(target=stop)
            stopper.start()
            try:
                self.assertTrue(self.runtime.stop_event.wait(2))
                self.assertFalse(
                    stopped.is_set(), "stop cannot abandon a pending write"
                )
            finally:
                release.set()
                stopper.join(timeout=2)
            self.assertTrue(stopped.is_set())
            self.assertFalse(worker.is_alive())
            self.runtime.stop()
            with self.assertRaises(RuntimeError):
                self.runtime.start()

    def test_failed_worker_start_remains_safe_to_stop(self):
        with (
            patch.object(
                threading.Thread,
                "start",
                side_effect=RuntimeError("cannot start new thread"),
            ),
            self.assertRaisesRegex(RuntimeError, "cannot start new thread"),
        ):
            self.runtime.start()
        self.assertIsNone(self.runtime.thread)
        self.runtime.stop()

    def test_stop_before_start_is_safe(self):
        self.runtime.stop()
        self.runtime.stop()
        with self.assertRaises(RuntimeError):
            self.runtime.start()
        self.assertIsNone(self.runtime.thread)


if __name__ == "__main__":
    unittest.main()
