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
from src.extension.api import ClockQuality, ClockReferenceRegistry, ClockSample


class ClockReferenceTests(unittest.TestCase):
    def test_registry_uses_stable_ids_and_preserves_provider_timezone(self) -> None:
        registry = ClockReferenceRegistry()
        local_time = datetime(2026, 10, 23, 12, 0, tzinfo=timezone(timedelta(hours=3)))

        sample = ClockSample(local_time, datetime.now(UTC))
        identifier = registry.register(
            "camera", "server.abc.utc", "Field server", lambda: sample
        )

        self.assertEqual(identifier, "camera.server.abc.utc")
        self.assertEqual(registry.references()[0].title, "Field server")
        self.assertEqual(registry.status(identifier).value, local_time)

        registry.register("camera", "server.abc.utc", "Renamed", lambda: sample)
        self.assertEqual(registry.references()[0].identifier, identifier)
        self.assertEqual(registry.references()[0].title, "Renamed")

    def test_camera_clock_defaults_to_local_system_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                CameraSettingsStore(Path(directory)).current.clock_reference_id,
                "system.local",
            )

    def test_registry_treats_missing_invalid_and_failing_sources_as_unavailable(self) -> None:
        registry = ClockReferenceRegistry()
        naive = registry.register(
            "test_source", "naive", "Naive", lambda: datetime.now()
        )

        def fail() -> ClockSample:
            raise RuntimeError("not ready")

        failed = registry.register("test_source", "failed", "Failed", fail)
        self.assertIs(
            registry.status(naive).quality,
            ClockQuality.UNAVAILABLE,
        )
        self.assertIs(
            registry.status(failed).quality,
            ClockQuality.UNAVAILABLE,
        )
        self.assertIs(
            registry.status("missing.source").quality,
            ClockQuality.UNAVAILABLE,
        )

        registry.unregister_owner("test_source")
        self.assertEqual(registry.references(), ())

    def test_registry_reports_sample_quality_and_measured_age(self) -> None:
        registry = ClockReferenceRegistry()
        now = datetime.now(UTC)
        value = now.astimezone(timezone(timedelta(hours=3)))
        live = registry.register(
            "clock_test",
            "live",
            "Live",
            lambda: ClockSample(value, now),
            stale_after_seconds=5.0,
        )
        estimated = registry.register(
            "clock_test",
            "estimated",
            "Estimated",
            lambda: ClockSample(value, now, estimated=True),
            stale_after_seconds=30.0,
        )
        old_sample_time = now - timedelta(seconds=60)
        stale = registry.register(
            "clock_test",
            "stale",
            "Stale",
            lambda: ClockSample(value, old_sample_time, estimated=True),
            stale_after_seconds=30.0,
        )
        invalid_provider = registry.register(
            "clock_test", "invalid_provider", "Invalid provider", lambda: value
        )
        unknown_freshness = registry.register(
            "clock_test",
            "unknown_freshness",
            "Unknown freshness",
            lambda: ClockSample(value, old_sample_time),
        )
        future_sample = registry.register(
            "clock_test",
            "future_sample",
            "Future sample",
            lambda: ClockSample(value, now + timedelta(days=1)),
            stale_after_seconds=5.0,
        )

        live_status = registry.status(live)
        estimated_status = registry.status(estimated)
        stale_status = registry.status(stale)
        invalid_status = registry.status(invalid_provider)
        unknown_status = registry.status(unknown_freshness)
        future_status = registry.status(future_sample)

        self.assertIs(live_status.quality, ClockQuality.LIVE)
        self.assertEqual(live_status.value, value)
        self.assertIsNotNone(live_status.updated_at)
        self.assertIsNotNone(live_status.age_seconds)
        self.assertLess(live_status.age_seconds, 2.0)
        self.assertIs(estimated_status.quality, ClockQuality.ESTIMATED)
        self.assertIs(stale_status.quality, ClockQuality.STALE)
        self.assertGreaterEqual(stale_status.age_seconds, 60.0)
        self.assertIs(stale_status.quality, ClockQuality.STALE)
        self.assertIs(invalid_status.quality, ClockQuality.UNAVAILABLE)
        self.assertIs(unknown_status.quality, ClockQuality.UNKNOWN)
        self.assertGreaterEqual(unknown_status.age_seconds, 60.0)
        self.assertEqual(future_status.value, value)
        self.assertIsNone(future_status.age_seconds)
        self.assertIs(future_status.quality, ClockQuality.UNKNOWN)

    def test_system_clock_references_are_reserved_for_the_host(self) -> None:
        registry = ClockReferenceRegistry()
        value = datetime.now(UTC)
        identifier = registry._register_system("local", "Local", lambda: value)

        with self.assertRaisesRegex(ValueError, "reserved for the host"):
            registry.register("system", "local", "Replacement", lambda: None)
        with self.assertRaisesRegex(ValueError, "owned by the host"):
            registry.unregister(identifier)
        with self.assertRaisesRegex(ValueError, "owned by the host"):
            registry.unregister_owner("system")

        self.assertEqual(registry.references()[0].title, "Local")

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

        def update(change):
            configuration = change(previous)
            saved.append(configuration)
            return configuration

        runtime.settings = SimpleNamespace(
            current=previous,
            update=update,
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
        runtime.clock_references = registry._scope("siha_teknofest_server")
        runtime._clock_reference_ids = set()
        sample_time = datetime(2026, 10, 23, tzinfo=UTC)
        sample = SimpleNamespace(
            received_monotonic=time.monotonic(),
            received_at=datetime.now(UTC),
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
        self.assertEqual(registry.status(reference.identifier).value, sample_time)

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
