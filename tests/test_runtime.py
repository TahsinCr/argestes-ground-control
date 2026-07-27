from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models.mission import MapOverlay
from src.services.planner import PlannerService
from src.services.runtime import create_runtime_services
from src.services.tile_cache import TileCacheServer


class RuntimeTests(unittest.TestCase):
    def test_video_start_respects_saved_auto_connect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = create_runtime_services(Path(directory))
            with patch.object(runtime.video_service, "start") as start:
                runtime.start()
                start.assert_not_called()
                runtime.stop()

        with tempfile.TemporaryDirectory() as directory:
            runtime = create_runtime_services(Path(directory))
            runtime.settings_service.set_value("backend.video_auto_connect", True)
            with patch.object(runtime.video_service, "start") as start:
                runtime.start()
                start.assert_called_once_with()
                runtime.stop()

    def test_disabled_tile_cache_keeps_no_idle_server_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TileCacheServer(
                cache_path=Path(directory),
                enabled=False,
                limit_mb=64,
            )
            cache.start()
            self.assertFalse(cache.running)
            self.assertEqual(cache.base_url, "")

            cache.configure(
                cache_path=Path(directory),
                enabled=True,
                limit_mb=64,
            )
            cache.start()
            self.assertTrue(cache.running)
            thread = cache._thread
            cache.stop()
            self.assertFalse(cache.running)
            self.assertIsNotNone(thread)
            self.assertFalse(thread.is_alive())

    def test_facade_supports_non_qt_callers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = create_runtime_services(Path(directory))
            runtime.start()
            self.assertFalse(runtime.connection_service.status("video").connected)
            server_status = runtime.connection_service.status("server")
            self.assertIn("TEKNOFEST", server_status.summary)
            self.assertEqual(server_status.endpoint, "http://127.0.0.1:5000")
            self.assertNotEqual(server_status.endpoint, runtime.tile_cache_service.base_url)
            self.assertIs(runtime.container.resolve(PlannerService), runtime.planner_service)
            runtime.facade.upsert_mission_item(
                MapOverlay("rtl-1", "rtl", "RTL", [[39.9, 32.8]], "#f4b942", 75)
            )
            runtime.facade.set_setting("ui.theme", "light")
            self.assertEqual(runtime.facade.parameter_snapshot(), ())
            result = runtime.facade.issue("ARM", source="test")
            self.assertEqual(runtime.facade.mission_snapshot().overlays[0].overlay_type, "rtl")
            self.assertEqual(runtime.facade.settings_snapshot().ui.theme, "light")
            self.assertFalse(result.accepted)
            runtime.stop()


if __name__ == "__main__":
    unittest.main()
