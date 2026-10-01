from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.container import create_container
from src.extension.api import ExtensionManifest, ExtensionRegistrar
from src.extension.host import ExtensionError, ExtensionHost
from src.services.abstracts.telemetry import TelemetryService
from src.services.vehicle.telemetry_base import MutableTelemetryService
from extensions.mavlink.runtime import MavlinkTelemetryRuntime
from extensions.vehicle_connection.runtime import ConnectionRuntime


class ExtensionTests(unittest.TestCase):
    def test_legacy_connection_preference_migrates_without_enabling_it(self) -> None:
        for saved, expected in (({"telemetry": False}, False),
                                ({"telemetry": True, "vehicle_connection": False}, False)):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "data/extensions/state.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(saved))
                container = create_container(root)
                try:
                    states = {item.manifest.identifier: item for item in
                              container.resolve(ExtensionHost).list_extensions()}
                    self.assertEqual(states["vehicle_connection"].enabled, expected)
                    self.assertNotIn("telemetry", states)
                    persisted = json.loads(path.read_text())
                    self.assertNotIn("telemetry", persisted)
                    self.assertEqual(persisted["vehicle_connection"], expected)
                finally:
                    container.shutdown()

    def test_connection_shutdown_preserves_protocol_runtime_and_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                host.start()
                runtime = container.resolve(MavlinkTelemetryRuntime)
                host.set_enabled("vehicle_connection", False)
                self.assertIs(container.resolve(MavlinkTelemetryRuntime), runtime)
                self.assertTrue(host.is_active("mavlink"))
            finally:
                container.shutdown()

    def test_extension_translations_have_single_owners_and_cached_lookup(self) -> None:
        from src.ui import i18n
        root = Path(__file__).resolve().parents[1]
        owned = set(json.loads((root / "src/ui/i18n/tr.json").read_text()))
        for extension in ("camera", "mavlink", "vehicle_connection"):
            directory = root / "extensions" / extension / "i18n"
            tr = json.loads((directory / "tr.json").read_text())
            en = json.loads((directory / "en.json").read_text())
            self.assertEqual(set(tr), set(en))
            self.assertFalse(owned.intersection(tr))
            owned.update(tr)
        with patch.object(i18n, "_read_language_pack", side_effect=AssertionError("disk I/O")):
            self.assertEqual(i18n.t("extension.vehicle_connection.name", "tr"), "Araç Bağlantısı")

    def test_telemetry_runtime_accepts_protocol_neutral_feed_and_releases_it(self) -> None:
        from src.models.telemetry import TelemetrySnapshot
        feed = MutableTelemetryService()
        feed.patch(battery_percent=73)
        hub = MutableTelemetryService()
        source = SimpleNamespace(create_telemetry=lambda settings, logging: feed)
        runtime = MavlinkTelemetryRuntime(source, hub, None, None)
        runtime.start()
        self.assertEqual(hub.current.battery_percent, 73)
        feed.patch(battery_percent=52)
        self.assertEqual(hub.current.battery_percent, 52)
        runtime.stop()
        feed.patch(battery_percent=10)
        self.assertEqual(hub.current.battery_percent, TelemetrySnapshot.zero().battery_percent)
        runtime.start()
        self.assertEqual(hub.current.battery_percent, 10)
        runtime.stop()

    def test_builtins_load_and_one_protocol_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                telemetry = container.resolve(TelemetryService)
                self.assertIsInstance(telemetry, MutableTelemetryService)
                states = {item.manifest.identifier: item for item in host.list_extensions()}
                self.assertTrue(states["mavlink"].enabled)
                self.assertTrue(states["vehicle_connection"].enabled)
                with self.assertRaises(ExtensionError):
                    host.set_enabled("mavlink", False)
                self.assertFalse(host.can_reload("mavlink"))
                self.assertFalse((Path(directory) / "data/extensions/state.json").exists())
            finally:
                container.shutdown()

    def test_telemetry_can_be_disabled_without_erasing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = create_container(root)
            try:
                host = first.resolve(ExtensionHost)
                telemetry = first.resolve(TelemetryService)
                host.start()
                telemetry.start()
                self.assertTrue(first.container.has(ConnectionRuntime))
                host.set_enabled("vehicle_connection", False)
                self.assertFalse(host.is_active("vehicle_connection"))
                self.assertFalse(first.container.has(ConnectionRuntime))
                self.assertIs(telemetry, first.resolve(TelemetryService))
                self.assertFalse(next(item for item in host.list_extensions()
                                      if item.manifest.identifier == "vehicle_connection").pending_restart)
                host.set_enabled("vehicle_connection", True)
                self.assertTrue(first.container.has(ConnectionRuntime))
                host.set_enabled("vehicle_connection", False)
                telemetry.stop()
                host.stop()
            finally:
                first.shutdown()
            second = create_container(root)
            try:
                self.assertIsInstance(second.resolve(TelemetryService), MutableTelemetryService)
                state = {item.manifest.identifier: item for item in second.resolve(ExtensionHost).list_extensions()}
                self.assertFalse(state["vehicle_connection"].enabled)
                self.assertFalse(state["vehicle_connection"].pending_restart)
                self.assertEqual(json.loads((root / "data/extensions/state.json").read_text())["vehicle_connection"], False)
            finally:
                second.shutdown()

    def test_duplicate_service_registration_is_rejected(self) -> None:
        registrar = ExtensionRegistrar()
        registrar.singleton(str, str)
        with self.assertRaises(ValueError):
            registrar.singleton(str, str)
        registrar.sidebar_view("dashboard", "Dashboard", lambda parent: parent)
        self.assertEqual(registrar.sidebar_views[0].identifier, "dashboard")
        with self.assertRaises(ValueError):
            registrar.sidebar_view("dashboard", "Duplicate", lambda parent: parent)

    def test_camera_activation_releases_owned_service_and_contributions(self) -> None:
        from extensions.camera.runtime import CameraRuntime

        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                host.start()
                host.set_enabled("camera", True)
                self.assertTrue(host.is_active("camera"))
                self.assertFalse(next(item.pending_restart for item in host.list_extensions()
                                      if item.manifest.identifier == "camera"))
                self.assertTrue(container.container.has(CameraRuntime))
                self.assertEqual(len(host.visual_views()), 1)
                previous_runtime = container.resolve(CameraRuntime)
                host.reload("camera")
                self.assertIsNot(previous_runtime, container.resolve(CameraRuntime))
                self.assertEqual(len(host.visual_views()), 1)
                self.assertEqual(len([item for item in host.status_widgets() if item.identifier.startswith("camera.")]), 1)
                host.set_enabled("camera", False)
                self.assertFalse(container.container.has(CameraRuntime))
                self.assertFalse(host.visual_views())
                self.assertFalse(any(item.identifier.startswith("camera.") for item in host.status_widgets()))
                self.assertIsNone(host.settings_action("camera"))
                host.set_enabled("camera", True)
                self.assertTrue(container.container.has(CameraRuntime))
                host.stop()
            finally:
                container.shutdown()

    def test_extension_settings_are_scoped_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                host._manifests["example"] = ExtensionManifest(
                    "example", "Example", "", "Test", "1.0", "extensions.example.plugin:create_extension",
                    settings={"sample_rate": {"type": "integer", "default": 25}},
                )
                self.assertEqual(host.settings("example"), {"sample_rate": 25})
                host.save_settings("example", {"sample_rate": 50})
                self.assertEqual(host.settings("example"), {"sample_rate": 50})
                with self.assertRaises(ValueError):
                    host.save_settings("example", {"sample_rate": "fast"})
                self.assertTrue(next(item for item in host.list_extensions()
                                     if item.manifest.identifier == "example").pending_restart)
            finally:
                container.shutdown()

    def test_corrupt_extension_state_does_not_restore_automatic_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data/extensions/state.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"mavlink": "false"}', encoding="utf-8")
            with self.assertRaises(ExtensionError):
                create_container(root)

    def test_discovery_does_not_autostart_new_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                extra = ExtensionManifest("extra", "Extra", "", "Test", "1.0",
                                          "extensions.extra.plugin:create_extension",
                                          default_enabled=True)
                with patch.object(host, "_discover", return_value={**host._manifests, "extra": extra}):
                    host.refresh_extensions()
                self.assertFalse(host.is_active("extra"))
                self.assertFalse(next(item.enabled for item in host.list_extensions()
                                      if item.manifest.identifier == "extra"))
            finally:
                container.shutdown()

    def test_failed_hot_activation_leaves_no_service_or_enabled_state(self) -> None:
        class BrokenExtension:
            stopped = False

            def configure(self, registrar, _context):
                registrar.singleton(int, lambda: 1)

            def start(self, _context):
                raise RuntimeError("startup failed")

            def stop(self):
                self.stopped = True

        broken = BrokenExtension()
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                host._manifests["broken"] = ExtensionManifest(
                    "broken", "Broken", "", "Test", "1.0",
                    "extensions.broken.plugin:create_extension")
                host.start()
                with patch("src.extension.host.host.importlib.import_module",
                           return_value=SimpleNamespace(create_extension=lambda: broken)):
                    with self.assertRaisesRegex(RuntimeError, "startup failed"):
                        host.set_enabled("broken", True)
                self.assertTrue(broken.stopped)
                self.assertFalse(host.is_active("broken"))
                self.assertFalse(container.container.has(int))
                self.assertFalse(next(item.enabled for item in host.list_extensions()
                                      if item.manifest.identifier == "broken"))
                host.stop()
            finally:
                container.shutdown()


if __name__ == "__main__":
    unittest.main()
