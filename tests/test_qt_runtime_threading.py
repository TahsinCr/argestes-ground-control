from __future__ import annotations

import os
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.core.events import EventBus
from src.models.logs import LogEntry
from src.models.telemetry import TelemetrySnapshot
from src.ui.adapters.runtime import QtRuntimeServices
from src.ui.adapters.services import QtLoggingService, QtTelemetryService
from src.services.logging_service import LoggingService


class _TelemetryBackend:
    def __init__(self) -> None:
        self.updated = EventBus[TelemetrySnapshot]()
        self.current = TelemetrySnapshot.zero()

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def push(self, snapshot: TelemetrySnapshot) -> None:
        self.current = snapshot
        self.updated.publish(snapshot)


class _BlockingBackend:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.stopped = threading.Event()

    def start(self) -> None:
        self.started.set()
        self.release.wait(timeout=5.0)

    def stop(self) -> None:
        self.stopped.set()
        self.release.set()


class _AdapterStub:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class QtRuntimeThreadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["qt-runtime-thread-tests"])

    def test_backend_events_reach_qt_signals_on_the_gui_thread(self) -> None:
        backend = _TelemetryBackend()
        adapter = QtTelemetryService(backend)
        received_on: list[int] = []
        adapter.telemetry_updated.connect(
            lambda _snapshot: received_on.append(threading.get_ident())
        )

        worker = threading.Thread(
            target=backend.updated.publish,
            args=(TelemetrySnapshot.zero(),),
        )
        worker.start()
        worker.join(timeout=1.0)

        self.assertEqual(received_on, [])
        QTest.qWait(50)
        self.assertEqual(received_on, [threading.get_ident()])
        adapter.close()
        adapter.deleteLater()
        self.app.processEvents()

    def test_telemetry_burst_is_coalesced_to_latest_ui_frame(self) -> None:
        backend = _TelemetryBackend()
        adapter = QtTelemetryService(backend)
        received: list[TelemetrySnapshot] = []
        adapter.telemetry_updated.connect(received.append)
        self.assertFalse(adapter._frame_timer.isActive())
        self.assertFalse(hasattr(adapter, "_dispatch_timer"))

        for packets_in in range(5_000):
            backend.updated.publish(
                TelemetrySnapshot.zero().copy(packets_in=packets_in)
            )

        self.assertEqual(received, [])
        QTest.qWait(50)
        self.assertGreaterEqual(len(received), 1)
        self.assertLessEqual(len(received), 2)
        self.assertEqual(received[-1].packets_in, 4_999)
        self.assertFalse(adapter._frame_timer.isActive())
        adapter.close()
        backend.updated.publish(TelemetrySnapshot.zero().copy(packets_in=9_999))
        QTest.qWait(25)
        self.assertEqual(received[-1].packets_in, 4_999)
        adapter.deleteLater()
        self.app.processEvents()

    def test_flight_dynamics_uses_immediate_latest_only_ui_path(self) -> None:
        backend = _TelemetryBackend()
        adapter = QtTelemetryService(backend)
        dynamics: list[TelemetrySnapshot] = []
        regular: list[TelemetrySnapshot] = []
        adapter.flight_dynamics_updated.connect(dynamics.append)
        adapter.telemetry_updated.connect(regular.append)
        started_at = datetime.now(UTC)

        for index in range(200):
            backend.updated.publish(
                TelemetrySnapshot.zero().copy(
                    telemetry_link=True,
                    autopilot_link=True,
                    pitch=float(index),
                    attitude_received_at=(
                        started_at + timedelta(milliseconds=index * 5)
                    ),
                )
            )

        self.assertEqual(dynamics, [])
        self.app.processEvents()
        self.assertEqual(len(dynamics), 1)
        self.assertEqual(dynamics[0].pitch, 199.0)
        self.assertEqual(regular, [])

        QTest.qWait(QtTelemetryService._UI_FRAME_INTERVAL_MS + 10)
        self.assertEqual(regular[-1].pitch, 199.0)
        adapter.close()
        adapter.deleteLater()
        self.app.processEvents()

    def test_log_burst_is_delivered_to_ui_as_one_batch(self) -> None:
        backend = LoggingService()
        adapter = QtLoggingService(backend)
        batches: list[tuple[LogEntry, ...]] = []
        adapter.entries_emitted.connect(batches.append)

        for index in range(500):
            backend.log("TEST", f"record-{index}")

        self.assertEqual(batches, [])
        QTest.qWait(80)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 500)
        self.assertEqual(batches[0][-1].message, "record-499")
        adapter.close()
        adapter.deleteLater()
        self.app.processEvents()

    def test_backend_start_does_not_block_gui(self) -> None:
        backend = _BlockingBackend()
        adapters = [_AdapterStub() for _index in range(2)]
        runtime = QtRuntimeServices(
            backend=backend,
            log_service=adapters[0],
            settings_service=adapters[1],
        )
        connection = self.app.aboutToQuit.connect(runtime.stop)

        try:
            started_at = time.monotonic()
            runtime.start()
            elapsed = time.monotonic() - started_at

            self.assertLess(elapsed, 0.25)
            self.assertTrue(backend.started.wait(timeout=1.0))
            runtime.stop()
            self.assertTrue(backend.stopped.wait(timeout=1.0))
            self.assertTrue(all(adapter.closed for adapter in adapters))
        finally:
            self.app.disconnect(connection)


if __name__ == "__main__":
    unittest.main()
