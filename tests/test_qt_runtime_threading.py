from __future__ import annotations

import os
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from src.core.events import EventBus
from src.models.logs import LogEntry
from src.models.telemetry import TelemetrySnapshot
from src.services.logging_service import LoggingService
from src.ui.adapters.runtime import QtRuntimeServices
from src.ui.adapters.services import QtLoggingService, QtTelemetryService, _QtAdapter
from src.ui.widgets.log_storage_page import LogStoragePage
from extensions.siha_teknofest_server.ui import ServerPanel


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

    def test_adapter_coalesces_only_explicit_latest_state_events(self) -> None:
        adapter = _QtAdapter()
        received: list[str] = []

        adapter._post(received.append, "state-old", coalesce_key=("status", "vehicle"))
        adapter._post(received.append, "command-1")
        adapter._post(received.append, "state-new", coalesce_key=("status", "vehicle"))
        adapter._post(received.append, "command-2")

        self.assertEqual(adapter._pending_events.qsize(), 3)
        QTest.qWait(50)
        self.assertEqual(received.count("state-old"), 0)
        self.assertEqual(received.count("state-new"), 1)
        self.assertEqual(
            [item for item in received if item.startswith("command-")],
            ["command-1", "command-2"],
        )
        adapter.close()
        adapter.deleteLater()
        self.app.processEvents()

    def test_adapter_coalesces_connection_snapshots_per_channel(self) -> None:
        adapter = _QtAdapter()
        received: list[tuple[str, str]] = []

        for channel, value in (
            ("camera", "old"),
            ("vehicle", "old"),
            ("camera", "new"),
            ("vehicle", "new"),
        ):
            adapter._post(
                received.append,
                (channel, value),
                coalesce_key=("connection.status", channel),
            )

        self.assertEqual(adapter._pending_events.qsize(), 2)
        QTest.qWait(50)
        self.assertEqual(
            dict(received), {"camera": "new", "vehicle": "new"}
        )
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

    def test_log_ui_overflow_is_marked_without_losing_persisted_records(self) -> None:
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            backend = LoggingService(log_path=Path(directory) / "gcs_test.json")
            adapter = QtLoggingService(backend)
            batches: list[tuple[LogEntry, ...]] = []
            adapter.entries_emitted.connect(batches.append)

            for index in range(adapter._MAX_PENDING_UI_LOGS + 5):
                backend.log("TEST", f"record-{index}")

            QTest.qWait(80)
            self.assertEqual(len(batches), 1)
            self.assertEqual(len(batches[0]), adapter._MAX_PENDING_UI_LOGS + 1)
            self.assertEqual(batches[0][0].action, "ui.log_entries_omitted")
            self.assertEqual(batches[0][0].details["omitted_count"], 5)
            self.assertEqual(batches[0][-1].message, "record-5004")
            self.assertEqual(len(json.loads((Path(directory) / "gcs_test.json").read_text())),
                             adapter._MAX_PENDING_UI_LOGS + 5)
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

    def test_disconnect_reaches_backend_while_reconnect_is_waiting(self) -> None:
        from types import SimpleNamespace

        from src.ui.adapters.services import QtConnectionControlService

        reconnect_started = threading.Event()
        disconnect_called = threading.Event()
        release_reconnect = threading.Event()
        completed: list[tuple[str, bool, str]] = []

        class Backend:
            changed = EventBus()

            @staticmethod
            def status(_channel):
                return SimpleNamespace(
                    summary="connecting",
                    connected=False,
                    options={"running": True},
                )

            def reconnect(self, _channel, _options):
                reconnect_started.set()
                release_reconnect.wait(timeout=2.0)
                return self.status(_channel)

            def disconnect(self, _channel):
                disconnect_called.set()
                release_reconnect.set()
                return self.status(_channel)

            def repair_serial_access(self, _channel):
                return self.status(_channel)

        adapter = QtConnectionControlService(Backend())
        adapter.operation_finished.connect(
            lambda channel, _operation_id, _action, ok, message, _status: completed.append(
                (channel, ok, message)
            )
        )
        reconnect = adapter.reconnect("telemetry", {})
        self.assertTrue(reconnect_started.wait(timeout=1.0))
        try:
            disconnect = adapter.disconnect("telemetry")
            self.assertIsNot(disconnect, reconnect)
            self.assertTrue(disconnect_called.wait(timeout=1.0))
        finally:
            release_reconnect.set()
            reconnect.join(timeout=1.0)
            if "disconnect" in locals():
                disconnect.join(timeout=1.0)
            deadline = time.monotonic() + 1.0
            while len(completed) < 2 and time.monotonic() < deadline:
                QTest.qWait(10)
            adapter.close()
        self.assertEqual(len(completed), 2)
        self.assertTrue(any(message == "Bağlantı kapatıldı" for _, _, message in completed))

    def test_log_export_runs_off_gui_thread_and_keeps_ui_responsive(self) -> None:
        started = threading.Event()
        release = threading.Event()
        worker_thread: list[int] = []

        class LoggingStub:
            def storage_summary(self):
                return {"file_count": 1, "total_bytes": 4, "archived_count": 0}

            def storage_files(self):
                return [{"name": "session.json", "size_bytes": 4, "active": True}]

            def export_log(self, _name, destination):
                worker_thread.append(threading.get_ident())
                started.set()
                release.wait(timeout=2.0)
                Path(destination).write_text("{}", encoding="utf-8")

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "export.json"
            page = LogStoragePage(LoggingStub(), "en")
            page.files.setCurrentRow(0)
            try:
                with patch(
                    "src.ui.widgets.log_storage_page.QFileDialog.getSaveFileName",
                    return_value=(str(destination), "JSON (*.json)"),
                ):
                    page._export_selected()
                self.assertTrue(started.wait(timeout=1.0))
                self.assertNotEqual(worker_thread[0], threading.get_ident())
                self.assertIsNotNone(page._export_future)
                self.assertIn("Exporting", page.status.text())
                release.set()
                deadline = time.monotonic() + 1.0
                while page._export_future is not None and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertIsNone(page._export_future)
                self.assertTrue(destination.exists())
                self.assertTrue(page.export_button.isEnabled())
            finally:
                release.set()
                page.close()

    def test_competition_history_summary_runs_off_gui_thread(self) -> None:
        started = threading.Event()
        release = threading.Event()
        worker_thread: list[int] = []

        class DatabaseStub:
            def storage_summary(self):
                worker_thread.append(threading.get_ident())
                started.set()
                release.wait(timeout=2.0)
                return {
                    "records": 3,
                    "size_bytes": 2048,
                    "oldest": "oldest",
                    "newest": "newest",
                }

        from concurrent.futures import ThreadPoolExecutor
        from types import SimpleNamespace

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="HistoryTest")
        panel = SimpleNamespace(
            _history_last_refresh=0.0,
            _history_executor=executor,
            _history_stats_future=None,
            _history_status="Ready",
            language="en",
            runtime=SimpleNamespace(database=DatabaseStub()),
            history_status=QLabel(""),
            history_count=QLabel("—"),
            history_size=QLabel("—"),
            history_range=QLabel("—"),
        )
        try:
            started_at = time.monotonic()
            ServerPanel._refresh_history_stats(panel)
            self.assertLess(time.monotonic() - started_at, 0.1)
            self.assertTrue(started.wait(timeout=1.0))
            self.assertNotEqual(worker_thread[0], threading.get_ident())
            self.assertEqual(panel.history_count.text(), "—")
            release.set()
            panel._history_stats_future.result(timeout=1.0)
            ServerPanel._finish_history_stats(panel)
            self.assertEqual(panel.history_count.text(), "3")
            self.assertEqual(panel.history_size.text(), "2.0 KiB")
            self.assertEqual(panel.history_range.text(), "oldest  →  newest")
        finally:
            release.set()
            executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
