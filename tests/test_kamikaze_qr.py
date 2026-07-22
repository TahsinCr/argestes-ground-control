from __future__ import annotations

import time
import unittest

from src.core.events import EventStream
from src.models.commands import CommandRequest, CommandResult
from src.models.video import QrDetection, QrDetectionSnapshot
from src.services.logging_service import LoggingService
from src.services.orchestration.kamikaze import KamikazeQrWorkflow


class _Commands:
    def __init__(self) -> None:
        self.dispatched = EventStream()
        self.completed = EventStream()


class _Qr:
    def __init__(self) -> None:
        self.qr_changed = EventStream()
        self.qr_active = False
        self.submitted = []

    def set_qr_active(self, active: bool) -> None:
        self.qr_active = bool(active)

    def mark_qr_submitted(self, data: str) -> None:
        self.submitted.append(data)


class _Competition:
    def __init__(self) -> None:
        self.events = []

    def submit_kamikaze_event(self, event) -> None:
        self.events.append(event)


class KamikazeQrWorkflowTests(unittest.TestCase):
    @staticmethod
    def _result(task: str, *, accepted: bool = True) -> CommandResult:
        request = CommandRequest("SET_MISSION_TASK", {"task": task}, "test")
        return CommandResult(request, accepted, "result", {"task": task})

    @staticmethod
    def _decoded(text: str = "TEKNOFEST-QR-2026") -> QrDetectionSnapshot:
        return QrDetectionSnapshot(
            (
                QrDetection(
                    "qr-1",
                    ((10.0, 10.0), (80.0, 10.0), (80.0, 80.0), (10.0, 80.0)),
                    True,
                    text,
                ),
            )
        )

    def test_kamikaze_command_activates_qr_and_submits_first_decode_once(self) -> None:
        commands = _Commands()
        qr = _Qr()
        competition = _Competition()
        logs = LoggingService()
        workflow = KamikazeQrWorkflow(
            commands,  # type: ignore[arg-type]
            qr,  # type: ignore[arg-type]
            competition,  # type: ignore[arg-type]
            logs.child("KAMIKAZE"),
        )
        workflow.start()

        commands.completed.publish(self._result("KAMIKAZE"))
        self.assertTrue(qr.qr_active)
        self.assertTrue(workflow.state.active)

        qr.qr_changed.publish(self._decoded())
        deadline = time.monotonic() + 1.0
        while not workflow.state.submitted and time.monotonic() < deadline:
            time.sleep(0.01)
        qr.qr_changed.publish(self._decoded())
        time.sleep(0.03)

        self.assertTrue(workflow.state.submitted)
        self.assertEqual(len(competition.events), 1)
        self.assertEqual(qr.submitted, ["TEKNOFEST-QR-2026"])
        self.assertEqual(competition.events[0].qr_text, "TEKNOFEST-QR-2026")
        self.assertEqual(
            competition.events[0].vehicle_source,
            "opencv-kamikaze-simulation",
        )
        actions = [entry.action for entry in logs.history]
        self.assertIn("kamikaze.qr_mode_started", actions)
        self.assertIn("kamikaze.qr_captured", actions)
        self.assertIn("kamikaze.qr_submitted", actions)

        commands.completed.publish(self._result("TARGET_LOCK"))
        self.assertFalse(qr.qr_active)
        self.assertFalse(workflow.state.active)
        workflow.stop()

    def test_rejected_kamikaze_command_does_not_start_qr(self) -> None:
        commands = _Commands()
        qr = _Qr()
        workflow = KamikazeQrWorkflow(
            commands,  # type: ignore[arg-type]
            qr,  # type: ignore[arg-type]
            _Competition(),  # type: ignore[arg-type]
        )
        workflow.start()

        commands.completed.publish(self._result("KAMIKAZE", accepted=False))

        self.assertFalse(qr.qr_active)
        self.assertFalse(workflow.state.active)
        workflow.stop()


if __name__ == "__main__":
    unittest.main()
