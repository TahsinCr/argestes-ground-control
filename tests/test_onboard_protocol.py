from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.core.events import EventBus, Subscription
from src.models.competition import QrCoordinate
from src.models.onboard import OnboardMissionState
from src.models.orin import OrinCommandReceipt, OrinConnectionState, OrinMessage
from src.services.logging_service import LoggingService
from src.services.onboard import OnboardMissionService


class FakeOrinService:
    def __init__(self) -> None:
        self.changed = EventBus[OrinConnectionState]()
        self.received = EventBus[OrinMessage]()
        self.remote_logs = EventBus()
        self.remote_log_batches = EventBus()
        self.state = OrinConnectionState(alive=False, version="0.2-beta")
        self.connected = True
        self.handlers: dict[str, object] = {}
        self.responses: list[OrinCommandReceipt | Exception] = []
        self.requests: list[tuple[str, dict]] = []

    def register_handler(self, packet_type, handler):
        key = str(packet_type).lower()
        self.handlers[key] = handler
        return Subscription(1, lambda: self.handlers.pop(key, None))

    def issue(self, packet_type, payload=None, *, timeout=None):
        del timeout
        self.requests.append((packet_type, dict(payload or {})))
        if packet_type == "kamikaze.target.update":
            target_payload = dict(payload or {})
            return OrinCommandReceipt(
                len(self.requests),
                True,
                "Target updated",
                {
                    "execution": "updated",
                    "target_revision": 1,
                    "target": {
                        "latitude_deg": target_payload["latitude_deg"],
                        "longitude_deg": target_payload["longitude_deg"],
                        "plate_height_m": 3.0,
                    },
                },
            )
        if not self.responses:
            return OrinCommandReceipt(len(self.requests), True, "ok", {})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def emit(self, packet_type: str, payload: dict, packet_id: int = 1) -> None:
        handler = self.handlers[packet_type]
        handler(OrinMessage(packet_type, payload, packet_id, 1, 191))


class FakeCompetitionService:
    def __init__(self) -> None:
        self.state = SimpleNamespace(connected=True, authenticated=True)
        self.qr_coordinate: QrCoordinate | None = QrCoordinate(39.925533, 32.866287)
        self.refresh_count = 0

    def refresh_qr_coordinate(self) -> None:
        self.refresh_count += 1


class OnboardMissionProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orin = FakeOrinService()
        self.competition = FakeCompetitionService()
        self.logs = LoggingService()
        self.service = OnboardMissionService(
            self.orin,  # type: ignore[arg-type]
            self.competition,  # type: ignore[arg-type]
            self.logs.child("ONBOARD"),
        )
        self.service.start()

    def tearDown(self) -> None:
        self.service.stop()

    def test_kamikaze_start_uses_contract_payload_and_waits_for_guided(self) -> None:
        self.orin.responses.append(
            OrinCommandReceipt(
                101,
                True,
                "Command accepted",
                {
                    "request_id": 101,
                    "task": "kamikaze.execute",
                    "mission_id": 42,
                    "execution": "starting",
                    "waiting_for_mode": True,
                    "vehicle_mode": "AUTO",
                },
            )
        )

        receipt = self.service.start_task("KAMIKAZE", source="ui")

        self.assertEqual(receipt.request_id, 101)
        self.assertEqual(self.competition.refresh_count, 1)
        self.assertEqual(self.orin.requests[-2][0], "kamikaze.target.update")
        target_payload = self.orin.requests[-2][1]
        self.assertEqual(target_payload["latitude_deg"], 39.925533)
        self.assertEqual(target_payload["longitude_deg"], 32.866287)
        self.assertEqual(target_payload["source"], "teknofest")
        self.assertTrue(target_payload["fetched_at_utc"].endswith("Z"))
        self.assertEqual(
            self.orin.requests[-1],
            ("task.start", {"task": "KAMIKAZE", "source": "ui"}),
        )
        self.assertTrue(self.service.state.waiting_for_guided)
        self.assertEqual(self.service.state.phase, "starting")
        self.assertEqual(self.service.state.mission_id, 42)
        self.assertTrue(self.service.state.target_ready)
        self.assertEqual(self.service.state.target_revision, 1)

    def test_target_rejection_blocks_kamikaze_start(self) -> None:
        original_issue = self.orin.issue

        def reject_target(packet_type, payload=None, *, timeout=None):
            if packet_type == "kamikaze.target.update":
                self.orin.requests.append((packet_type, dict(payload or {})))
                return OrinCommandReceipt(
                    77,
                    False,
                    "Target rejected",
                    {"execution": "rejected", "error_type": "ValueError"},
                )
            return original_issue(packet_type, payload, timeout=timeout)

        self.orin.issue = reject_target

        with self.assertRaisesRegex(RuntimeError, "reddedildi"):
            self.service.start_task("KAMIKAZE")

        self.assertEqual(
            [packet_type for packet_type, _payload in self.orin.requests],
            ["kamikaze.target.update"],
        )
        self.assertFalse(self.service.state.target_ready)
        self.assertTrue(
            any(entry.action == "kamikaze.target_failed" for entry in self.logs.history)
        )

    def test_missing_teknofest_target_blocks_kamikaze_start(self) -> None:
        self.competition.qr_coordinate = None

        with self.assertRaisesRegex(RuntimeError, "QR hedef koordinatı"):
            self.service.start_task("KAMIKAZE")

        self.assertEqual(self.orin.requests, [])

    def test_transition_and_terminal_result_are_not_confused_with_ack(self) -> None:
        self.orin.emit(
            "mission.transition",
            {
                "task": "kamikaze.execute",
                "mission_id": 42,
                "previous": "starting",
                "current": "running",
            },
        )
        self.assertEqual(self.service.state.phase, "running")
        self.assertFalse(self.service.state.result_received)

        self.orin.emit(
            "mission.result",
            {
                "task": "kamikaze.execute",
                "mission_id": 42,
                "execution": "succeeded",
                "snapshot": {
                    "mission_id": 42,
                    "task": "kamikaze.execute",
                    "phase": "succeeded",
                    "duration_seconds": 15.0,
                    "result": {"qr_text": "QR-42", "end_time_utc": "now"},
                },
            },
        )
        self.assertEqual(self.service.state.phase, "succeeded")
        self.assertTrue(self.service.state.result_received)
        self.assertEqual(self.service.state.qr_text, "QR-42")

    def test_both_qr_events_are_inert_deduplicated_and_logged(self) -> None:
        malicious = '<img src=x onerror="boom()">; rm -rf /'
        payload = {
            "event": "QR_DECODED_DURING_PULL_UP",
            "time_utc": "2026-08-07T10:20:40Z",
            "qr_text": malicious,
            "altitude_m": 48.1,
        }
        self.orin.emit("kamikaze.event", payload)
        self.orin.emit("kamikaze.event", payload)

        state = self.service.state
        self.assertEqual(state.qr_text, malicious)
        self.assertEqual(state.qr_status, "decoded")
        self.assertEqual(len(state.timeline), 1)
        self.assertTrue(any(entry.action == "kamikaze.qr_received" for entry in self.logs.history))

    def test_result_conflict_preserves_both_qr_values(self) -> None:
        self.orin.emit("kamikaze.event", {"event": "QR_DECODED", "qr_text": "LIVE"})
        self.orin.emit(
            "mission.result",
            {
                "execution": "succeeded",
                "snapshot": {"result": {"qr_text": "RESULT"}},
            },
        )
        self.assertEqual(self.service.state.qr_text, "LIVE")
        self.assertEqual(self.service.state.qr_conflicts, ("RESULT",))
        self.assertEqual(self.service.state.qr_status, "conflict")

    def test_recovered_mission_event_restores_missed_qr(self) -> None:
        self.service._apply_recovered_events(
            42,
            [
                {
                    "sequence": 151,
                    "type": "log",
                    "fields": {"event": "QR_DECODED", "qr_text": "RECOVERED"},
                }
            ],
        )
        self.assertEqual(self.service.state.qr_text, "RECOVERED")
        self.assertEqual(self.service.state.timeline[-1].source, "recovery")

    def test_start_timeout_is_not_retried(self) -> None:
        self.orin.responses.append(TimeoutError("lost ack"))
        with self.assertRaises(TimeoutError):
            self.service.start_task("KAMIKAZE")
        starts = [request for request in self.orin.requests if request[0] == "task.start"]
        self.assertEqual(len(starts), 1)
        self.assertIn("otomatik", self.service.state.last_error)

    def test_safety_floor_is_critical_and_cancels_waiting_state(self) -> None:
        self.orin.emit(
            "kamikaze.event",
            {
                "event": "SAFETY_FLOOR_BREACH",
                "altitude_m": 20.0,
                "minimum_safe_alt_m": 25.0,
                "qr_cancelled": True,
            },
        )
        self.assertEqual(self.service.state.critical_event, "SAFETY_FLOOR_BREACH")
        self.assertEqual(self.service.state.qr_status, "cancelled")
        self.assertTrue(self.service.state.timeline[-1].critical)


if __name__ == "__main__":
    unittest.main()
