from __future__ import annotations

from datetime import UTC, datetime, timedelta
import threading
import time
from types import SimpleNamespace
import unittest

from fake_teknofest_server import create_server
from src.models.competition import CompetitionServerConfig, KamikazeEvent, LockEvent
from src.models.geometry import Point
from src.models.mission import MissionState
from src.models.telemetry import TargetTrack, TelemetrySnapshot
from src.services.competition import TeknofestCompetitionService
from src.services.logging_service import LoggingService
from src.services.processing.mission import MissionStateService
from src.services.processing.telemetry import MutableTelemetryService


class CompetitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(port=0, quiet=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        host, port = self.server.server_address[:2]
        self.telemetry = MutableTelemetryService()
        self.mission = MissionStateService(initial_state=MissionState())
        self.vehicle = SimpleNamespace(connected=True)
        self.logs = LoggingService()
        self.freshness_timeout_s = 0.8
        self.service = TeknofestCompetitionService(
            CompetitionServerConfig(
                base_url=f"http://{host}:{port}",
                username="kirlangic",
                password="kirlangic-demo",
                team_number=1,
                timeout_s=1,
                telemetry_period_ms=500,
                auto_connect=True,
            ),
            self.telemetry,
            self.mission,
            self.vehicle,  # type: ignore[arg-type]
            self.logs.child("TEKNOFEST"),
            telemetry_freshness_timeout_s=self.freshness_timeout_s,
        )
        self.telemetry.start()
        self.mission.start()

    def tearDown(self) -> None:
        self.service.stop()
        self.mission.stop()
        self.telemetry.stop()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=1)

    def test_client_receives_27_nearby_competitors_and_publishes_map_contacts(self) -> None:
        received_at = datetime.now(UTC)
        own = TelemetrySnapshot.zero().copy(
            team_id=1,
            latitude=39.868,
            longitude=32.7485,
            altitude_msl=1100.0,
            altitude_agl=137.0,
            ground_speed=31.0,
            battery_percent=87,
            heading=92.0,
            pitch=1.2,
            roll=-3.4,
            flight_mode="AUTO",
            position_valid=True,
            telemetry_link=True,
            autopilot_link=True,
            timestamp=received_at,
            uav_time=received_at,
            position_received_at=received_at,
            uav_time_received_at=received_at,
            target_track=TargetTrack(
                locked=True,
                center_x=300,
                center_y=230,
                width=30,
                height=43,
                vehicle_source="jetson-vision",
            ),
        )
        self.telemetry.push(own)
        self.service.start()

        self.assertTrue(self._wait_until(lambda: self.service.state.competitor_count == 27))
        contacts = self.mission.state.contacts
        self.assertEqual(len(contacts), 27)
        self.assertEqual({contact.source for contact in contacts}, {"teknofest"})
        self.assertEqual({contact.metadata["team_number"] for contact in contacts}, set(range(2, 29)))
        own_point = Point(own.latitude, own.longitude)
        distances = [
            own_point.distance_to(Point(contact.latitude, contact.longitude))
            for contact in contacts
        ]
        self.assertLess(max(distances), 2_000.0)
        self.assertTrue(self.telemetry.current.server_link)
        self.assertIsNotNone(self.telemetry.current.server_time)
        self.assertTrue(self._wait_until(lambda: self.service.state.hss_count == 4))
        self.assertTrue(self.service.state.qr_available)
        server_overlays = {
            item.overlay_id: item for item in self.mission.state.overlays
            if str(item.metadata.get("source", "")).startswith("teknofest-")
        }
        self.assertEqual(len([key for key in server_overlays if key.startswith("teknofest-hss-")]), 4)
        self.assertIn("teknofest-qr", server_overlays)

        sent = self.server.state.telemetry_history[-1]
        self.assertEqual(sent["iha_irtifa"], 137.0)
        self.assertEqual(sent["iha_kilitlenme"], 1)
        self.assertEqual(sent["hedef_genislik"], 30)
        self.assertEqual(sent["hedef_yukseklik"], 43)
        self.assertEqual(sent["gps_saati"]["saat"], own.uav_time.hour)
        actions = {entry.action for entry in self.logs.history}
        self.assertIn("competition.telemetry_tx", actions)
        self.assertIn("competition.telemetry_rx", actions)
        self.assertIn("competition.hss_rx", actions)
        self.assertIn("competition.qr_rx", actions)

        self.assertTrue(
            self._wait_until(
                lambda: any(
                    int(contact.metadata["data_age_ms"]) > 0
                    for contact in self.mission.state.contacts
                ),
                timeout=4.0,
            )
        )
        self.assertTrue(all(contact.metadata["data_age_ms"] >= 0 for contact in self.mission.state.contacts))

    def test_disconnect_clears_only_teknofest_contacts(self) -> None:
        from src.models.mission import MapContact

        self.mission.upsert_contact(MapContact("radar-1", "Radar", 39.86, 32.74, source="radar"))
        self.service.disconnect()
        self.assertEqual([item.contact_id for item in self.mission.state.contacts], ["radar-1"])

    def test_vehicle_events_are_forwarded_once_and_ui_cannot_create_them(self) -> None:
        self.service.start()
        self.assertTrue(self._wait_until(lambda: self.service.state.connected))
        ended_at = datetime.now(UTC)
        lock = LockEvent(ended_at, True, "jetson-lock-controller")
        kamikaze = KamikazeEvent(
            ended_at - timedelta(seconds=14),
            ended_at,
            "teknofest2026",
            "jetson-qr-reader",
        )
        self.service.submit_lock_event(lock)
        self.service.submit_kamikaze_event(kamikaze)

        self.assertEqual(len(self.server.state.received_lock_events), 1)
        self.assertEqual(len(self.server.state.received_kamikaze_events), 1)
        self.assertEqual(
            self.server.state.received_kamikaze_events[0]["qrMetni"],
            "teknofest2026",
        )
        actions = {entry.action for entry in self.logs.history}
        self.assertTrue(
            {
                "competition.lock_tx",
                "competition.lock_ack",
                "competition.kamikaze_tx",
                "competition.kamikaze_ack",
            }
            <= actions
        )
        with self.assertRaises(ValueError):
            LockEvent(ended_at, True, "ui")
        with self.assertRaises(ValueError):
            KamikazeEvent(ended_at, ended_at, "invented", "yki")

    def test_telemetry_waits_for_direct_vehicle_gps_time(self) -> None:
        self.telemetry.push(
            TelemetrySnapshot.zero().copy(
                latitude=39.868,
                longitude=32.7485,
                altitude_agl=100.0,
                position_valid=True,
                telemetry_link=True,
                autopilot_link=True,
            )
        )
        self.service.start()
        self.assertTrue(self._wait_until(lambda: self.service.state.connected))
        time.sleep(0.7)
        self.assertEqual(len(self.server.state.telemetry_history), 0)

    def test_telemetry_is_never_sent_while_vehicle_connection_is_down(self) -> None:
        received_at = datetime.now(UTC)
        self.vehicle.connected = False
        self.telemetry.push(
            TelemetrySnapshot.zero().copy(
                latitude=39.868,
                longitude=32.7485,
                altitude_agl=100.0,
                position_valid=True,
                telemetry_link=True,
                autopilot_link=True,
                uav_time=received_at,
                position_received_at=received_at,
                uav_time_received_at=received_at,
            )
        )

        self.service.start()
        self.assertTrue(self._wait_until(lambda: self.service.state.connected))
        time.sleep(0.7)

        self.assertEqual(len(self.server.state.telemetry_history), 0)
        self.assertFalse(self.service.state.telemetry_tx_active)
        blocked = [
            entry for entry in self.logs.history
            if entry.action == "competition.telemetry_blocked"
        ]
        self.assertTrue(blocked)
        self.assertEqual(blocked[-1].details["reason"], "vehicle_connection_down")

    def test_telemetry_transmission_stops_when_vehicle_feed_becomes_stale(self) -> None:
        received_at = datetime.now(UTC)
        self.telemetry.push(
            TelemetrySnapshot.zero().copy(
                latitude=39.868,
                longitude=32.7485,
                altitude_agl=100.0,
                position_valid=True,
                telemetry_link=True,
                autopilot_link=True,
                uav_time=received_at,
                position_received_at=received_at,
                uav_time_received_at=received_at,
            )
        )
        self.service.start()
        self.assertTrue(
            self._wait_until(lambda: len(self.server.state.telemetry_history) >= 1)
        )
        self.assertTrue(self.service.state.telemetry_tx_active)

        self.assertTrue(
            self._wait_until(
                lambda: not self.service.state.telemetry_tx_active,
                timeout=self.freshness_timeout_s + 1.5,
            )
        )
        sent_count = len(self.server.state.telemetry_history)
        time.sleep(0.65)
        self.assertEqual(len(self.server.state.telemetry_history), sent_count)
        blocked = [
            entry for entry in self.logs.history
            if entry.action == "competition.telemetry_blocked"
        ]
        self.assertTrue(blocked)
        self.assertIn(
            blocked[-1].details["reason"],
            {"position_stale", "vehicle_gps_time_stale"},
        )

    @staticmethod
    def _wait_until(predicate, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return bool(predicate())


if __name__ == "__main__":
    unittest.main()
