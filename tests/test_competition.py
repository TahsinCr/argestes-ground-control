from __future__ import annotations

from datetime import UTC, datetime, timedelta
import threading
import time
from types import SimpleNamespace
import unittest

from fake_teknofest_server import create_server
from src.core.events import EventBus, Subscription
from src.models.competition import (
    CompetitionServerTime,
    CompetitionServerConfig,
    HssZone,
    KamikazeEvent,
    LockEvent,
)
from src.models.geometry import Point
from src.models.mission import MissionState
from src.models.telemetry import TargetTrack, TelemetrySnapshot
from src.models.orin import OrinConnectionState, OrinMessage
from src.services.competition import (
    CompetitionHttpError,
    CompetitionProtocolError,
    TeknofestCompetitionService,
)
from src.services.logging_service import LoggingService
from src.services.onboard import OnboardMissionService
from src.services.planner import PlannerService
from src.services.vehicle.telemetry import MutableTelemetryService


class _TargetLockOrin:
    def __init__(self) -> None:
        self.changed = EventBus[OrinConnectionState]()
        self.state = OrinConnectionState(alive=False, version="test")
        self.connected = False
        self.handlers: dict[str, object] = {}

    def register_handler(self, packet_type, handler):
        key = str(packet_type).lower()
        self.handlers[key] = handler
        return Subscription(1, lambda: self.handlers.pop(key, None))


class CompetitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(port=0, quiet=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        host, port = self.server.server_address[:2]
        self.telemetry = MutableTelemetryService()
        self.planner = PlannerService(initial_state=MissionState())
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
            self.planner,
            self.vehicle,  # type: ignore[arg-type]
            self.logs.child("TEKNOFEST"),
            telemetry_freshness_timeout_s=self.freshness_timeout_s,
        )
        self.telemetry.start()
        self.planner.start()

    def tearDown(self) -> None:
        self.service.stop()
        self.planner.stop()
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
        contacts = self.planner.state.contacts
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
        self.assertTrue(self._wait_until(lambda: self.service.state.qr_available))
        server_overlays = {
            item.overlay_id: item for item in self.planner.state.overlays
            if str(item.metadata.get("source", "")).startswith("teknofest-")
        }
        self.assertEqual(len([key for key in server_overlays if key.startswith("teknofest-hss-")]), 4)
        self.assertIn("teknofest-qr", server_overlays)
        hss_distances = [
            own_point.distance_to(Point(item.points[0][0], item.points[0][1]))
            for key, item in server_overlays.items()
            if key.startswith("teknofest-hss-")
        ]
        self.assertLessEqual(max(hss_distances), 901.0)
        hss_request_count = sum(
            entry.action == "competition.hss_request_tx"
            for entry in self.logs.history
        )
        hss_snapshot = {
            item.overlay_id: item.to_dict()
            for item in self.planner.state.overlays
            if item.overlay_id.startswith("teknofest-hss-")
        }
        self.service.refresh_reference_data()
        self.assertEqual(
            sum(
                entry.action == "competition.hss_request_tx"
                for entry in self.logs.history
            ),
            hss_request_count + 1,
        )
        self.assertEqual(
            {
                item.overlay_id: item.to_dict()
                for item in self.planner.state.overlays
                if item.overlay_id.startswith("teknofest-hss-")
            },
            hss_snapshot,
        )

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
                    for contact in self.planner.state.contacts
                ),
                timeout=4.0,
            )
        )
        self.assertTrue(all(contact.metadata["data_age_ms"] >= 0 for contact in self.planner.state.contacts))

    def test_outbound_nonnegative_fields_are_clamped_before_transmission(self) -> None:
        received_at = datetime.now(UTC)
        snapshot = TelemetrySnapshot.zero().copy(
            latitude=40.225611,
            longitude=28.8749911,
            altitude_agl=-0.12,
            ground_speed=-0.04,
            position_valid=True,
            telemetry_link=True,
            autopilot_link=True,
            timestamp=received_at,
            uav_time=received_at,
            position_received_at=received_at,
            uav_time_received_at=received_at,
        )

        payload = self.service._telemetry_payload(snapshot, received_at)

        self.assertEqual(payload["iha_irtifa"], 0.0)
        self.assertEqual(payload["iha_hiz"], 0.0)

    def test_hss_is_polled_each_second_and_removed_after_empty_response(self) -> None:
        received_at = datetime.now(UTC)
        self.telemetry.push(
            TelemetrySnapshot.zero().copy(
                latitude=40.225611,
                longitude=28.8749911,
                altitude_agl=100.0,
                position_valid=True,
                telemetry_link=True,
                autopilot_link=True,
                timestamp=received_at,
                uav_time=received_at,
                position_received_at=received_at,
                uav_time_received_at=received_at,
            )
        )
        self.service.start()
        self.assertTrue(self._wait_until(lambda: self.service.state.hss_count == 4))
        initial_requests = sum(
            entry.action == "competition.hss_request_tx"
            for entry in self.logs.history
        )
        self.assertTrue(
            self._wait_until(
                lambda: sum(
                    entry.action == "competition.hss_request_tx"
                    for entry in self.logs.history
                ) > initial_requests,
                timeout=1.5,
            )
        )

        with self.server.state.lock:
            self.server.state.hss_zones = ()

        self.assertTrue(
            self._wait_until(
                lambda: self.service.state.hss_count == 0,
                timeout=1.5,
            )
        )
        self.assertFalse(
            any(
                item.overlay_id.startswith("teknofest-hss-")
                for item in self.planner.state.overlays
            )
        )

        with self.server.state.lock:
            self.server.state.hss_zones = (
                {
                    "id": 20,
                    "hssEnlem": 40.226,
                    "hssBoylam": 28.876,
                    "hssYaricap": 75,
                },
            )

        self.assertTrue(
            self._wait_until(
                lambda: self.service.state.hss_count == 1,
                timeout=1.5,
            )
        )
        self.assertTrue(
            any(
                item.overlay_id == "teknofest-hss-20"
                for item in self.planner.state.overlays
            )
        )

    def test_disconnect_clears_only_teknofest_contacts(self) -> None:
        from src.models.mission import MapContact

        self.planner.upsert_contact(MapContact("radar-1", "Radar", 39.86, 32.74, source="radar"))
        self.service.disconnect()
        self.assertEqual([item.contact_id for item in self.planner.state.contacts], ["radar-1"])

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
        with self.assertRaisesRegex(
            CompetitionProtocolError,
            "güvenli gönderim bütçesinde değil",
        ):
            self.service.submit_kamikaze_event(
                KamikazeEvent(
                    ended_at - timedelta(seconds=5),
                    ended_at - timedelta(seconds=3),
                    "late-qr",
                    "jetson-qr-reader",
                )
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

    def test_orin_target_lock_reaches_periodic_telemetry_and_result_endpoint(
        self,
    ) -> None:
        received_at = datetime.now(UTC)
        self.telemetry.push(
            TelemetrySnapshot.zero().copy(
                team_id=1,
                latitude=39.868,
                longitude=32.7485,
                altitude_agl=137.0,
                altitude_msl=1100.0,
                ground_speed=31.0,
                battery_percent=87,
                heading=92.0,
                flight_mode="AUTO",
                position_valid=True,
                telemetry_link=True,
                autopilot_link=True,
                timestamp=received_at,
                uav_time=received_at,
                position_received_at=received_at,
                uav_time_received_at=received_at,
            )
        )
        self.service.start()
        self.assertTrue(self._wait_until(lambda: self.service.state.connected))
        orin = _TargetLockOrin()
        onboard = OnboardMissionService(
            orin,  # type: ignore[arg-type]
            self.service,
            self.logs.child("ONBOARD"),
            telemetry=self.telemetry,
        )
        onboard._TARGET_LOCK_STATUS_TIMEOUT_S = 3.0
        onboard.start()
        try:
            now = self.service.estimated_server_time()
            self.assertIsNotNone(now)
            assert now is not None
            clock = {
                "saat": now.hour,
                "dakika": now.minute,
                "saniye": now.second,
                "milisaniye": now.microsecond // 1000,
            }
            status_handler = orin.handlers["target.lock.status"]
            status_handler(
                OrinMessage(
                    "target.lock.status",
                    {
                        "state": "locked",
                        "frame_sequence": 120,
                        "target_id": 7,
                        "confidence": 0.91,
                        "hedef_merkez_X": 480,
                        "hedef_merkez_Y": 270,
                        "hedef_genislik": 192,
                        "hedef_yukseklik": 100,
                        "error_x": 0.0,
                        "error_y": -0.01,
                        "lock_progress": 1.0,
                        "in_zone": True,
                        "locked": True,
                        "autonomous": True,
                        "time_utc": datetime.now(UTC).isoformat(),
                    },
                    900,
                    1,
                    191,
                )
            )
            self.assertTrue(
                self._wait_until(
                    lambda: any(
                        item.get("iha_kilitlenme") == 1
                        and item.get("hedef_merkez_X") == 480
                        and item.get("hedef_merkez_Y") == 270
                        and item.get("hedef_genislik") == 192
                        and item.get("hedef_yukseklik") == 100
                        for item in self.server.state.telemetry_history
                    ),
                    timeout=1.5,
                )
            )

            report_payload = {
                "report_id": "e2e-session:42:120:lock",
                "mission_id": 42,
                "target_id": 7,
                "kilitlenmeBitisZamani": clock,
                "otonom_kilitlenme": 1,
            }
            report_handler = orin.handlers["target.lock.report"]
            first = report_handler(
                OrinMessage("target.lock.report", report_payload, 901, 1, 191)
            )
            duplicate = report_handler(
                OrinMessage("target.lock.report", report_payload, 902, 1, 191)
            )

            self.assertEqual(first.payload["execution"], "submitted")
            self.assertEqual(dict(first.payload), dict(duplicate.payload))
            self.assertEqual(
                self.server.state.received_lock_events,
                [
                    {
                        "kilitlenmeBitisZamani": clock,
                        "otonom_kilitlenme": 1,
                    }
                ],
            )
        finally:
            onboard.stop()

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
        self.assertTrue(
            self._wait_until(lambda: self.service.state.telemetry_tx_active)
        )

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

    def test_repeated_connection_failures_are_rate_limited(self) -> None:
        self.service._log_connection_failure("connection refused")
        self.service._log_connection_failure("connection refused")
        failures = [
            entry
            for entry in self.logs.history
            if entry.action == "competition.connection_failed"
        ]
        self.assertEqual(len(failures), 1)

        self.service._log_connection_failure("timeout")
        failures = [
            entry
            for entry in self.logs.history
            if entry.action == "competition.connection_failed"
        ]
        self.assertEqual(len(failures), 2)

        self.service._last_connection_error_signature = "connection refused"
        self.service._last_connection_error_log_at = time.monotonic() - 31.0
        self.service._log_connection_failure("connection refused")
        failures = [
            entry
            for entry in self.logs.history
            if entry.action == "competition.connection_failed"
        ]
        self.assertEqual(len(failures), 3)

    def test_missing_hss_response_retains_last_verified_zones(self) -> None:
        self.service._replace_hss_zones(
            (HssZone(1, 39.868, 32.7485, 75.0),)
        )
        self.service._hss_response_available = True
        original_request = self.service._request_json

        def fail_hss(method, path, payload=None):
            if path == "/api/hss_koordinatlari":
                raise ConnectionError("HSS response missing")
            return original_request(method, path, payload)

        self.service._request_json = fail_hss  # type: ignore[method-assign]
        self.service._poll_hss_zones()
        self.service._poll_hss_zones()

        self.assertEqual(
            self.service.hss_zones,
            (HssZone(1, 39.868, 32.7485, 75.0),),
        )
        self.assertTrue(
            any(
                item.overlay_id.startswith("teknofest-hss-")
                for item in self.planner.state.overlays
            )
        )
        unavailable = [
            entry
            for entry in self.logs.history
            if entry.action == "competition.hss_unavailable"
        ]
        self.assertEqual(len(unavailable), 1)

    def test_qr_endpoint_failure_does_not_drop_authenticated_session(self) -> None:
        self.service._publish_state(
            connected=True,
            authenticated=True,
            running=True,
            session_health="authenticated",
        )

        def fail_qr(_method, path, _payload=None):
            if path == "/api/qr_koordinati":
                raise CompetitionHttpError(500, "temporary QR failure")
            raise AssertionError(path)

        self.service._request_json = fail_qr  # type: ignore[method-assign]
        self.service._poll_qr_coordinate()
        self.service._poll_qr_coordinate()

        state = self.service.state
        self.assertTrue(state.connected)
        self.assertTrue(state.authenticated)
        self.assertEqual(state.session_health, "authenticated")
        self.assertEqual(state.qr_health, "degraded")
        self.assertEqual(
            sum(
                entry.action == "competition.qr_unavailable"
                for entry in self.logs.history
            ),
            1,
        )

    def test_qr_unauthorized_response_is_forwarded_for_session_recovery(self) -> None:
        self.service._request_json = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                CompetitionHttpError(401, "login required")
            )
        )
        with self.assertRaises(CompetitionHttpError):
            self.service._poll_qr_coordinate()

    def test_server_clock_preserves_day_only_protocol_and_advances_monotonically(self) -> None:
        now = datetime.now(UTC)
        protocol_time = CompetitionServerTime(
            day=now.day,
            hour=now.hour,
            minute=now.minute,
            second=now.second,
            millisecond=123,
        )
        resolved = protocol_time.resolve_near(now)
        self.assertEqual(resolved.day, now.day)
        first = self.service._update_server_clock(
            {
                "gun": now.day,
                "saat": now.hour,
                "dakika": now.minute,
                "saniye": now.second,
                "milisaniye": 123,
            }
        )
        self.assertIsNotNone(first)
        time.sleep(0.01)
        second = self.service.estimated_server_time()
        self.assertIsNotNone(second)
        self.assertGreater(second, first)

    def test_http_response_body_is_bounded(self) -> None:
        class OversizedResponse:
            headers = {"Content-Length": str(3 * 1024 * 1024)}

            def read(self, _limit: int) -> bytes:
                raise AssertionError("oversized body must not be read")

        with self.assertRaises(CompetitionProtocolError):
            self.service._read_limited(OversizedResponse())

    def test_identical_state_updates_are_not_republished(self) -> None:
        states = []
        subscription = self.service.changed.subscribe(states.append)
        try:
            self.service._publish_state(running=False)
            self.assertEqual(states, [])
            self.service._publish_state(running=True)
            self.assertEqual(len(states), 1)
            self.service._publish_state(running=True)
            self.assertEqual(len(states), 1)
        finally:
            subscription.cancel()

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
