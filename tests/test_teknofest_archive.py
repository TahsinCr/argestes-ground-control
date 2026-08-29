from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from src.core.events import EventBus
from src.models.competition import (
    CompetitionClockSample,
    CompetitionConnectionState,
    CompetitionServerConfig,
    CompetitionServerTime,
    CompetitorTelemetry,
    HssZone,
    QrCoordinate,
)
from src.services.teknofest_archive import TeknofestSessionArchiveService


class _Competition:
    def __init__(self) -> None:
        self.config = CompetitionServerConfig(
            identifier="primary",
            name="Ana sunucu",
            base_url="http://10.0.0.10:10001",
            username="team",
            password="secret",
            team_number=7,
        )
        self.state = CompetitionConnectionState(
            connected=False,
            authenticated=False,
            running=True,
            endpoint=self.config.base_url,
            team_number=7,
        )
        self.competitors = ()
        self.hss_zones = ()
        self.qr_coordinate = None
        self.config_updated = EventBus[CompetitionServerConfig]()
        self.changed = EventBus[CompetitionConnectionState]()
        self.clock_updated = EventBus[CompetitionClockSample]()
        self.competitors_updated = EventBus[tuple[CompetitorTelemetry, ...]]()
        self.hss_updated = EventBus[tuple[HssZone, ...]]()
        self.qr_updated = EventBus[QrCoordinate | None]()


class TeknofestSessionArchiveTests(unittest.TestCase):
    def test_records_server_switch_disconnect_and_reference_data_in_one_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            competition = _Competition()
            service = TeknofestSessionArchiveService(Path(directory), competition)
            service.start()
            session_dir = service.session_dir
            self.assertIsNotNone(session_dir)

            received_at = datetime.now(UTC)
            competitors = (
                CompetitorTelemetry.from_payload(
                    {
                        "takim_numarasi": 12,
                        "iha_enlem": 39.9,
                        "iha_boylam": 32.8,
                        "iha_irtifa": 120.0,
                        "iha_dikilme": 3.0,
                        "iha_yonelme": 180.0,
                        "iha_yatis": -2.0,
                        "iha_hizi": 24.0,
                        "zaman_farki": 15,
                        "extra_server_field": "preserved",
                    },
                    received_at,
                ),
            )
            competition.competitors_updated.publish(competitors)
            competition.hss_updated.publish((HssZone(4, 39.91, 32.81, 75.0),))
            competition.hss_updated.publish(())
            competition.qr_updated.publish(QrCoordinate(39.92, 32.82))

            disconnected = CompetitionConnectionState(
                connected=False,
                authenticated=False,
                running=True,
                endpoint=competition.config.base_url,
                team_number=7,
                last_error="timeout",
                session_health="offline",
            )
            competition.changed.publish(disconnected)
            competition.config = CompetitionServerConfig(
                identifier="backup",
                name="Yedek sunucu",
                base_url="http://10.0.0.11:10001",
                username="team",
                password="another-secret",
                team_number=7,
            )
            competition.config_updated.publish(competition.config)
            competition.changed.publish(
                CompetitionConnectionState(
                    connected=True,
                    authenticated=True,
                    running=True,
                    endpoint=competition.config.base_url,
                    team_number=7,
                    session_health="online",
                )
            )
            service.stop()

            self.assertEqual(service.session_dir, session_dir)
            records = self._records(session_dir)
            events = [record["event"] for record in records]
            self.assertIn("competitors.snapshot", events)
            self.assertIn("hss.updated", events)
            self.assertIn("hss.cleared", events)
            self.assertIn("qr.updated", events)
            self.assertEqual(events.count("server.selected"), 2)
            self.assertEqual(records[-1]["event"], "session.stopped")

            competitor_records = [
                record
                for record in records
                if record["event"] == "competitors.snapshot" and not record["data"]["initial"]
            ]
            self.assertEqual(
                competitor_records[0]["data"]["vehicles"][0]["raw"]["extra_server_field"],
                "preserved",
            )
            selected = [record for record in records if record["event"] == "server.selected"]
            self.assertEqual(selected[-1]["data"]["name"], "Yedek sunucu")
            self.assertNotIn("password", selected[-1]["data"])

    def test_records_clock_samples_and_ignores_success_timestamp_only_state_noise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            competition = _Competition()
            service = TeknofestSessionArchiveService(Path(directory), competition)
            service.start()
            initial_state_count = self._event_count(service.session_dir, "connection.state")

            base = datetime.now(UTC)
            competition.changed.publish(
                CompetitionConnectionState(
                    connected=False,
                    authenticated=False,
                    running=True,
                    endpoint=competition.config.base_url,
                    team_number=7,
                    last_success_at=base,
                )
            )
            competition.clock_updated.publish(
                CompetitionClockSample(
                    CompetitionServerTime(28, 9, 10, 11, 123),
                    received_monotonic=4.2,
                    request_started_monotonic=4.1,
                    resolved_at=base,
                )
            )
            service.stop()

            self.assertEqual(
                self._event_count(service.session_dir, "connection.state"),
                initial_state_count,
            )
            clocks = [
                record
                for record in self._records(service.session_dir)
                if record["event"] == "server.clock"
            ]
            self.assertEqual(clocks[0]["data"]["sunucusaati"]["milisaniye"], 123)
            self.assertAlmostEqual(clocks[0]["data"]["round_trip_ms"], 100.0)

    @staticmethod
    def _records(session_dir: Path | None) -> list[dict]:
        assert session_dir is not None
        return [
            json.loads(line)
            for line in (session_dir / "teknofest.jsonl").read_text(encoding="utf-8").splitlines()
        ]

    @classmethod
    def _event_count(cls, session_dir: Path | None, event: str) -> int:
        return sum(record["event"] == event for record in cls._records(session_dir))


if __name__ == "__main__":
    unittest.main()
