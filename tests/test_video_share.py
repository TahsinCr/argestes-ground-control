from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from src.models.video import VideoShareConfig
from src.services.video_share import VideoSharePublisher


class VideoShareClockTests(unittest.TestCase):
    def test_overlay_advances_from_the_received_server_time_without_timezone_conversion(self) -> None:
        reference = datetime(2026, 8, 27, 14, 5, 6, 789_000)

        with patch("src.services.video_share.time.monotonic", return_value=102.345):
            text = VideoSharePublisher._clock_text(reference, started_at=100.0)

        self.assertEqual(text, "14:05:09.134")

    def test_multicast_sink_binds_to_the_selected_active_interface(self) -> None:
        config = VideoShareConfig(
            identifier="share",
            name="share",
            endpoint="235.10.10.10:10021",
            network_interface="eno1",
        )

        with patch(
            "src.services.video_share.active_ipv4_interfaces",
            return_value=(("eno1", "10.0.0.240"),),
        ):
            description = VideoSharePublisher._sink_description(config, "rtph264pay pt=96")

        self.assertIn("bind-address=10.0.0.240", description)
        self.assertIn("multicast-iface=eno1", description)
