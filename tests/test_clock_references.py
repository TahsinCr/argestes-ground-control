from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from extensions.camera.runtime import (
    CameraConfiguration,
    CameraRuntime,
    CameraSettingsStore,
)
from extensions.siha_teknofest_server.runtime import CompetitionRuntime
from src.extension.api import ClockReferenceRegistry


class ClockReferenceTests(unittest.TestCase):
    def test_registry_uses_stable_ids_and_normalizes_aware_values_to_utc(self) -> None:
        registry = ClockReferenceRegistry()
        local_time = datetime(2026, 10, 23, 12, 0, tzinfo=timezone(timedelta(hours=3)))

        identifier = registry.register(
            "camera", "server.abc.utc", "Field server", lambda: local_time
        )

        self.assertEqual(identifier, "camera.server.abc.utc")
        self.assertEqual(registry.references()[0].title, "Field server")
        self.assertEqual(
            registry.current_time(identifier),
            datetime(2026, 10, 23, 9, 0, tzinfo=UTC),
        )

        registry.register("camera", "server.abc.utc", "Renamed", lambda: local_time)
        self.assertEqual(registry.references()[0].identifier, identifier)
        self.assertEqual(registry.references()[0].title, "Renamed")

    def test_registry_treats_missing_invalid_and_failing_sources_as_unavailable(self) -> None:
        registry = ClockReferenceRegistry()
        naive = registry.register("test_source", "naive", "Naive", lambda: datetime.now())

        def fail() -> datetime:
            raise RuntimeError("not ready")

        failed = registry.register("test_source", "failed", "Failed", fail)
        self.assertIsNone(registry.current_time(naive))
        self.assertIsNone(registry.current_time(failed))
        self.assertIsNone(registry.current_time("missing.source"))

        registry.unregister_owner("test_source")
        self.assertEqual(registry.references(), ())

    def test_camera_clock_setting_round_trips_without_using_display_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CameraSettingsStore(root)
            original = store.current
            reference_id = "siha_teknofest_server.server.123abc.utc"
            store.save(replace(original, clock_reference_id=reference_id))

            reloaded = CameraSettingsStore(root)
            self.assertEqual(reloaded.current.clock_reference_id, reference_id)

    def test_camera_clock_selection_updates_active_video_clock_provider(self) -> None:
        runtime = object.__new__(CameraRuntime)
        previous = CameraConfiguration((), "", ())
        saved = []
        provider_updates = []
        runtime.settings = SimpleNamespace(
            current=previous,
            save=saved.append,
        )
        runtime.video = SimpleNamespace(set_clock_provider=provider_updates.append)
        runtime._clock_reference_id = "system.utc"
        runtime._current_clock_time = lambda: None

        runtime.save_clock_reference("server.profile.utc")

        self.assertEqual(saved[0].clock_reference_id, "server.profile.utc")
        self.assertEqual(runtime._clock_reference_id, "server.profile.utc")
        self.assertEqual(provider_updates, [runtime._current_clock_time])

    def test_competition_profiles_register_stable_clock_references(self) -> None:
        registry = ClockReferenceRegistry()
        runtime = object.__new__(CompetitionRuntime)
        runtime.clock_references = registry
        runtime._clock_reference_ids = set()
        sample_time = datetime(2026, 10, 23, tzinfo=UTC)
        sample = SimpleNamespace(
            received_monotonic=time.monotonic(),
            estimate=lambda _now: sample_time,
        )
        session = SimpleNamespace(
            config={"name": "Field server"},
            clock=sample,
        )
        runtime.sessions = {"profile-stable-id": session}

        runtime._sync_clock_references()
        reference, = registry.references()
        self.assertEqual(reference.extension_id, "siha_teknofest_server")
        self.assertEqual(reference.title, "Field server")
        self.assertEqual(registry.current_time(reference.identifier), sample_time)

        session.config["name"] = "Renamed server"
        runtime._sync_clock_references()
        updated, = registry.references()
        self.assertEqual(updated.identifier, reference.identifier)
        self.assertEqual(updated.title, "Renamed server")

        runtime.sessions.clear()
        runtime._sync_clock_references()
        self.assertEqual(registry.references(), ())


if __name__ == "__main__":
    unittest.main()
