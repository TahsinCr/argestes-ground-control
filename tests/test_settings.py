from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.models.settings import SettingsModel
from src.services.settings import SettingsService
from src.ui.config.settings_schema import iter_fields


class SettingsTests(unittest.TestCase):
    def test_legacy_settings_are_split_between_backend_and_ui(self) -> None:
        model = SettingsModel.from_dict(
            {
                "language": "en",
                "theme": "light",
                "mock_mode": True,
                "cache_enabled": False,
                "default_radius_land": 81,
            }
        )
        self.assertEqual(model.ui.language, "en")
        self.assertEqual(model.ui.theme, "light")
        self.assertTrue(model.backend.mock_mode)
        self.assertFalse(model.backend.cache.enabled)
        self.assertEqual(model.backend.mission.radius_for("land"), 81)
        self.assertIn("rtl", model.backend.mission.radii)

    def test_every_schema_path_is_backed_by_the_settings_model(self) -> None:
        model = SettingsModel()
        self.assertTrue(model.backend.mavlink.auto_connect)
        for field in iter_fields():
            value = model.value_at(field.path)
            changed = model.with_value(field.path, value)
            self.assertEqual(changed.value_at(field.path), value, field.path)

    def test_service_persists_nested_shape_and_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            service = SettingsService(base_dir)
            received: list[SettingsModel] = []
            service.changed.subscribe(received.append)
            service.set_value("ui.theme", "light")
            service.set_value("backend.mavlink.auto_connect", True)
            payload = json.loads((base_dir / "data/settings.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["ui"]["theme"], "light")
            self.assertTrue(payload["backend"]["mavlink"]["auto_connect"])
            self.assertEqual(len(received), 2)

    def test_invalid_backend_values_are_rejected_before_persistence(self) -> None:
        model = SettingsModel()
        with self.assertRaises(ValueError):
            model.with_value("backend.mavlink.heartbeat_timeout", 0)
        with self.assertRaises(ValueError):
            model.with_value("backend.video_frame_timeout_s", 0)
        with self.assertRaises(ValueError):
            model.with_value("backend.mission.radii.rtl", -1)


if __name__ == "__main__":
    unittest.main()
