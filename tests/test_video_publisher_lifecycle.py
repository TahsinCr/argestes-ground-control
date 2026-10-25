from __future__ import annotations

import threading
import time
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from src.core.events import EventBus
from extensions.camera.publishing import VideoSharePublisher
from extensions.camera.models import VideoFrame, VideoShareConfig, VideoSourceFrame
from src.services.logging_service import LoggingService


class VideoPublisherLifecycleTests(unittest.TestCase):
    def test_pipeline_errors_redact_uri_credentials_and_tokens(self) -> None:
        secret_uri = "rtsp://operator:password@example.test/live?token=secret-token"
        logs = LoggingService()
        publisher = VideoSharePublisher(EventBus(), logs.child("VIDEO"))
        config = VideoShareConfig(
            identifier="camera-output",
            name="Camera output",
            endpoint=secret_uri,
            connection_type="rtsp",
            codec="h264",
        )

        with patch.object(
            publisher,
            "_wait_for_frame",
            side_effect=RuntimeError(f"GStreamer failed to open {secret_uri}"),
        ):
            publisher._run_target(config, threading.Event())

        messages = "\n".join(entry.message for entry in logs.history)
        self.assertNotIn("operator", messages)
        self.assertNotIn("password", messages)
        self.assertNotIn("secret-token", messages)
        self.assertIn("[redacted]", messages)

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

    def test_panel_source_never_falls_back_to_a_different_camera(self) -> None:
        publisher = VideoSharePublisher(EventBus(), source_frames=EventBus())
        selected = {"source": "camera-a"}
        publisher.set_active_source_provider(lambda: selected["source"])
        frame_a = VideoFrame(1, 1, b"\x01\x02\x03\xff", "rgba8888", 4)
        frame_b = VideoFrame(1, 1, b"\x04\x05\x06\xff", "rgba8888", 4)
        config = SimpleNamespace(source_id="panel")
        publisher._on_frame(frame_a)
        publisher._on_source_frame(VideoSourceFrame("camera-a", frame_a))

        selected["source"] = "camera-b"

        self.assertIsNone(publisher._current_frame(config))
        self.assertFalse(publisher._frame_is_fresh(config))

        publisher._on_source_frame(VideoSourceFrame("camera-b", frame_b))

        self.assertIs(publisher._current_frame(config), frame_b)
        self.assertTrue(publisher._frame_is_fresh(config))

    def test_h265_rtmp_target_is_rejected_before_gstreamer_start(self) -> None:
        config = VideoShareConfig(
            identifier="h265-rtmp",
            name="Unsupported target",
            endpoint="rtmp://example.test/live",
            connection_type="rtmp",
            codec="h265",
        )

        with self.assertRaisesRegex(ValueError, "H.264"):
            config.validate_output_compatibility()

    def test_stream_accepts_320x240_source_and_builds_sink_without_uri_interpolation(self) -> None:
        publisher = VideoSharePublisher(EventBus())
        frame = VideoFrame(320, 240, bytes(320 * 240 * 4), "rgba8888", 320 * 4)
        endpoint = "rtmp://example.test/live?token=one%20two"
        config = VideoShareConfig(
            identifier="small-stream",
            name="Low bandwidth",
            endpoint=endpoint,
            connection_type="rtmp",
            codec="h264",
        )

        description = publisher._pipeline_description(config, frame)

        self.assertIn("width=320,height=240", description)
        self.assertIn("name=ags_video_output", description)
        self.assertNotIn(endpoint, description)

    def test_output_uri_is_set_as_a_property_and_rejects_pipeline_syntax(self) -> None:
        class Sink:
            def __init__(self) -> None:
                self.properties = {}

            def set_property(self, name, value) -> None:
                self.properties[name] = value

        class Pipeline:
            def __init__(self) -> None:
                self.sink = Sink()

            def get_by_name(self, name):
                return self.sink if name == "ags_video_output" else None

        endpoint = "rtmp://example.test/live?token=one%20two"
        config = VideoShareConfig(
            identifier="safe-target",
            name="Safe target",
            endpoint=endpoint,
            connection_type="rtmp",
            codec="h264",
        )
        pipeline = Pipeline()

        VideoSharePublisher._configure_sink(pipeline, config)

        self.assertEqual(pipeline.sink.properties["location"], endpoint)
        unsafe = VideoShareConfig(
            identifier="unsafe-target",
            name="Unsafe target",
            endpoint='rtmp://example.test/live" ! fakesink name=owned',
            connection_type="rtmp",
            codec="h264",
        )
        with self.assertRaisesRegex(ValueError, "geçersiz"):
            VideoSharePublisher._configure_sink(Pipeline(), unsafe)

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

    def test_stream_pipeline_accepts_320x240_source(self) -> None:
        class FakeGst:
            SECOND = 1_000_000_000

            class State:
                PLAYING = 1
                NULL = 2

            class StateChangeReturn:
                FAILURE = -1
                SUCCESS = 0

            class FlowReturn:
                OK = 0

            class Buffer:
                @staticmethod
                def new_wrapped(data):
                    return SimpleNamespace(data=data, pts=None, dts=None, duration=None)

            def __init__(self) -> None:
                self.description = ""

            def parse_launch(self, description):
                self.description = description
                appsrc = SimpleNamespace(emit=self._push_buffer)
                pipeline = self
                self.elements = {
                    "yki_share_src": appsrc,
                    "yki_share_clock": None,
                    "ags_video_output": SimpleNamespace(set_property=lambda *_: None),
                }
                return pipeline

            def _push_buffer(self, _signal, _buffer):
                stop_event.set()
                return self.FlowReturn.OK

            def get_by_name(self, name):
                return self.elements.get(name)

            def set_state(self, _state):
                return self.StateChangeReturn.SUCCESS

        publisher = VideoSharePublisher(EventBus(), source_frames=EventBus())
        frame = VideoFrame(320, 240, bytes(320 * 240 * 4), "rgba8888", 320 * 4)
        config = VideoShareConfig(
            identifier="small-stream",
            name="Low bandwidth",
            endpoint="127.0.0.1:14550",
            connection_type="udp",
            codec="h264",
            source_id="camera-a",
            timestamp_enabled=False,
        )
        stop_event = publisher._stop
        gst = FakeGst()
        publisher._on_source_frame(VideoSourceFrame("camera-a", frame))
        publisher._wait_for_frame = lambda *_args: frame
        publisher._load_gstreamer = lambda: gst
        publisher._encoder_chain = lambda *_args: ("x264enc", "test encoder")

        publisher._run_target(config, stop_event)

        self.assertIn("width=320,height=240", gst.description)

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
