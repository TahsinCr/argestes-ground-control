from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.core.events import EventStream
from src.models.telemetry import TelemetrySnapshot
from src.ui.adapters.runtime import QtRuntimeServices
from src.ui.adapters.services import QtTelemetryService


class _TelemetryBackend:
    def __init__(self) -> None:
        self.updated = EventStream[TelemetrySnapshot]()
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

    def test_backend_start_does_not_block_the_gui_thread(self) -> None:
        backend = _BlockingBackend()
        adapters = [_AdapterStub() for _index in range(9)]
        runtime = QtRuntimeServices(
            backend=backend,
            log_service=adapters[0],
            settings_service=adapters[1],
            telemetry_service=adapters[2],
            mission_service=adapters[3],
            command_service=adapters[4],
            parameter_service=adapters[5],
            video_service=adapters[6],
            tile_cache_service=adapters[7],
            connection_service=adapters[8],
        )
        connection = self.app.aboutToQuit.connect(runtime.stop)

        try:
            started_at = time.monotonic()
            runtime.start()
            elapsed = time.monotonic() - started_at

            self.assertLess(elapsed, 0.25)
            self.assertTrue(backend.started.wait(timeout=1.0))
            self.assertTrue(adapters[6].started)

            runtime.stop()
            self.assertTrue(backend.stopped.wait(timeout=1.0))
            self.assertTrue(adapters[6].stopped)
            self.assertTrue(all(adapter.closed for adapter in adapters))
        finally:
            self.app.disconnect(connection)


if __name__ == "__main__":
    unittest.main()
