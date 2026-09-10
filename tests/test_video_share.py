from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.events import EventBus
from src.models.video import VideoFrame, VideoShareConfig, VideoSourceFrame
from src.services.video_share import VideoSharePublisher


class VideoSharePublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frames = EventBus[VideoFrame]()
        self.publisher = VideoSharePublisher(self.frames)

    def tearDown(self) -> None:
        self.publisher.close()

    @staticmethod
    def _config() -> VideoShareConfig:
        return VideoShareConfig(
            identifier="share-a",
            name="Share A",
            endpoint="239.1.2.3:10021",
            timestamp_enabled=False,
        )

    def test_reconfiguration_keeps_old_workers_cancelled(self) -> None:
        self.publisher.configure((self._config(),))
        first_generation = self.publisher._stop

        self.publisher.configure(())

        self.assertTrue(first_generation.is_set())
        self.assertIsNot(first_generation, self.publisher._stop)
        self.assertFalse(self.publisher._stop.is_set())

    def test_pipeline_drops_stale_frames_instead_of_blocking_the_publisher(self) -> None:
        description = self.publisher._pipeline_description(
            self._config(),
            VideoFrame(width=640, height=480, data=bytes(640 * 480 * 4)),
        )

        self.assertIn("block=false", description)
        self.assertIn("queue leaky=downstream max-size-buffers=2", description)

    def test_recording_target_forces_thirty_fps_and_mp4_output(self) -> None:
        config = VideoShareConfig(
            identifier="record-a",
            name="Front Camera",
            endpoint="",
            source_id="front",
            output_type="record",
            fps=75,
            fixed_frame_rate=False,
            timestamp_enabled=False,
        )

        description = self.publisher._pipeline_description(
            config,
            VideoFrame(width=640, height=480, data=bytes(640 * 480 * 4)),
            encoder_chain="hardware_encoder",
            output_path=Path("/tmp/front.mp4"),
        )

        self.assertEqual(config.fps, 30)
        self.assertTrue(config.fixed_frame_rate)
        self.assertIn("framerate=30/1", description)
        self.assertIn("hardware_encoder ! h264parse ! mp4mux", description)
        self.assertIn('filesink location="/tmp/front.mp4"', description)

    def test_recording_pauses_after_source_frames_become_stale(self) -> None:
        config = VideoShareConfig(
            identifier="record-a",
            name="Front Camera",
            endpoint="data/recordings",
            source_id="front",
            output_type="record",
        )
        with patch("src.services.video_share.time.monotonic", return_value=100.0):
            self.publisher._on_source_frame(
                VideoSourceFrame(
                    source_id="front",
                    frame=VideoFrame(
                        width=640,
                        height=480,
                        data=bytes(640 * 480 * 4),
                    ),
                )
            )
            self.assertTrue(self.publisher._frame_is_fresh(config))
        with patch("src.services.video_share.time.monotonic", return_value=101.1):
            self.assertFalse(self.publisher._frame_is_fresh(config))

    def test_recording_rejects_dynamic_panel_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "belirli bir kameraya"):
            VideoShareConfig(
                identifier="record-a",
                name="Record",
                endpoint="",
                source_id="panel",
                output_type="record",
            )
