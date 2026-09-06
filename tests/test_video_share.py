from __future__ import annotations

import unittest

from src.core.events import EventBus
from src.models.video import VideoFrame, VideoShareConfig
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

