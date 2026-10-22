from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from src.core.events import EventBus
from extensions.camera.publishing import VideoSharePublisher


class VideoPublisherLifecycleTests(unittest.TestCase):
    def test_reconfigure_waits_for_old_pipeline_finalization_without_blocking(self) -> None:
        publisher = VideoSharePublisher(EventBus())
        old_started = threading.Event()
        new_started = threading.Event()
        lock = threading.Lock()
        active = 0
        max_active = 0

        def run_target(config, stop_event) -> None:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            if config.name == "old":
                old_started.set()
                stop_event.wait()
                # Simulate muxer EOS finalization after the stop request.
                time.sleep(0.15)
            else:
                new_started.set()
                stop_event.wait()
            with lock:
                active -= 1
            publisher._target_finished(
                config.identifier,
                threading.current_thread(),
                stop_event,
            )

        publisher._run_target = run_target
        old_config = SimpleNamespace(
            identifier="camera-1", name="old", enabled=True, output_type="record"
        )
        new_config = SimpleNamespace(
            identifier="camera-1", name="new", enabled=True, output_type="record"
        )
        publisher.configure((old_config,))
        self.assertTrue(old_started.wait(timeout=1.0))

        started_at = time.monotonic()
        publisher.configure((new_config,))
        self.assertLess(time.monotonic() - started_at, 0.1)
        self.assertTrue(new_started.wait(timeout=1.0))
        self.assertEqual(max_active, 1)

        self.assertTrue(publisher.close())

    def test_close_reports_worker_that_exceeds_shutdown_deadline(self) -> None:
        publisher = VideoSharePublisher(EventBus())
        release = threading.Event()
        worker = threading.Thread(target=release.wait, name="blocked-video-worker")
        publisher._workers["camera-stuck"] = worker
        worker.start()

        try:
            self.assertFalse(publisher.close(timeout_s=0.01))
            self.assertEqual(publisher._states["camera-stuck"].status, "error")
        finally:
            release.set()
            worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
