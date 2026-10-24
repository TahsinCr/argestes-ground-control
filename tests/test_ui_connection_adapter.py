from __future__ import annotations

import threading
import queue
import unittest
import time
from types import SimpleNamespace

from src.core.events import EventBus
from src.ui.adapters.services import QtConnectionControlService
from extensions.vehicle_connection.ui.connection_editor import ConnectionEditor


class _Backend:
    def __init__(self) -> None:
        self.changed = EventBus()
        self.reconnect_started = threading.Event()
        self.disconnect_started = threading.Event()
        self.release_reconnect = threading.Event()
        self.release_disconnect = threading.Event()
        self.reconnect_calls = 0
        self.connected = False

    def status(self, _channel: str):
        return SimpleNamespace(
            channel="telemetry",
            connected=self.connected,
            detail="",
            summary="connected",
            options={"running": self.connected},
        )

    def reconnect(self, _channel: str, _options):
        self.reconnect_calls += 1
        self.reconnect_started.set()
        self.release_reconnect.wait(2.0)
        self.connected = True
        return self.status("telemetry")

    def disconnect(self, _channel: str):
        self.disconnect_started.set()
        self.release_disconnect.wait(2.0)
        self.connected = False
        return self.status("telemetry")

    def repair_serial_access(self, _channel: str):
        return self.status("telemetry")


class _ImmediateConnectionAdapter(QtConnectionControlService):
    def __init__(self, backend) -> None:
        super().__init__(backend)
        self.completions: queue.Queue[tuple] = queue.Queue()

    def _post(self, callback, *args, **_kwargs) -> None:
        if len(args) == 6 and isinstance(args[1], int):
            self.completions.put(args)
            return
        callback(*args)


class QtConnectionControlAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = _Backend()
        self.service = _ImmediateConnectionAdapter(self.backend)
        self.started: list[tuple[str, int, str]] = []
        self.finished: list[tuple[str, int, str, bool, str, object]] = []
        self.lock = threading.Lock()

        def on_started(channel, operation_id, action) -> None:
            with self.lock:
                self.started.append((channel, operation_id, action))

        self.service.operation_started.connect(on_started)

    def tearDown(self) -> None:
        self.backend.release_reconnect.set()
        self.backend.release_disconnect.set()
        self.service.close()

    def _operation_id(self, action: str) -> int:
        with self.lock:
            return next(item[1] for item in reversed(self.started) if item[2] == action)

    def _wait_finished(self, operation_id: int) -> None:
        deadline = time.monotonic() + 2.0
        while not any(item[1] == operation_id for item in self.finished):
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0.0, f"operation {operation_id} did not finish")
            self.finished.append(self.service.completions.get(timeout=remaining))

    def test_new_disconnect_has_its_own_generation_and_old_result_is_distinguishable(self) -> None:
        reconnect = self.service.reconnect("telemetry", {})
        self.assertTrue(self.backend.reconnect_started.wait(1.0))
        reconnect_id = self._operation_id("reconnect")

        disconnect = self.service.disconnect("telemetry")
        self.assertTrue(self.backend.disconnect_started.wait(1.0))
        disconnect_id = self._operation_id("disconnect")
        self.assertGreater(disconnect_id, reconnect_id)

        self.backend.release_disconnect.set()
        disconnect.join(1.0)
        self._wait_finished(disconnect_id)
        self.backend.release_reconnect.set()
        reconnect.join(1.0)
        self._wait_finished(reconnect_id)

        with self.lock:
            completed_ids = [item[1] for item in self.finished]
        self.assertIn(disconnect_id, completed_ids)
        self.assertIn(reconnect_id, completed_ids)

    def test_reconnect_is_reported_instead_of_silently_reused_during_disconnect(self) -> None:
        disconnect = self.service.disconnect("telemetry")
        self.assertTrue(self.backend.disconnect_started.wait(1.0))

        rejected = self.service.reconnect("telemetry", {"profile_id": "new"})
        rejected.join(1.0)
        rejected_id = self._operation_id("reconnect")
        self._wait_finished(rejected_id)

        result = next(item for item in self.finished if item[1] == rejected_id)
        self.assertFalse(result[3])
        self.assertEqual(result[4], "operation_in_progress")
        self.assertEqual(self.backend.reconnect_calls, 0)

        self.backend.release_disconnect.set()
        disconnect.join(1.0)


class ConnectionEditorOperationTests(unittest.TestCase):
    def test_stale_operation_completion_cannot_clear_current_ui_state(self) -> None:
        class Panel:
            calls = 0

            def finish_connection(self, **_kwargs) -> None:
                self.calls += 1

        panel = Panel()
        editor = SimpleNamespace(
            _latest_operation_id=8,
            connection_service=SimpleNamespace(
                status=lambda _channel: self.fail("stale result queried backend")
            ),
            profiles_panel=panel,
            profiles=SimpleNamespace(active_identifier="profile"),
            language="en",
        )

        ConnectionEditor._operation_finished(
            editor,
            "telemetry",
            7,
            "reconnect",
            False,
            "old operation failed",
            None,
        )

        self.assertEqual(panel.calls, 0)


if __name__ == "__main__":
    unittest.main()
