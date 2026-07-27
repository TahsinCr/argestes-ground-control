from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models.settings import SettingsModel
from src.services.logging_service import LoggingService
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
        self.assertFalse(model.backend.mavlink.auto_connect)
        self.assertFalse(model.backend.video_auto_connect)
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
            service.set_value("backend.video_auto_connect", True)
            payload = json.loads((base_dir / "data/settings.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["ui"]["theme"], "light")
            self.assertTrue(payload["backend"]["mavlink"]["auto_connect"])
            self.assertTrue(payload["backend"]["video_auto_connect"])
            self.assertEqual(len(received), 3)

    def test_missing_data_directory_creates_complete_default_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory) / "new-project"
            service = SettingsService(base_dir)

            settings_path = base_dir / "data" / "settings.json"
            cache_path = base_dir / "data" / "map-cache"
            self.assertTrue(settings_path.is_file())
            self.assertTrue(cache_path.is_dir())
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload,
                service.current.to_dict(),
            )
            self.assertEqual(
                payload["backend"]["battery_alarm"]["warning_threshold"],
                20.0,
            )
            self.assertEqual(
                payload["backend"]["battery_alarm"]["failsafe_threshold"],
                10.0,
            )
            self.assertEqual(payload["backend"]["video_source_type"], "udp")
            self.assertFalse(payload["backend"]["video_auto_connect"])
            self.assertFalse(payload["backend"]["mavlink"]["auto_connect"])
            self.assertFalse(payload["backend"]["competition"]["auto_connect"])
            self.assertFalse(payload["backend"]["orin"]["enabled"])

    def test_invalid_settings_file_is_replaced_with_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            settings_path = base_dir / "data" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text("{invalid-json", encoding="utf-8")
            logs = LoggingService()

            service = SettingsService(base_dir, logs.child("SETTINGS"))

            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(payload, service.current.to_dict())
            self.assertTrue(
                any(
                    entry.action == "settings.load_failed"
                    for entry in logs.history
                )
            )
            self.assertTrue(
                any(
                    entry.action == "settings.defaults_created"
                    for entry in logs.history
                )
            )

    def test_unreadable_settings_file_falls_back_and_rewrites_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            settings_path = base_dir / "data" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text("{}", encoding="utf-8")
            with patch(
                "src.services.settings.load_json_file",
                side_effect=PermissionError("read denied"),
            ):
                service = SettingsService(base_dir)

            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(payload, service.current.to_dict())

    def test_invalid_backend_values_are_rejected_before_persistence(self) -> None:
        model = SettingsModel()
        with self.assertRaises(ValueError):
            model.with_value("backend.mavlink.heartbeat_timeout", 0)
        with self.assertRaises(ValueError):
            model.with_value("backend.video_frame_timeout_s", 0)
        with self.assertRaises(ValueError):
            model.with_value("backend.mission.radii.rtl", -1)
        with self.assertRaises(ValueError):
            model.with_value("backend.battery_alarm.warning_mode", "current")
        with self.assertRaises(ValueError):
            model.with_value("backend.battery_alarm.warning_threshold", 0)
        with self.assertRaises(ValueError):
            model.with_value("backend.battery_alarm.failsafe_threshold", 0)

    def test_battery_warning_and_failsafe_thresholds_are_persisted(self) -> None:
        model = SettingsModel()
        self.assertFalse(model.backend.battery_alarm.failsafe_enabled)
        self.assertEqual(model.backend.battery_alarm.warning_mode, "percent")
        self.assertEqual(model.backend.battery_alarm.warning_threshold, 20.0)
        self.assertEqual(model.backend.battery_alarm.failsafe_mode, "percent")
        self.assertEqual(model.backend.battery_alarm.failsafe_threshold, 10.0)

        updated = model.with_value(
            "backend.battery_alarm.warning_mode",
            "voltage",
        )
        updated = updated.with_value(
            "backend.battery_alarm.warning_threshold",
            14.4,
        )
        updated = updated.with_value(
            "backend.battery_alarm.failsafe_mode",
            "percent",
        )
        updated = updated.with_value(
            "backend.battery_alarm.failsafe_threshold",
            8.0,
        )
        updated = updated.with_value(
            "backend.battery_alarm.failsafe_enabled",
            True,
        )
        restored = SettingsModel.from_dict(updated.to_dict())
        self.assertTrue(restored.backend.battery_alarm.failsafe_enabled)
        self.assertEqual(restored.backend.battery_alarm.warning_mode, "voltage")
        self.assertEqual(restored.backend.battery_alarm.warning_threshold, 14.4)
        self.assertEqual(restored.backend.battery_alarm.failsafe_mode, "percent")
        self.assertEqual(restored.backend.battery_alarm.failsafe_threshold, 8.0)

    def test_legacy_battery_threshold_migrates_to_both_policies(self) -> None:
        model = SettingsModel.from_dict(
            {
                "backend": {
                    "battery_alarm": {
                        "failsafe_enabled": True,
                        "mode": "voltage",
                        "threshold": 14.2,
                    }
                }
            }
        )
        battery = model.backend.battery_alarm
        self.assertEqual(battery.warning_mode, "voltage")
        self.assertEqual(battery.warning_threshold, 14.2)
        self.assertEqual(battery.failsafe_mode, "voltage")
        self.assertEqual(battery.failsafe_threshold, 14.2)


if __name__ == "__main__":
    unittest.main()
