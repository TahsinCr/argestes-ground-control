from __future__ import annotations

import threading
import time
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from src.core.events import EventBus
from extensions.camera.publishing import VideoSharePublisher
from extensions.camera.models import VideoFrame, VideoSourceFrame


class VideoPublisherLifecycleTests(unittest.TestCase):
    def test_removed_source_releases_cached_frame_and_timestamp(self) -> None:
        publisher = VideoSharePublisher(EventBus(), source_frames=EventBus())
        frame = VideoFrame(
            width=1,
            height=1,
            data=b"\x00\x00\x00\xff",
            pixel_format="rgba8888",
            stride=4,
        )
        publisher._on_source_frame(VideoSourceFrame("camera-a", frame))

        publisher.remove_source("camera-a")

        self.assertNotIn("camera-a", publisher._latest_source_frames)
        self.assertNotIn("camera-a", publisher._latest_source_frame_at)
        self.assertIsNone(publisher._current_frame(SimpleNamespace(source_id="camera-a")))

    def test_signal_lost_stream_frame_is_opaque_black_at_source_dimensions(self) -> None:
        source = VideoFrame(
            width=2,
            height=1,
            data=b"\xff\x00\x00\xff" * 2,
            pixel_format="rgba8888",
            stride=8,
        )

        blank = VideoSharePublisher._signal_lost_frame(source)

        self.assertEqual((blank.width, blank.height), (2, 1))
        self.assertEqual(blank.pixel_format, "rgba8888")
        self.assertEqual(blank.stride, 8)
        self.assertEqual(blank.data, b"\x00\x00\x00\xff" * 2)

    def test_missing_optional_clock_never_blocks_video_startup(self) -> None:
        publisher = object.__new__(VideoSharePublisher)
        publisher._clock_provider = lambda: None
        publisher._clock_lock = threading.Lock()
        publisher._clock_generation = 0

        started_at = time.monotonic()
        reference, anchor = publisher._sample_clock_reference()

        self.assertIsNone(reference)
        self.assertIsNone(anchor)
        self.assertLess(time.monotonic() - started_at, 0.05)

    def test_clock_provider_change_is_visible_to_running_publishers(self) -> None:
        publisher = object.__new__(VideoSharePublisher)
        publisher._clock_provider = lambda: None
        publisher._clock_lock = threading.Lock()
        publisher._clock_generation = 0
        initial_generation, _ = publisher._clock_provider_snapshot()
        expected_time = time.time()

        publisher.set_clock_provider(lambda: datetime.fromtimestamp(expected_time, UTC))

        generation, provider = publisher._clock_provider_snapshot()
        reference, anchor = publisher._sample_clock_reference(provider)
        self.assertGreater(generation, initial_generation)
        self.assertIsNotNone(reference)
        self.assertIsNotNone(anchor)
        self.assertAlmostEqual(reference.timestamp(), expected_time, delta=0.001)

    def test_recording_finalization_requires_confirmed_eos(self) -> None:
        class FakeGst:
            SECOND = 1

            class FlowReturn:
                OK = 0

            class MessageType:
                EOS = 1
                ERROR = 2

            class State:
                NULL = 0

            class StateChangeReturn:
                FAILURE = 1
                SUCCESS = 0

        class AppSource:
            def emit(self, _signal):
                return FakeGst.FlowReturn.OK

        class Message:
            def __init__(self, message_type):
                self.type = message_type

            def parse_error(self):
                return RuntimeError("muxer failed"), "test"

        class Bus:
            def __init__(self, message):
                self.message = message

            def timed_pop_filtered(self, _timeout, _mask):
                return self.message

        class Pipeline:
            def __init__(self, message):
                self.bus = Bus(message)

            def get_bus(self):
                return self.bus

            def set_state(self, _state):
                return FakeGst.StateChangeReturn.SUCCESS

        results = (
            (Message(FakeGst.MessageType.EOS), "finalized"),
            (None, "eos_timeout"),
            (Message(FakeGst.MessageType.ERROR), "pipeline_error"),
        )
        for message, expected in results:
            with self.subTest(expected=expected):
                result = VideoSharePublisher._finalize_recording(
                    Pipeline(message), AppSource(), FakeGst
                )
                self.assertEqual(result.status, expected)
                self.assertEqual(result.finalized, expected == "finalized")

    def test_minimum_resolution_applies_to_streams_not_local_recordings(self) -> None:
        frame = SimpleNamespace(width=320, height=240)
        recording = SimpleNamespace(output_type="record")
        stream = SimpleNamespace(output_type="stream")

        VideoSharePublisher._validate_minimum_resolution(recording, frame)
        with self.assertRaisesRegex(RuntimeError, "320x240"):
            VideoSharePublisher._validate_minimum_resolution(stream, frame)

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
