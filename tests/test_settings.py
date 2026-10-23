from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src.models.settings import SettingsModel
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from src.ui.config.settings_schema import iter_fields


class SettingsTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX file modes are required")
    def test_existing_settings_file_is_restricted_to_its_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            settings_path = base_dir / "data" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(SettingsModel.default(base_dir).to_dict()),
                encoding="utf-8",
            )
            settings_path.chmod(0o644)

            SettingsService(base_dir)

            self.assertEqual(stat.S_IMODE(settings_path.stat().st_mode), 0o600)

    def test_concurrent_path_updates_are_one_read_modify_write_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = SettingsService(Path(directory))

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = (
                    executor.submit(service.set_value, "ui.theme", "light"),
                    executor.submit(
                        service.set_value,
                        "backend.mavlink.auto_connect",
                        True,
                    ),
                )
                for future in futures:
                    future.result()

            restored = SettingsService(Path(directory)).current
            self.assertEqual(restored.ui.theme, "light")
            self.assertTrue(restored.backend.mavlink.auto_connect)
            self.assertFalse(list((Path(directory) / "data").glob("*.tmp")))

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

    def test_unchanged_patch_does_not_rewrite_or_notify_the_ui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            service = SettingsService(base_dir)
            settings_path = base_dir / "data/settings.json"
            original_mtime = settings_path.stat().st_mtime_ns
            received: list[SettingsModel] = []
            service.changed.subscribe(received.append)

            service.patch(map_layer=service.current.map_layer)

            self.assertEqual(settings_path.stat().st_mtime_ns, original_mtime)
            self.assertEqual(received, [])

    def test_missing_data_directory_creates_complete_default_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory) / "new-project"
            service = SettingsService(base_dir)

            settings_path = base_dir / "data" / "settings.json"
            cache_path = base_dir / "data" / "map-cache"
            self.assertTrue(settings_path.is_file())
            self.assertFalse(cache_path.exists(), "Shared settings must not create a map cache")
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
            self.assertFalse(payload["backend"]["mavlink"]["auto_connect"])

    def test_invalid_settings_file_is_preserved_before_safe_defaults_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            settings_path = base_dir / "data" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text("{invalid-json", encoding="utf-8")
            logs = LoggingService()

            service = SettingsService(base_dir, logs.child("SETTINGS"))

            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(payload, service.current.to_dict())
            backups = tuple(settings_path.parent.glob("settings.corrupt-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "{invalid-json")
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

    def test_unreadable_settings_file_is_preserved_before_defaults_are_written(self) -> None:
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
            backups = tuple(settings_path.parent.glob("settings.corrupt-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "{}")

    def test_corrupt_settings_are_not_overwritten_when_preservation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            settings_path = base_dir / "data" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            original = "{invalid-json"
            settings_path.write_text(original, encoding="utf-8")
            with patch(
                "src.services.settings.SettingsService._preserve_unreadable_settings",
                return_value=None,
            ):
                service = SettingsService(base_dir)

            self.assertEqual(settings_path.read_text(encoding="utf-8"), original)
            self.assertEqual(service.current.ui.language, "tr")

    def test_invalid_backend_values_are_rejected_before_persistence(self) -> None:
        model = SettingsModel()
        with self.assertRaises(ValueError):
            model.with_value("backend.mavlink.heartbeat_timeout", 0)
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

    def test_voltage_battery_profile_derives_zero_percent_from_reserve(self) -> None:
        profile = SettingsModel().backend.battery_telemetry

        self.assertEqual(profile.mode, "voltage")
        self.assertEqual(profile.cell_count, 4)
        self.assertAlmostEqual(profile.full_voltage, 16.8)
        self.assertAlmostEqual(profile.reserve_voltage, 14.8)
        self.assertAlmostEqual(profile.reserve_percent, 10.0)
        self.assertAlmostEqual(profile.empty_voltage, 14.5777777778)

        restored = SettingsModel.from_dict(SettingsModel().to_dict())
        self.assertAlmostEqual(
            restored.backend.battery_telemetry.empty_voltage,
            profile.empty_voltage,
        )

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
