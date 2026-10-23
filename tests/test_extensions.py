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
from src.services.settings import SettingsService
from src.services.telemetry import MutableTelemetryService
from extensions.mavlink.runtime import MavlinkTelemetryRuntime
from extensions.vehicle_connection.runtime import ConnectionRuntime


class ExtensionTests(unittest.TestCase):
    def test_legacy_connection_preference_migrates_without_enabling_it(self) -> None:
        for saved, expected in (
            ({"telemetry": False}, False),
            ({"telemetry": True, "vehicle_connection": False}, False),
        ):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "data/extensions/state.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(saved))
                container = create_container(root)
                try:
                    states = {
                        item.manifest.identifier: item
                        for item in container.resolve(ExtensionHost).list_extensions()
                    }
                    self.assertEqual(states["vehicle_connection"].enabled, expected)
                    self.assertNotIn("telemetry", states)
                    persisted = {
                        key: entry["enabled"]
                        for key, entry in json.loads(
                            (root / "data/settings.json").read_text()
                        )["extensions"].items()
                        if "enabled" in entry
                    }
                    self.assertFalse(path.exists())
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
        with patch.object(
            i18n, "_read_language_pack", side_effect=AssertionError("disk I/O")
        ):
            self.assertEqual(
                i18n.t("extension.vehicle_connection.name", "tr"), "Araç Bağlantısı"
            )

    def test_telemetry_runtime_accepts_protocol_neutral_feed_and_releases_it(
        self,
    ) -> None:
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
        self.assertEqual(
            hub.current.battery_percent, TelemetrySnapshot.zero().battery_percent
        )
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
                states = {
                    item.manifest.identifier: item for item in host.list_extensions()
                }
                self.assertTrue(states["mavlink"].enabled)
                self.assertTrue(states["vehicle_connection"].enabled)
                clock_ids = {
                    reference.identifier
                    for reference in host.clock_references.references()
                }
                self.assertTrue({"system.local", "system.utc"}.issubset(clock_ids))
                with self.assertRaises(ExtensionError):
                    host.set_enabled("mavlink", False)
                self.assertFalse(host.can_reload("mavlink"))
                self.assertFalse(
                    (Path(directory) / "data/extensions/state.json").exists()
                )
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
                self.assertFalse(
                    next(
                        item
                        for item in host.list_extensions()
                        if item.manifest.identifier == "vehicle_connection"
                    ).pending_restart
                )
                host.set_enabled("vehicle_connection", True)
                self.assertTrue(first.container.has(ConnectionRuntime))
                host.set_enabled("vehicle_connection", False)
                telemetry.stop()
                host.stop()
            finally:
                first.shutdown()
            second = create_container(root)
            try:
                self.assertIsInstance(
                    second.resolve(TelemetryService), MutableTelemetryService
                )
                state = {
                    item.manifest.identifier: item
                    for item in second.resolve(ExtensionHost).list_extensions()
                }
                self.assertFalse(state["vehicle_connection"].enabled)
                self.assertFalse(state["vehicle_connection"].pending_restart)
                self.assertEqual(
                    json.loads((root / "data/settings.json").read_text())["extensions"][
                        "vehicle_connection"
                    ]["enabled"],
                    False,
                )
            finally:
                second.shutdown()

    def test_duplicate_service_registration_is_rejected(self) -> None:
        registrar = ExtensionRegistrar()
        registrar.singleton(str, str)
        with self.assertRaises(ValueError):
            registrar.singleton(str, str)
        registrar.visual_view(
            "dashboard", lambda parent, theme, language: parent, title="Dashboard"
        )
        self.assertEqual(registrar.visual_views[0].identifier, "dashboard")
        with self.assertRaises(ValueError):
            registrar.visual_view(
                "dashboard", lambda parent, theme, language: parent, title="Duplicate"
            )

    def test_camera_activation_releases_owned_service_and_contributions(self) -> None:
        from extensions.camera.runtime import CameraRuntime

        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                host.start()
                host.set_enabled("camera", True)
                self.assertTrue(host.is_active("camera"))
                self.assertFalse(
                    next(
                        item.pending_restart
                        for item in host.list_extensions()
                        if item.manifest.identifier == "camera"
                    )
                )
                self.assertTrue(container.container.has(CameraRuntime))
                self.assertEqual(
                    len(
                        [
                            item
                            for item in host.visual_views()
                            if item.identifier.startswith("camera.")
                        ]
                    ),
                    1,
                )
                previous_runtime = container.resolve(CameraRuntime)
                host.reload("camera")
                self.assertIsNot(previous_runtime, container.resolve(CameraRuntime))
                self.assertEqual(
                    len(
                        [
                            item
                            for item in host.visual_views()
                            if item.identifier.startswith("camera.")
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    len(
                        [
                            item
                            for item in host.status_widgets()
                            if item.identifier.startswith("camera.")
                        ]
                    ),
                    1,
                )
                host.set_enabled("camera", False)
                self.assertFalse(container.container.has(CameraRuntime))
                self.assertFalse(
                    [
                        item
                        for item in host.visual_views()
                        if item.identifier.startswith("camera.")
                    ]
                )
                self.assertFalse(
                    any(
                        item.identifier.startswith("camera.")
                        for item in host.status_widgets()
                    )
                )
                self.assertIsNone(host.settings_contribution("camera"))
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
                    "example",
                    "Example",
                    "",
                    "Test",
                    "1.0",
                    "extensions.example.plugin:create_extension",
                    settings={"sample_rate": {"type": "integer", "default": 25}},
                )
                self.assertEqual(host.settings("example"), {"sample_rate": 25})
                host.save_settings("example", {"sample_rate": 50})
                self.assertEqual(host.settings("example"), {"sample_rate": 50})
                with self.assertRaises(ValueError):
                    host.save_settings("example", {"sample_rate": "fast"})
                self.assertTrue(
                    next(
                        item
                        for item in host.list_extensions()
                        if item.manifest.identifier == "example"
                    ).pending_restart
                )
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
                extra = ExtensionManifest(
                    "extra",
                    "Extra",
                    "",
                    "Test",
                    "1.0",
                    "extensions.extra.plugin:create_extension",
                    default_enabled=True,
                )
                with patch.object(
                    host, "_discover", return_value={**host._manifests, "extra": extra}
                ):
                    host.refresh_extensions()
                self.assertFalse(host.is_active("extra"))
                self.assertFalse(
                    next(
                        item.enabled
                        for item in host.list_extensions()
                        if item.manifest.identifier == "extra"
                    )
                )
            finally:
                container.shutdown()

    def test_missing_optional_extension_is_disabled_but_keeps_its_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = create_container(root)
            try:
                host = first.resolve(ExtensionHost)
                host.start()
                host.set_enabled("camera", True)
                settings = first.resolve(SettingsService)
                settings.update_extension(
                    "camera", config={"retained_option": "keep-me"}
                )
                installed = dict(host._manifests)
            finally:
                first.shutdown()

            without_camera = {
                key: manifest for key, manifest in installed.items() if key != "camera"
            }
            with patch.object(ExtensionHost, "_discover", return_value=without_camera):
                second = create_container(root)
            try:
                host = second.resolve(ExtensionHost)
                self.assertNotIn(
                    "camera",
                    {item.manifest.identifier for item in host.list_extensions()},
                )
                saved = second.resolve(SettingsService).current.extensions["camera"]
                self.assertFalse(saved["enabled"])
                self.assertEqual(saved["config"]["retained_option"], "keep-me")
            finally:
                second.shutdown()

    def test_disabled_extension_settings_can_edit_saved_unknown_keys_only(self):
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                settings = container.resolve(SettingsService)
                settings.update_extension(
                    "camera",
                    config={
                        "retained_option": "before",
                        "video_sources": [{"source_id": "front", "uri": "test://front"}],
                    },
                )

                host.save_settings("camera", {"retained_option": "after"})

                self.assertEqual(
                    settings.extension_config("camera")["retained_option"], "after"
                )
                with self.assertRaisesRegex(ValueError, "video_sources: expected array"):
                    host.save_settings("camera", {"video_sources": {"front": "bad"}})
                self.assertEqual(
                    settings.extension_config("camera")["video_sources"],
                    [{"source_id": "front", "uri": "test://front"}],
                )
                with self.assertRaisesRegex(ValueError, "Unknown extension setting"):
                    host.save_settings("camera", {"new_undeclared_option": True})
            finally:
                container.shutdown()

    def test_refresh_unloads_removed_optional_extension_and_preserves_settings(self):
        from extensions.camera.runtime import CameraRuntime

        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                host.start()
                host.set_enabled("camera", True)
                settings = container.resolve(SettingsService)
                settings.update_extension(
                    "camera", config={"retained_option": "keep-me"}
                )
                installed = dict(host._manifests)
                without_camera = {
                    key: manifest
                    for key, manifest in installed.items()
                    if key != "camera"
                }
                with patch.object(host, "_discover", return_value=without_camera):
                    host.refresh_extensions()

                self.assertFalse(host.is_active("camera"))
                self.assertNotIn(
                    "camera",
                    {item.manifest.identifier for item in host.list_extensions()},
                )
                self.assertFalse(container.container.has(CameraRuntime))
                saved = settings.current.extensions["camera"]
                self.assertFalse(saved["enabled"])
                self.assertEqual(saved["config"]["retained_option"], "keep-me")
            finally:
                container.shutdown()

    def test_refresh_restores_unloaded_extension_if_state_save_fails(self):
        from extensions.camera.runtime import CameraRuntime

        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                host.start()
                host.set_enabled("camera", True)
                installed = dict(host._manifests)
                without_camera = {
                    key: manifest
                    for key, manifest in installed.items()
                    if key != "camera"
                }
                with (
                    patch.object(host, "_discover", return_value=without_camera),
                    patch.object(host, "_save_states", side_effect=OSError("disk")),
                ):
                    with self.assertRaisesRegex(OSError, "disk"):
                        host.refresh_extensions()

                self.assertTrue(host.is_active("camera"))
                self.assertIn(
                    "camera",
                    {item.manifest.identifier for item in host.list_extensions()},
                )
                self.assertTrue(container.container.has(CameraRuntime))
            finally:
                container.shutdown()

    def test_disabling_server_extension_removes_its_clock_references(self):
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                host.start()
                container.resolve(SettingsService).update_extension(
                    "siha_teknofest_server",
                    config={
                        "servers": [
                            {
                                "identifier": "server-one",
                                "name": "Test server",
                                "base_url": "http://127.0.0.1:5000",
                                "username": "test",
                                "password": "test",
                                "team_number": 1,
                                "timeout_s": 3.0,
                                "auto_connect": False,
                            }
                        ]
                    },
                )
                host.set_enabled("siha_teknofest_server", True)
                self.assertTrue(
                    any(
                        reference.extension_id == "siha_teknofest_server"
                        for reference in host.clock_references.references()
                    )
                )

                host.set_enabled("siha_teknofest_server", False)

                self.assertFalse(
                    any(
                        reference.extension_id == "siha_teknofest_server"
                        for reference in host.clock_references.references()
                    )
                )
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
                    "broken",
                    "Broken",
                    "",
                    "Test",
                    "1.0",
                    "extensions.broken.plugin:create_extension",
                )
                host.start()
                with patch(
                    "src.extension.host.host.importlib.import_module",
                    return_value=SimpleNamespace(create_extension=lambda: broken),
                ):
                    with self.assertRaisesRegex(RuntimeError, "startup failed"):
                        host.set_enabled("broken", True)
                self.assertTrue(broken.stopped)
                self.assertFalse(host.is_active("broken"))
                self.assertFalse(container.container.has(int))
                self.assertFalse(
                    next(
                        item.enabled
                        for item in host.list_extensions()
                        if item.manifest.identifier == "broken"
                    )
                )
                host.stop()
            finally:
                container.shutdown()


if __name__ == "__main__":
    unittest.main()
