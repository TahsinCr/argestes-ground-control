from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.models.mission import MapOverlay
from src.services.contracts.mission import MissionService
from src.services.runtime import create_runtime_services


class RuntimeTests(unittest.TestCase):
    def test_facade_supports_non_qt_callers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = create_runtime_services(Path(directory))
            runtime.start()
            self.assertFalse(runtime.connection_service.status("video").connected)
            server_status = runtime.connection_service.status("server")
            self.assertIn("TEKNOFEST", server_status.summary)
            self.assertEqual(server_status.endpoint, "http://127.0.0.1:5000")
            self.assertNotEqual(server_status.endpoint, runtime.tile_cache_service.base_url)
            self.assertIs(runtime.container.resolve(MissionService), runtime.mission_service)
            runtime.facade.upsert_mission_item(
                MapOverlay("rtl-1", "rtl", "RTL", [[39.9, 32.8]], "#f4b942", 75)
            )
            runtime.facade.set_setting("ui.theme", "light")
            first_parameter = runtime.facade.parameter_snapshot()[0]
            with self.assertRaises(ConnectionError):
                runtime.facade.update_parameters({first_parameter.key: first_parameter.value})
            result = runtime.facade.issue("ARM", source="test")
            self.assertEqual(runtime.facade.mission_snapshot().overlays[0].overlay_type, "rtl")
            self.assertEqual(runtime.facade.settings_snapshot().ui.theme, "light")
            self.assertFalse(result.accepted)
            runtime.stop()


if __name__ == "__main__":
    unittest.main()
