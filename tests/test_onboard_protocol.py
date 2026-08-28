from __future__ import annotations

import unittest
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from src.core.events import EventBus, Subscription
from src.models.competition import HssZone, KamikazeEvent, LockEvent, QrCoordinate
from src.models.onboard import ACTUATOR_TEST_TASK
from src.services.kamikaze_approach import KamikazeApproachService
from src.models.orin import (
    OrinCommandReceipt,
    OrinConnectionState,
    OrinHandlerResponse,
    OrinMessage,
)
from src.services.logging_service import LoggingService
from src.services.onboard import OnboardMissionService
from src.services.vehicle.telemetry import MutableTelemetryService


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
        if packet_type == "competition.clock.update":
            clock_payload = dict(payload or {})
            return OrinCommandReceipt(
                len(self.requests),
                True,
                "Clock updated",
                {
                    "execution": "updated",
                    "clock_revision": clock_payload["source_revision"],
                },
            )
        if packet_type == "kamikaze.target.update":
            target_payload = dict(payload or {})
            return OrinCommandReceipt(
                len(self.requests),
                True,
                "Target updated",
                {
                    "execution": "updated",
                    "target_revision": target_payload["source_revision"],
                    "target": {
                        "latitude_deg": target_payload["latitude_deg"],
                        "longitude_deg": target_payload["longitude_deg"],
                        "plate_height_m": 3.0,
                    },
                },
            )
        if packet_type == "competition.hss.update":
            hss_payload = dict(payload or {})
            return OrinCommandReceipt(
                len(self.requests),
                True,
                "HSS synchronized",
                {
                    "execution": (
                        "updated" if hss_payload.get("active") else "cleared"
                    ),
                    "revision": hss_payload.get("revision"),
                    "content_hash": hss_payload.get("content_hash"),
                },
            )
        if packet_type == "kamikaze.approach.update":
            return OrinCommandReceipt(
                len(self.requests),
                True,
                "Approach updated",
                {"execution": "updated"},
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
        self.state = SimpleNamespace(
            connected=True,
            authenticated=True,
            endpoint="http://10.0.0.10:10001",
        )
        self.qr_coordinate: QrCoordinate | None = QrCoordinate(39.925533, 32.866287)
        self.hss_zones: tuple[HssZone, ...] = ()
        self.hss_updated = EventBus[tuple[HssZone, ...]]()
        self.qr_updated = EventBus[QrCoordinate | None]()
        self.submitted_kamikaze: list[KamikazeEvent] = []
        self.submitted_locks: list[LockEvent] = []
        self.refresh_count = 0

    def refresh_qr_coordinate(self) -> None:
        self.refresh_count += 1

    def submit_kamikaze_event(self, event: KamikazeEvent) -> None:
        self.submitted_kamikaze.append(event)

    def submit_lock_event(self, event: LockEvent) -> None:
        self.submitted_locks.append(event)

    @staticmethod
    def estimated_server_time() -> datetime:
        return datetime.now(UTC)


class OnboardMissionProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orin = FakeOrinService()
        self.competition = FakeCompetitionService()
        self.telemetry = MutableTelemetryService()
        self.telemetry.start()
        self.logs = LoggingService()
        self.service = OnboardMissionService(
            self.orin,  # type: ignore[arg-type]
            self.competition,  # type: ignore[arg-type]
            self.logs.child("ONBOARD"),
            telemetry=self.telemetry,
        )
        self.service.start()

    def tearDown(self) -> None:
        self.service.stop()
        self.telemetry.stop()

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
        self.assertEqual(self.competition.refresh_count, 0)
        self.assertFalse(
            any(
                packet_type == "competition.hss.update"
                for packet_type, _payload in self.orin.requests
            )
        )
        self.assertEqual(self.orin.requests[-2][0], "kamikaze.target.update")
        target_payload = self.orin.requests[-2][1]
        self.assertEqual(target_payload["latitude_deg"], 39.925533)
        self.assertEqual(target_payload["longitude_deg"], 32.866287)
        self.assertEqual(target_payload["source"], "teknofest")
        self.assertEqual(target_payload["plate_height_m"], 3.0)
        self.assertEqual(target_payload["target_id"], "teknofest-qr-target")
        self.assertTrue(target_payload["fetched_at_utc"].endswith("Z"))
        self.assertEqual(self.orin.requests[-3][0], "competition.clock.update")
        self.assertGreater(self.orin.requests[-3][1]["source_revision"], 0)
        self.assertEqual(
            self.orin.requests[-1],
            ("task.start", {"task": "KAMIKAZE", "source": "ui"}),
        )
        self.assertTrue(self.service.state.waiting_for_guided)
        self.assertEqual(self.service.state.phase, "starting")
        self.assertEqual(self.service.state.mission_id, 42)
        self.assertTrue(self.service.state.target_ready)
        self.assertGreater(self.service.state.target_revision, 0)

    def test_video_recording_control_uses_shared_contract_without_touching_telemetry(self) -> None:
        self.orin.responses.append(
            OrinCommandReceipt(
                17,
                True,
                "Recording stopped",
                {"stream": "video", "enabled": False, "video": False, "telemetry": True},
            )
        )

        receipt = self.service.set_video_recording(False)

        self.assertTrue(receipt.accepted)
        self.assertEqual(
            self.orin.requests[-1],
            ("recording.control", {"stream": "video", "enabled": False}),
        )
        self.assertTrue(
            any(
                entry.action == "onboard.recording_control_acknowledged"
                and entry.details["video"] is False
                and entry.details["telemetry"] is True
                for entry in self.logs.history
            )
        )

    def test_target_lock_alias_starts_the_canonical_onboard_mission(self) -> None:
        self.orin.responses.append(
            OrinCommandReceipt(
                102,
                True,
                "Command accepted",
                {
                    "request_id": 102,
                    "task": "target.lock",
                    "mission_id": 52,
                    "execution": "starting",
                },
            )
        )

        receipt = self.service.start_task("TARGET_LOCK", source="ui")

        self.assertTrue(receipt.accepted)
        self.assertEqual(self.orin.requests[-2][0], "competition.clock.update")
        clock_payload = self.orin.requests[-2][1]
        self.assertGreater(clock_payload["source_revision"], 0)
        self.assertEqual(
            set(clock_payload["sunucusaati"]),
            {"gun", "saat", "dakika", "saniye", "milisaniye"},
        )
        self.assertEqual(
            self.orin.requests[-1],
            ("task.start", {"task": "TARGET_LOCK", "source": "ui"}),
        )
        self.assertEqual(self.service.state.active_task, "target.lock")
        self.assertEqual(self.service.state.mission_id, 52)

    def test_kamikaze_check_syncs_clock_and_uses_canonical_wire_task(self) -> None:
        receipt = self.service.start_task("KAMIKAZE_CHECK", source="ui")

        self.assertTrue(receipt.accepted)
        self.assertEqual(self.orin.requests[-2][0], "competition.clock.update")
        packet_type, payload = self.orin.requests[-1]
        self.assertEqual(packet_type, "task.start")
        self.assertEqual(payload["task"], "kamikaze.check")
        self.assertEqual(payload["source"], "ui")
        self.assertTrue(str(payload["requested_at"]).endswith("Z"))
        self.assertEqual(self.service.state.phase, "acknowledged")
        self.assertNotEqual(self.service.state.phase, "running")
        self.assertEqual(self.service.state.active_task, "kamikaze.check")

    def test_kamikaze_clock_is_acked_before_start_and_refreshed_only_while_active(self) -> None:
        self.service._CLOCK_REFRESH_INTERVAL_S = 0.02

        self.service.start_task("KAMIKAZE", source="ui")
        initial_clock_count = sum(
            packet_type == "competition.clock.update"
            for packet_type, _payload in self.orin.requests
        )
        self.assertEqual(initial_clock_count, 1)

        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            clock_count = sum(
                packet_type == "competition.clock.update"
                for packet_type, _payload in self.orin.requests
            )
            if clock_count > initial_clock_count:
                break
            time.sleep(0.005)
        self.assertGreater(clock_count, initial_clock_count)

        self.orin.emit(
            "mission.result",
            {
                "task": "kamikaze.execute",
                "mission_id": self.service.state.mission_id or 1,
                "execution": "failed",
                "reason_code": "input.rejected",
                "snapshot": {},
            },
        )
        stopped_count = sum(
            packet_type == "competition.clock.update"
            for packet_type, _payload in self.orin.requests
        )
        time.sleep(0.06)
        self.assertEqual(
            sum(
                packet_type == "competition.clock.update"
                for packet_type, _payload in self.orin.requests
            ),
            stopped_count,
        )

    def test_observed_task_start_is_not_reported_as_a_failed_command_when_ack_times_out(self) -> None:
        original_issue = self.orin.issue

        def delayed_start(packet_type, payload=None, *, timeout=None):
            if packet_type == "task.start":
                self.orin.emit(
                    "mission.transition",
                    {
                        "task": "kamikaze.execute",
                        "mission_id": 42,
                        "previous": "registered",
                        "current": "running",
                    },
                )
                raise TimeoutError("late task.start ACK")
            return original_issue(packet_type, payload, timeout=timeout)

        self.orin.issue = delayed_start
        receipt = self.service.start_task("KAMIKAZE", source="ui")

        self.assertTrue(receipt.accepted)
        self.assertTrue(receipt.response["observed_lifecycle"])
        self.assertEqual(self.service.state.phase, "running")
        self.assertFalse(
            any(entry.action == "onboard.command_failed" for entry in self.logs.history)
        )
        self.assertTrue(
            any(
                entry.action == "onboard.command_ack_missing_mission_observed"
                for entry in self.logs.history
            )
        )

    def test_kamikaze_transit_does_not_send_hss_then_starts_its_own_task(self) -> None:
        selector = KamikazeApproachService(
            self.competition,  # type: ignore[arg-type]
            SimpleNamespace(current=None, updated=EventBus()),  # type: ignore[arg-type]
        )
        plan = selector.build_plan_from_values(
            qr=self.competition.qr_coordinate,
            zones=(),
            wind_from_deg=0.0,
            wind_speed_mps=5.0,
        )
        locked = selector.select(plan.corridors[0].angle_deg, plan_revision=plan.revision)
        self.orin.responses.append(
            OrinCommandReceipt(
                404,
                True,
                "Transit accepted",
                {
                    "task": "kamikaze.transit",
                    "mission_id": 91,
                    "execution": "starting",
                    "waiting_for_mode": False,
                    "vehicle_mode": "GUIDED",
                },
            )
        )

        receipt = self.service.start_kamikaze_transit(locked)

        self.assertEqual(receipt.request_id, 404)
        packet_types = [packet_type for packet_type, _payload in self.orin.requests]
        self.assertEqual(
            packet_types[-4:],
            [
                "kamikaze.target.update",
                "competition.clock.update",
                "kamikaze.approach.update",
                "task.start",
            ],
        )
        self.assertNotIn("competition.hss.update", packet_types)
        approach_payload = self.orin.requests[-2][1]
        self.assertEqual(approach_payload["plan_revision"], locked.revision)
        self.assertEqual(approach_payload["p2"]["altitude_m"], 130.0)
        self.assertEqual(approach_payload["p1"]["altitude_m"], 130.0)
        self.assertEqual(approach_payload["dive_start_altitude_m"], 130.0)
        self.assertEqual(approach_payload["pullup_altitude_m"], 90.0)
        self.assertEqual(approach_payload["dive_pitch_deg"], -45.0)
        self.assertEqual(approach_payload["corridor_width_m"], 1.0)
        self.assertEqual(approach_payload["safety_margin_m"], 5.0)
        self.assertEqual(approach_payload["hss_revision"], 0)
        self.assertEqual(len(approach_payload["content_hash"]), 64)
        task_payload = self.orin.requests[-1][1]
        self.assertEqual(task_payload["task"], "kamikaze.transit")
        self.assertEqual(task_payload["parameters"]["plan_revision"], locked.revision)
        self.assertEqual(self.service.state.active_task, "kamikaze.transit")

    def test_only_hss_task_sends_hss_snapshot_before_its_task_start(self) -> None:
        receipt = self.service.start_task("HSS_ESCAPE", source="ui")

        self.assertTrue(receipt.accepted)
        packet_types = [packet_type for packet_type, _payload in self.orin.requests]
        self.assertEqual(
            packet_types[-2:],
            ["competition.hss.update", "task.start"],
        )
        self.assertEqual(self.orin.requests[-1][1]["task"], "HSS_ESCAPE")

    def test_generic_kamikaze_transit_start_is_blocked_without_locked_plan(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "P1/P2 planı"):
            self.service.start_task("kamikaze.transit")
        self.assertFalse(any(kind == "task.start" for kind, _ in self.orin.requests))

    def test_foreground_target_lock_can_start_while_hss_task_is_active(self) -> None:
        self.service._state = replace(
            self.service.state,
            active_task="hss.predict",
            phase="running",
            mission_id=88,
            mission_contexts={
                "hss.predict:88": {
                    "task": "hss.predict",
                    "mission_id": 88,
                    "phase": "running",
                }
            },
        )
        self.orin.responses.append(
            OrinCommandReceipt(
                99,
                True,
                "Command accepted",
                {
                    "task": "target.lock",
                    "mission_id": 99,
                    "execution": "starting",
                    "vehicle_mode": "GUIDED",
                },
            )
        )

        receipt = self.service.start_task("TARGET_LOCK")

        self.assertTrue(receipt.accepted)
        state = self.service.state
        self.assertEqual(state.active_task, "target.lock")
        self.assertEqual(state.target_lock_mission_id, 99)
        self.assertEqual(state.mission_contexts["hss.predict:88"]["phase"], "running")
        self.assertEqual(state.mission_contexts["target.lock:99"]["phase"], "starting")

    def test_target_lock_transition_is_tracked_separately_from_background_hss(self) -> None:
        self.service._state = replace(
            self.service.state,
            active_task="hss.predict",
            mission_id=88,
            phase="running",
            mission_contexts={
                "hss.predict:88": {
                    "task": "hss.predict",
                    "mission_id": 88,
                    "phase": "running",
                }
            },
        )

        self.orin.emit(
            "mission.transition",
            {
                "task": "target.lock",
                "mission_id": 99,
                "previous": "starting",
                "current": "running",
            },
        )

        state = self.service.state
        self.assertEqual(state.active_task, "hss.predict")
        self.assertEqual(state.mission_id, 88)
        self.assertEqual(state.phase, "running")
        self.assertEqual(state.target_lock_mission_id, 99)
        self.assertEqual(state.target_lock_phase, "running")
        self.assertEqual(state.mission_contexts["target.lock:99"]["phase"], "running")

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
            ["competition.clock.update", "kamikaze.target.update"],
        )
        self.assertFalse(self.service.state.target_ready)
        self.assertTrue(
            any(entry.action == "kamikaze.target_failed" for entry in self.logs.history)
        )

    def test_compact_target_ack_allows_kamikaze_start(self) -> None:
        original_issue = self.orin.issue

        def compact_target_ack(packet_type, payload=None, *, timeout=None):
            if packet_type == "kamikaze.target.update":
                self.orin.requests.append((packet_type, dict(payload or {})))
                return OrinCommandReceipt(
                    77,
                    True,
                    "Command accepted",
                    {"execution": "updated"},
                )
            return original_issue(packet_type, payload, timeout=timeout)

        self.orin.issue = compact_target_ack
        receipt = self.service.start_task("KAMIKAZE")

        self.assertTrue(receipt.accepted)
        self.assertEqual(self.orin.requests[-1][0], "task.start")
        self.assertTrue(self.service.state.target_ready)
        self.assertEqual(
            self.service.state.target["latitude_deg"],
            self.competition.qr_coordinate.latitude,
        )
        target_logs = [
            entry for entry in self.logs.history
            if entry.action == "kamikaze.target_ack"
        ]
        self.assertTrue(target_logs[-1].details["compact_ack"])

    def test_mismatched_target_ack_blocks_kamikaze_start(self) -> None:
        original_issue = self.orin.issue

        def wrong_target_ack(packet_type, payload=None, *, timeout=None):
            if packet_type == "kamikaze.target.update":
                request = dict(payload or {})
                self.orin.requests.append((packet_type, request))
                return OrinCommandReceipt(
                    78,
                    True,
                    "Command accepted",
                    {
                        "execution": "updated",
                        "target_revision": request["source_revision"],
                        "target": {
                            "latitude_deg": 41.0,
                            "longitude_deg": 29.0,
                        },
                    },
                )
            return original_issue(packet_type, payload, timeout=timeout)

        self.orin.issue = wrong_target_ack

        with self.assertRaisesRegex(RuntimeError, "farklı bir kamikaze hedefini"):
            self.service.start_task("KAMIKAZE")

        self.assertFalse(
            any(packet_type == "task.start" for packet_type, _ in self.orin.requests)
        )

    def test_missing_target_blocks_kamikaze_start_before_task_request(self) -> None:
        self.competition.qr_coordinate = None

        with self.assertRaisesRegex(RuntimeError, "hedefi Onboard"):
            self.service.start_task("KAMIKAZE")

        self.assertEqual(
            [packet_type for packet_type, _payload in self.orin.requests],
            ["competition.clock.update"],
        )
        self.assertTrue(
            any(
                entry.action == "kamikaze.target_sync_skipped"
                for entry in self.logs.history
            )
        )

    def test_hss_sync_only_sends_changes_and_clears_expired_zones(self) -> None:
        zones = (
            HssZone(2, 39.9256, 32.8664, 80.0),
            HssZone(1, 39.9255, 32.8663, 75.0),
        )
        self.service._on_hss_updated(zones)
        first = self.service._send_hss_update()
        duplicate = self.service._send_hss_update()

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        packet_type, payload = self.orin.requests[-1]
        self.assertEqual(packet_type, "competition.hss.update")
        self.assertTrue(payload["active"])
        self.assertEqual([zone["id"] for zone in payload["zones"]], [1, 2])
        first_revision = payload["revision"]
        self.assertGreater(first_revision, 0)

        self.service._on_hss_updated(())
        cleared = self.service._send_hss_update()

        self.assertIsNotNone(cleared)
        _packet_type, clear_payload = self.orin.requests[-1]
        self.assertFalse(clear_payload["active"])
        self.assertEqual(clear_payload["zones"], [])
        self.assertEqual(clear_payload["reason"], "source-ended")
        self.assertGreater(clear_payload["revision"], first_revision)
        self.assertFalse(self.service.state.hss_active)
        self.assertEqual(self.service.state.hss_count, 0)

    def test_new_orin_session_does_not_push_kamikaze_target_without_launch(self) -> None:
        target = self.competition.qr_coordinate
        assert target is not None
        self.service._send_target_update(target)
        self.assertIsNone(self.service._send_target_update(target))

        self.orin.state = replace(
            self.orin.state,
            alive=True,
            session_id="orin-session-2",
            kamikaze_target_ready=False,
        )
        self.service._on_orin_changed(self.orin.state)
        time.sleep(0.05)
        target_requests = [
            request for request in self.orin.requests
            if request[0] == "kamikaze.target.update"
        ]
        self.assertEqual(len(target_requests), 1)

    def test_qr_update_does_not_trigger_hss_or_target_sync(self) -> None:
        before = len(self.orin.requests)
        self.competition.qr_updated.publish(QrCoordinate(40.0, 29.0))
        time.sleep(0.05)
        self.assertEqual(len(self.orin.requests), before)

    def test_hss_update_does_not_trigger_automatic_onboard_sync(self) -> None:
        before = len(self.orin.requests)
        self.competition.hss_updated.publish(
            (HssZone(1, 39.0, 32.0, 100.0),)
        )
        time.sleep(0.05)
        self.assertEqual(len(self.orin.requests), before)

    def test_new_orin_session_clears_a_stale_requesting_task(self) -> None:
        self.service._state = replace(
            self.service.state,
            connected=True,
            active_task=ACTUATOR_TEST_TASK,
            mission_id=84,
            phase="requesting",
            pending_command="start",
        )
        self.service._last_orin_session = "old-session"
        state = replace(
            self.orin.state,
            alive=True,
            session_id="new-session",
            active_task="",
            active_missions=(),
        )

        self.service._on_orin_changed(state)

        self.assertEqual(self.service.state.active_task, "")
        self.assertIsNone(self.service.state.mission_id)
        self.assertEqual(self.service.state.phase, "idle")
        self.assertEqual(self.service.state.pending_command, "")

    def test_mission_list_reconciles_a_lost_start_ack_to_terminal_state(self) -> None:
        self.service._state = replace(
            self.service.state,
            active_task=ACTUATOR_TEST_TASK,
            phase="requesting",
        )

        self.service._apply_mission_list(
            {
                "available_tasks": [ACTUATOR_TEST_TASK],
                "missions": [
                    {
                        "mission_id": 84,
                        "task": ACTUATOR_TEST_TASK,
                        "phase": "succeeded",
                        "updated_at": 10.0,
                        "result": {"execution": "notification_only"},
                    }
                ],
            }
        )

        state = self.service.state
        self.assertEqual(state.active_task, ACTUATOR_TEST_TASK)
        self.assertEqual(state.mission_id, 84)
        self.assertEqual(state.phase, "succeeded")
        self.assertTrue(state.result_received)

    def test_reconciled_failure_exposes_the_real_onboard_reason(self) -> None:
        self.service._state = replace(
            self.service.state,
            active_task=ACTUATOR_TEST_TASK,
            mission_id=84,
            phase="reconciling",
        )

        self.service._apply_mission_list(
            {
                "missions": [
                    {
                        "mission_id": 84,
                        "task": ACTUATOR_TEST_TASK,
                        "phase": "failed",
                        "reason": "Actuator test is blocked while the vehicle is armed",
                        "updated_at": 10.0,
                    }
                ]
            }
        )

        state = self.service.state
        self.assertEqual(state.phase, "failed")
        self.assertIn("vehicle is armed", state.last_error)

    def test_kamikaze_terminal_state_and_qr_are_recovered_after_lost_live_packets(self) -> None:
        self.service._state = replace(
            self.service.state,
            active_task="kamikaze.execute",
            mission_id=42,
            phase="reconciling",
            qr_status="pending",
        )

        self.service._apply_mission_list(
            {
                "missions": [
                    {
                        "mission_id": 42,
                        "task": "kamikaze.execute",
                        "phase": "succeeded",
                        "updated_at": 10.0,
                        "result": {"reason": "QR_DECODED"},
                    }
                ]
            }
        )
        self.service._apply_recovered_events(
            42,
            [
                {
                    "sequence": 9,
                    "type": "log",
                    "fields": {
                        "event": "QR_DECODED_DURING_PULL_UP",
                        "qr_text": "RECOVERED-KAMIKAZE-QR",
                    },
                }
            ],
        )

        state = self.service.state
        self.assertEqual(state.phase, "succeeded")
        self.assertTrue(state.result_received)
        self.assertEqual(state.qr_status, "decoded")
        self.assertEqual(state.qr_text, "RECOVERED-KAMIKAZE-QR")
        self.assertEqual(state.timeline[-1].source, "recovery")

    def test_compact_terminal_result_is_applied_without_snapshot(self) -> None:
        self.orin.emit(
            "mission.result",
            {
                "task": ACTUATOR_TEST_TASK,
                "mission_id": 84,
                "execution": "failed",
                "reason": "Actuator test is blocked while the vehicle is armed",
                "result": {},
            },
        )

        state = self.service.state
        self.assertEqual(state.phase, "failed")
        self.assertTrue(state.result_received)
        self.assertIn("vehicle is armed", state.last_error)

    def test_target_lock_status_updates_official_telemetry_and_expires(self) -> None:
        self.service._TARGET_LOCK_STATUS_TIMEOUT_S = 0.05
        handler = self.orin.handlers["target.lock.status"]
        handler(
            OrinMessage(
                "target.lock.status",
                {
                    "state": "track",
                    "frame_sequence": 120,
                    "target_id": 7,
                    "confidence": 0.91,
                    "hedef_merkez_X": 480,
                    "hedef_merkez_Y": 270,
                    "hedef_genislik": 192,
                    "hedef_yukseklik": 100,
                    "error_x": 0.0,
                    "error_y": -0.01,
                    "lock_progress": 0.75,
                    "in_zone": True,
                    "locked": False,
                    "autonomous": True,
                    "time_utc": datetime.now(UTC).isoformat(),
                },
                800,
                1,
                191,
            )
        )

        track = self.telemetry.current.target_track
        self.assertTrue(track.locked)
        self.assertEqual(
            (track.center_x, track.center_y, track.width, track.height),
            (480, 270, 192, 100),
        )
        self.assertEqual(track.vehicle_source, "orin-target-lock")

        deadline = time.monotonic() + 0.5
        while self.telemetry.current.target_track.locked and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.telemetry.current.target_track.locked)
        self.assertEqual(self.telemetry.current.target_track.width, 0)

    def test_target_lock_report_is_submitted_once_and_duplicate_is_acknowledged(
        self,
    ) -> None:
        self.service._state = replace(
            self.service.state,
            active_task="hss.predict",
            mission_id=88,
            phase="running",
        )
        now = self.competition.estimated_server_time()
        clock = {
            "saat": now.hour,
            "dakika": now.minute,
            "saniye": now.second,
            "milisaniye": now.microsecond // 1000,
        }
        handler = self.orin.handlers["target.lock.report"]
        message = OrinMessage(
            "target.lock.report",
            {
                "report_id": "session:42:120:lock",
                "mission_id": 42,
                "target_id": 7,
                "kilitlenmeBitisZamani": clock,
                "otonom_kilitlenme": 1,
            },
            801,
            1,
            191,
        )

        first = handler(message)
        duplicate = handler(message)

        self.assertIsInstance(first, OrinHandlerResponse)
        self.assertEqual(dict(first.payload), dict(duplicate.payload))
        self.assertEqual(first.payload["execution"], "queued")
        deadline = time.monotonic() + 1.0
        while not self.competition.submitted_locks and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.competition.submitted_locks), 1)
        self.assertEqual(
            self.competition.submitted_locks[0].to_payload(),
            {
                "kilitlenmeBitisZamani": clock,
                "otonom_kilitlenme": 1,
            },
        )
        self.assertTrue(
            any(
                entry.action == "target_lock.report_submitted"
                for entry in self.logs.history
            )
        )

    def test_failed_target_lock_submission_is_acked_and_not_duplicated(self) -> None:
        attempts = 0

        def submit(event: LockEvent) -> None:
            nonlocal attempts
            attempts += 1
            raise ConnectionError("competition server unavailable")

        self.competition.submit_lock_event = submit
        now = self.competition.estimated_server_time()
        payload = {
            "report_id": "retryable-lock-report",
            "mission_id": 43,
            "target_id": 8,
            "kilitlenmeBitisZamani": {
                "saat": now.hour,
                "dakika": now.minute,
                "saniye": now.second,
                "milisaniye": now.microsecond // 1000,
            },
            "otonom_kilitlenme": 0,
        }
        handler = self.orin.handlers["target.lock.report"]

        first = handler(OrinMessage("target.lock.report", payload, 802, 1, 191))
        duplicate = handler(
            OrinMessage("target.lock.report", payload, 803, 1, 191)
        )

        self.assertEqual(first.payload["execution"], "queued")
        self.assertEqual(duplicate.payload["execution"], "queued")
        deadline = time.monotonic() + 1.0
        while attempts == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(attempts, 1)
        self.assertEqual(len(self.competition.submitted_locks), 0)
        self.assertTrue(
            any(
                entry.action == "target_lock.report_delivery_failed"
                for entry in self.logs.history
            )
        )

    def test_target_lock_report_ack_does_not_wait_for_http_post(self) -> None:
        release = threading.Event()

        def submit(_event: LockEvent) -> None:
            release.wait(1.0)

        self.competition.submit_lock_event = submit
        now = self.competition.estimated_server_time()
        started = time.monotonic()
        response = self.orin.handlers["target.lock.report"](
            OrinMessage(
                "target.lock.report",
                {
                    "report_id": "fast-ack-lock-report",
                    "mission_id": 44,
                    "target_id": 9,
                    "kilitlenmeBitisZamani": {
                        "saat": now.hour,
                        "dakika": now.minute,
                        "saniye": now.second,
                        "milisaniye": now.microsecond // 1000,
                    },
                    "otonom_kilitlenme": 1,
                },
                804,
                1,
                191,
            )
        )
        elapsed = time.monotonic() - started
        release.set()

        self.assertEqual(response.payload["execution"], "queued")
        self.assertLess(elapsed, 0.25)

    def test_qr_report_is_submitted_once_and_duplicate_is_acknowledged(self) -> None:
        handler = self.orin.handlers["kamikaze.qr.report"]
        message = OrinMessage(
            "kamikaze.qr.report",
            {
                "report_id": "mission-42:qr-1",
                "mission_id": 42,
                "event": "QR_DECODED_DURING_PULL_UP",
                "qr_text": "KIRLANGIC-QR",
                "started_at_server": {"saat": 12, "dakika": 0, "saniye": 0, "milisaniye": 0},
                "ended_at_server": {"saat": 12, "dakika": 0, "saniye": 4, "milisaniye": 0},
                "altitude_m": 48.2,
            },
            701,
            1,
            191,
        )

        first = handler(message)
        duplicate = handler(message)

        self.assertIsInstance(first, OrinHandlerResponse)
        self.assertIsInstance(duplicate, OrinHandlerResponse)
        self.assertEqual(first.payload["execution"], "submitted")
        self.assertEqual(duplicate.payload["execution"], "duplicate")
        self.assertEqual(first.message, "QR report accepted")
        self.assertEqual(len(self.competition.submitted_kamikaze), 1)
        self.assertEqual(
            self.competition.submitted_kamikaze[0].qr_text,
            "KIRLANGIC-QR",
        )
        self.assertEqual(self.service.state.qr_text, "KIRLANGIC-QR")
        self.assertTrue(
            any(entry.action == "kamikaze.qr_submitted" for entry in self.logs.history)
        )

    def test_failed_qr_submission_is_not_cached_and_can_be_retried(self) -> None:
        attempts = 0

        def submit(event: KamikazeEvent) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("competition server unavailable")
            self.competition.submitted_kamikaze.append(event)

        self.competition.submit_kamikaze_event = submit
        handler = self.orin.handlers["kamikaze.qr.report"]
        message = OrinMessage(
            "kamikaze.qr.report",
            {
                "report_id": "retryable-report",
                "mission_id": 43,
                "event": "QR_DECODED",
                "qr_text": "RETRY-QR",
                "started_at_server": {"saat": 12, "dakika": 0, "saniye": 0, "milisaniye": 0},
                "ended_at_server": {"saat": 12, "dakika": 0, "saniye": 4, "milisaniye": 0},
                "altitude_m": 0.0,
            },
            702,
            1,
            191,
        )

        with self.assertRaisesRegex(ConnectionError, "unavailable"):
            handler(message)
        response = handler(message)

        self.assertIsInstance(response, OrinHandlerResponse)
        self.assertEqual(response.payload["execution"], "submitted")
        self.assertEqual(attempts, 2)
        self.assertEqual(len(self.competition.submitted_kamikaze), 1)

    def test_qr_report_accepts_the_exact_shared_wire_timestamp_shape(self) -> None:
        now = datetime.now(UTC)
        clock = {
            "saat": now.hour,
            "dakika": now.minute,
            "saniye": now.second,
            "milisaniye": now.microsecond // 1000,
        }
        handler = self.orin.handlers["kamikaze.qr.report"]

        response = handler(
            OrinMessage(
                "kamikaze.qr.report",
                {
                    "report_id": "wire-shape-report",
                    "mission_id": 44,
                    "event": "QR_DECODED",
                    "qr_text": "WIRE-QR",
                    "kamikazeBaslangicZamani": clock,
                    "kamikazeBitisZamani": clock,
                    "altitude_m": 90.0,
                },
                705,
                1,
                191,
            )
        )

        self.assertEqual(response.payload["execution"], "submitted")
        self.assertEqual(self.competition.submitted_kamikaze[-1].qr_text, "WIRE-QR")

    def test_qr_report_id_uses_the_same_192_character_contract(self) -> None:
        handler = self.orin.handlers["kamikaze.qr.report"]
        report_id = "r" * 192
        payload = {
            "report_id": report_id,
            "mission_id": 45,
            "event": "QR_DECODED",
            "qr_text": "BOUNDARY-QR",
            "started_at_server": {"saat": 12, "dakika": 0, "saniye": 0, "milisaniye": 0},
            "ended_at_server": {"saat": 12, "dakika": 0, "saniye": 4, "milisaniye": 0},
            "altitude_m": 0.0,
        }

        response = handler(
            OrinMessage("kamikaze.qr.report", payload, 703, 1, 191)
        )

        self.assertIsInstance(response, OrinHandlerResponse)
        self.assertEqual(response.payload["report_id"], report_id)
        with self.assertRaisesRegex(ValueError, "192"):
            handler(
                OrinMessage(
                    "kamikaze.qr.report",
                    {**payload, "report_id": "r" * 193},
                    704,
                    1,
                    191,
                )
            )

    def test_ground_check_report_is_accepted_without_competition_post(self) -> None:
        handler = self.orin.handlers["kamikaze.qr.report"]
        payload = {
            "report_id": "ground-check:46",
            "mission_id": 46,
            "event": "QR_DECODED",
            "qr_text": "GROUND-CHECK-QR",
            "started_at_server": {"saat": 13, "dakika": 1, "saniye": 2, "milisaniye": 3},
            "ended_at_server": {"saat": 13, "dakika": 1, "saniye": 5, "milisaniye": 6},
            "altitude_m": 0.0,
            "test_only": True,
        }

        first = handler(OrinMessage("kamikaze.qr.report", payload, 706, 1, 191))
        duplicate = handler(OrinMessage("kamikaze.qr.report", payload, 707, 1, 191))

        self.assertEqual(first.payload["execution"], "accepted")
        self.assertTrue(first.payload["test_only"])
        self.assertEqual(duplicate.payload["execution"], "duplicate")
        self.assertEqual(self.competition.submitted_kamikaze, [])
        self.assertEqual(self.service.state.qr_text, "GROUND-CHECK-QR")

    def test_source_revisions_remain_monotonic_above_wall_clock_value(self) -> None:
        high_revision = 9_000_000_000_000_000

        next_revision = self.service._next_revision(high_revision)

        self.assertEqual(next_revision, high_revision + 1)

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

    def test_terminal_reason_code_is_kept_and_late_transition_is_ignored(self) -> None:
        self.orin.emit(
            "mission.result",
            {
                "task": "kamikaze.execute",
                "mission_id": 42,
                "execution": "failed",
                "reason": "Camera stream ended",
                "reason_code": "vision.camera_unavailable",
                "snapshot": {},
            },
        )
        self.orin.emit(
            "mission.transition",
            {
                "task": "kamikaze.execute",
                "mission_id": 42,
                "previous": "running",
                "current": "running",
                "reason_code": "execution.updated",
            },
        )

        state = self.service.state
        self.assertEqual(state.phase, "failed")
        self.assertEqual(state.reason_code, "vision.camera_unavailable")
        self.assertEqual(
            state.mission_contexts["kamikaze.execute:42"]["reason_code"],
            "vision.camera_unavailable",
        )
        self.assertIn("kamera", state.last_error.lower())
        diagnosis = next(
            entry for entry in self.logs.history
            if entry.action == "mission.terminal_diagnosis"
        )
        self.assertEqual(diagnosis.details["reason_code"], "vision.camera_unavailable")
        self.assertEqual(diagnosis.details["task"], "kamikaze.execute")
        self.assertTrue(
            any(entry.action == "mission.packet_ignored" for entry in self.logs.history)
        )

    def test_unknown_terminal_reason_code_is_forward_compatible(self) -> None:
        self.orin.emit(
            "mission.result",
            {
                "task": "kamikaze.execute",
                "mission_id": 42,
                "execution": "cancelled",
                "reason": "Future onboard implementation detail",
                "reason_code": "future.detail",
                "snapshot": {},
            },
        )

        self.assertEqual(self.service.state.reason_code, "future.detail")
        self.assertEqual(self.service.state.phase, "cancelled")

    def test_late_packets_from_another_mission_do_not_replace_active_state(self) -> None:
        self.service._state = replace(
            self.service.state,
            active_task="kamikaze.execute",
            mission_id=42,
            phase="running",
        )

        self.orin.emit(
            "mission.transition",
            {
                "task": ACTUATOR_TEST_TASK,
                "mission_id": 84,
                "previous": "running",
                "current": "succeeded",
            },
        )
        self.orin.emit(
            "mission.result",
            {
                "task": ACTUATOR_TEST_TASK,
                "mission_id": 84,
                "execution": "succeeded",
            },
        )
        self.orin.emit(
            "mission.event",
            {
                "mission": ACTUATOR_TEST_TASK,
                "mission_id": 84,
                "event": "actuator_test.step",
                "step": 1,
                "servo_output": 9,
                "pwm": 1300,
            },
        )

        state = self.service.state
        self.assertEqual(state.active_task, "kamikaze.execute")
        self.assertEqual(state.mission_id, 42)
        self.assertEqual(state.phase, "running")
        self.assertEqual(state.timeline, ())
        self.assertTrue(
            any(entry.action == "mission.packet_ignored" for entry in self.logs.history)
        )

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

    def test_actuator_test_uses_alias_payload_and_preserves_ack_state(self) -> None:
        snapshot = {
            "mission_id": 84,
            "task": ACTUATOR_TEST_TASK,
            "phase": "starting",
            "resources": ["actuators"],
        }
        self.orin.responses.append(
            OrinCommandReceipt(
                202,
                True,
                "Command accepted",
                {
                    "request_id": 202,
                    "task": ACTUATOR_TEST_TASK,
                    "mission_id": 84,
                    "execution": "starting",
                    "vehicle_mode": "MANUAL",
                    "snapshot": snapshot,
                },
            )
        )

        receipt = self.service.start_task("ACTUATOR_TEST", source="operator-ui")

        self.assertTrue(receipt.accepted)
        self.assertEqual(
            self.orin.requests[-1],
            (
                "task.start",
                {"task": "ACTUATOR_TEST", "source": "ui"},
            ),
        )
        state = self.service.state
        self.assertEqual(state.active_task, ACTUATOR_TEST_TASK)
        self.assertEqual(state.mission_id, 84)
        self.assertEqual(state.phase, "starting")
        self.assertEqual(state.vehicle_mode, "MANUAL")
        self.assertEqual(dict(state.acknowledgement_snapshot), snapshot)

    def test_actuator_events_update_progress_and_notification_only_state(self) -> None:
        self.orin.emit(
            "mission.event",
            {
                "event": "actuator_test.started",
                "mission_id": 84,
                "step": "initializing",
                "armed": False,
            },
        )
        self.orin.emit(
            "mission.event",
            {
                "event": "actuator_test.step",
                "mission_id": 84,
                "step": "servo-1-high",
                "servo_output": 1,
                "pwm": 1700,
                "armed": False,
            },
        )
        self.orin.emit(
            "mission.event",
            {
                "event": "actuator_test.notification_only",
                "mission_id": 84,
                "step": "notification",
                "servo_output": 0,
                "pwm": 0,
                "armed": False,
            },
        )

        state = self.service.state
        self.assertEqual(state.active_task, ACTUATOR_TEST_TASK)
        self.assertEqual(state.mission_id, 84)
        self.assertEqual(state.actuator_step_count, 1)
        self.assertEqual(state.actuator_step, "notification")
        self.assertEqual(state.actuator_servo_output, 0)
        self.assertEqual(state.actuator_pwm, 0)
        self.assertFalse(state.actuator_armed)
        self.assertTrue(state.actuator_notification_only)
        self.assertEqual(len(state.timeline), 3)
        self.assertTrue(
            any(entry.action == "actuator_test.event" for entry in self.logs.history)
        )

    def test_actuator_start_is_blocked_while_the_same_task_is_active(self) -> None:
        self.orin.emit(
            "mission.transition",
            {
                "task": ACTUATOR_TEST_TASK,
                "mission_id": 84,
                "previous": "starting",
                "current": "running",
            },
        )

        with self.assertRaisesRegex(RuntimeError, "zaten çalışıyor"):
            self.service.start_task("ACTUATOR_TEST", source="operator-ui")

        self.assertFalse(
            any(packet_type == "task.start" for packet_type, _ in self.orin.requests)
        )

    def test_actuator_system_error_message_is_preserved_verbatim(self) -> None:
        self.orin.responses.append(
            OrinCommandReceipt(
                203,
                False,
                "Vehicle must be DISARMED",
                {
                    "request_id": 203,
                    "accepted": False,
                    "message": "Vehicle must be DISARMED",
                    "error_type": "SafetyError",
                },
            )
        )

        with self.assertRaisesRegex(RuntimeError, "^Vehicle must be DISARMED$"):
            self.service.start_task("ACTUATOR_TEST", source="operator-ui")

        self.assertEqual(self.service.state.last_error, "Vehicle must be DISARMED")
        self.assertEqual(self.service.state.phase, "failed")

        self.orin.responses.append(
            OrinCommandReceipt(
                204,
                True,
                "Command accepted",
                {
                    "request_id": 204,
                    "task": ACTUATOR_TEST_TASK,
                    "mission_id": 85,
                    "execution": "starting",
                },
            )
        )
        self.assertTrue(
            self.service.start_task(
                "ACTUATOR_TEST",
                source="operator-ui",
            ).accepted
        )

    def test_actuator_task_is_discovered_from_heartbeat_and_mission_list(self) -> None:
        self.orin.state = replace(
            self.orin.state,
            alive=True,
            available_tasks=(ACTUATOR_TEST_TASK,),
        )
        self.orin.changed.publish(self.orin.state)
        self.assertIn(ACTUATOR_TEST_TASK, self.service.state.available_tasks)

        service = OnboardMissionService(
            self.orin,  # type: ignore[arg-type]
            self.competition,  # type: ignore[arg-type]
        )
        service._apply_mission_list(
            {
                "missions": [
                    {
                        "mission_id": 84,
                        "task": ACTUATOR_TEST_TASK,
                        "phase": "registered",
                    }
                ],
                "available_tasks": ["ACTUATOR_TEST"],
            }
        )
        self.assertIn(ACTUATOR_TEST_TASK, service.state.available_tasks)
        self.assertEqual(service.state.active_task, "")

    def test_empty_mission_list_clears_uncertain_started_task(self) -> None:
        self.service._state = replace(
            self.service.state,
            connected=True,
            active_task=ACTUATOR_TEST_TASK,
            phase="reconciling",
            last_error="Start acknowledgement timed out",
        )

        self.service._apply_mission_list(
            {
                "missions": [],
                "available_tasks": [ACTUATOR_TEST_TASK],
            }
        )

        state = self.service.state
        self.assertEqual(state.active_task, "")
        self.assertIsNone(state.mission_id)
        self.assertEqual(state.phase, "idle")

    def test_cancel_is_idempotent_when_onboard_task_is_already_absent(self) -> None:
        self.service._state = replace(
            self.service.state,
            connected=True,
            active_task=ACTUATOR_TEST_TASK,
            mission_id=84,
            phase="running",
        )
        self.orin.responses.append(
            OrinCommandReceipt(
                205,
                False,
                "Task is not instantiated: system.actuator.test",
                {
                    "request_id": 205,
                    "accepted": False,
                    "message": "Task is not instantiated: system.actuator.test",
                    "error_type": "MissionNotFoundError",
                },
            )
        )

        receipt = self.service.cancel_task(ACTUATOR_TEST_TASK)

        self.assertTrue(receipt.accepted)
        self.assertEqual(receipt.response["execution"], "cancelled")
        self.assertTrue(receipt.response["already_inactive"])
        self.assertEqual(self.service.state.active_task, ACTUATOR_TEST_TASK)
        self.assertEqual(self.service.state.phase, "cancelled")
        self.assertEqual(
            self.logs.history[-1].action,
            "onboard.cancel_idempotent",
        )

    def test_positive_already_inactive_cancel_ack_is_terminal_and_uses_alias(self) -> None:
        self.service._state = replace(
            self.service.state,
            connected=True,
            active_task="kamikaze.execute",
            mission_id=42,
            phase="reconciling",
        )
        self.orin.responses.append(
            OrinCommandReceipt(
                206,
                True,
                "Already inactive",
                {
                    "request_id": 206,
                    "accepted": True,
                    "execution": "cancelled",
                    "mission_id": None,
                    "already_inactive": True,
                },
            )
        )

        receipt = self.service.cancel_task("kamikaze.execute")

        self.assertTrue(receipt.accepted)
        self.assertEqual(
            self.orin.requests[-1],
            ("task.cancel", {"task": "KAMIKAZE"}),
        )
        self.assertEqual(self.service.state.phase, "cancelled")
        self.assertIsNone(self.service.state.mission_id)
        self.assertEqual(self.service.state.pending_command, "")

    def test_cancel_timeout_reconciles_with_mission_list_without_retry(self) -> None:
        self.service._state = replace(
            self.service.state,
            connected=True,
            active_task="kamikaze.execute",
            mission_id=42,
            phase="running",
        )
        self.orin.responses.extend(
            [
                TimeoutError("lost cancel ack"),
                OrinCommandReceipt(
                    208,
                    True,
                    "listed",
                    {
                        "missions": [
                            {
                                "mission_id": 42,
                                "task": "kamikaze.execute",
                                "phase": "cancelled",
                                "reason": "operator_cancelled",
                                "updated_at": 10.0,
                            }
                        ]
                    },
                ),
                OrinCommandReceipt(209, True, "events", {"events": []}),
            ]
        )

        with self.assertRaises(TimeoutError):
            self.service.cancel_task("kamikaze.execute")

        deadline = time.monotonic() + 1.0
        while self.service.state.phase == "reconciling" and time.monotonic() < deadline:
            time.sleep(0.01)
        cancels = [item for item in self.orin.requests if item[0] == "task.cancel"]
        self.assertEqual(cancels, [("task.cancel", {"task": "KAMIKAZE"})])
        self.assertIn(("mission.list", {}), self.orin.requests)
        self.assertEqual(self.service.state.phase, "cancelled")
        self.assertEqual(self.service.state.pending_command, "")


if __name__ == "__main__":
    unittest.main()
