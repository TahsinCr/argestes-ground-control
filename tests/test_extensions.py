from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.container import create_container
from src.extension.api import ExtensionManifest, ExtensionRegistrar
from src.extension.host import ExtensionError, ExtensionHost
from src.services.abstracts.telemetry import TelemetryService
from src.services.vehicle.telemetry import MutableTelemetryService


class ExtensionTests(unittest.TestCase):
    def test_builtins_load_and_one_protocol_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                states = {item.manifest.identifier: item for item in host.list_extensions()}
                self.assertTrue(states["mavlink"].enabled)
                self.assertTrue(states["telemetry"].enabled)
                with self.assertRaises(ExtensionError):
                    host.set_enabled("mavlink", False)
                self.assertFalse((Path(directory) / "data/extensions/state.json").exists())
            finally:
                container.shutdown()

    def test_telemetry_can_be_disabled_without_erasing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = create_container(root)
            try:
                first.resolve(ExtensionHost).set_enabled("telemetry", False)
            finally:
                first.shutdown()
            second = create_container(root)
            try:
                self.assertIsInstance(second.resolve(TelemetryService), MutableTelemetryService)
                state = {item.manifest.identifier: item for item in second.resolve(ExtensionHost).list_extensions()}
                self.assertFalse(state["telemetry"].enabled)
                self.assertFalse(state["telemetry"].pending_restart)
                self.assertEqual(json.loads((root / "data/extensions/state.json").read_text())["telemetry"], False)
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


if __name__ == "__main__":
    unittest.main()
