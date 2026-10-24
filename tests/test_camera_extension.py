from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from extensions.camera.capture import GStreamerVideoService
from extensions.camera.models import VideoShareConfig, VideoSourceConfig, VideoStreamStats
from extensions.camera.runtime import CameraRuntime, CameraSettingsStore, _ConnectionAttempt
from src.container import create_container
from src.extension.host import ExtensionHost
from src.services.settings import SettingsService


class CameraExtensionTests(unittest.TestCase):
    def test_connection_timeout_is_validated_and_failed_attempts_finish_promptly(self) -> None:
        source = VideoSourceConfig(
            identifier="front",
            name="Front",
            source_uri="udp://0.0.0.0:5600",
            connection_timeout_s=25,
        )
        self.assertEqual(source.connection_timeout_s, 25.0)
        with self.assertRaises(ValueError):
            VideoSourceConfig(
                identifier="invalid",
                name="Invalid",
                source_uri="udp://0.0.0.0:5600",
                connection_timeout_s=0,
            )

        attempt = _ConnectionAttempt(Event())
        runtime = SimpleNamespace(
            _lock=RLock(), _connection_attempts={"front": attempt}
        )
        CameraRuntime._source_stats_changed(
            runtime,
            SimpleNamespace(source_id="front", stats=VideoStreamStats()),
        )
        self.assertFalse(attempt.ready.is_set())

        CameraRuntime._source_stats_changed(
            runtime,
            SimpleNamespace(source_id="front", stats=VideoStreamStats(connecting=True)),
        )
        self.assertTrue(attempt.started)
        self.assertFalse(attempt.ready.is_set())

        CameraRuntime._source_stats_changed(
            runtime,
            SimpleNamespace(source_id="front", stats=VideoStreamStats()),
        )
        self.assertTrue(attempt.ready.is_set())

    def test_camera_connection_can_be_cancelled_without_waiting_for_timeout(self) -> None:
        attempt = _ConnectionAttempt(Event())
        reconnect_started, disconnected = Event(), Event()

        class Video:
            source_stats = ()

            def reconnect_source(self, _identifier: str) -> None:
                reconnect_started.set()

            def disconnect_source(self, _identifier: str) -> None:
                disconnected.set()

        runtime = SimpleNamespace(
            _lock=RLock(),
            _connection_attempts={"front": attempt},
            video=Video(),
        )
        runtime._finish_connection_attempt = lambda identifier, current: (
            CameraRuntime._finish_connection_attempt(runtime, identifier, current)
        )
        source = VideoSourceConfig(
            identifier="front",
            name="Front",
            source_uri="udp://0.0.0.0:5600",
            connection_timeout_s=30,
        )

        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(
                CameraRuntime._connect_source,
                runtime,
                "front",
                source,
                attempt,
                None,
            )
            self.assertTrue(reconnect_started.wait(1))
            self.assertTrue(CameraRuntime.cancel_source_connection(runtime, "front"))
            with self.assertRaises(CancelledError):
                future.result(1)
        self.assertTrue(attempt.cancelled)
        self.assertTrue(disconnected.is_set())

    def test_camera_connection_timeout_stops_a_source_without_a_first_frame(self) -> None:
        disconnected = Event()

        class Video:
            source_stats = ()

            def reconnect_source(self, _identifier: str) -> None:
                pass

            def disconnect_source(self, _identifier: str) -> None:
                disconnected.set()

        attempt = _ConnectionAttempt(Event())
        runtime = SimpleNamespace(
            _lock=RLock(),
            _connection_attempts={"front": attempt},
            video=Video(),
        )
        runtime._finish_connection_attempt = lambda identifier, current: (
            CameraRuntime._finish_connection_attempt(runtime, identifier, current)
        )
        source = VideoSourceConfig(
            identifier="front",
            name="Front",
            source_uri="udp://0.0.0.0:5600",
            connection_timeout_s=1,
        )

        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(
                CameraRuntime._connect_source,
                runtime,
                "front",
                source,
                attempt,
                None,
            )
            with self.assertRaises(TimeoutError):
                future.result(2)
        self.assertTrue(disconnected.is_set())

    def test_capture_watchdog_stops_when_no_first_video_frame_arrives(self) -> None:
        service = GStreamerVideoService(
            "udp", "udp://0.0.0.0:5600", connection_timeout_s=1
        )
        service._started = True
        service._started_at = 10
        service._last_frame_at = 0
        events: list[tuple[str, dict]] = []
        service._event = lambda name, *_args, **kwargs: events.append(
            (name, kwargs)
        )

        service._check_connection_timeout(now=12)

        self.assertTrue(service._stop_event.is_set())
        self.assertEqual(events[0][0], "video.connection_timeout")

    def test_camera_is_optional_and_does_not_auto_connect_without_profile_flag(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            container = create_container(root)
            try:
                host = container.resolve(ExtensionHost)
                self.assertFalse(host.is_active("camera"))
                self.assertFalse(
                    [
                        item
                        for item in host.visual_views()
                        if item.identifier.startswith("camera.")
                    ]
                )
                host.set_enabled("camera", True)
            finally:
                container.shutdown()
            container = create_container(root)
            try:
                host = container.resolve(ExtensionHost)
                self.assertTrue(host.is_active("camera"))
                self.assertIsNotNone(host.settings_contribution("camera"))
                self.assertEqual(
                    len(
                        [
                            item
                            for item in host.status_widgets()
                            if item.identifier.startswith("camera.")
                        ]
                    ),
                    1,
                )
                runtime = container.resolve(CameraRuntime)
                runtime.start()
                self.assertFalse(runtime.video._started)
                runtime.stop()
            finally:
                container.shutdown()

    def test_camera_configuration_reads_current_extension_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsService(root)
            source = VideoSourceConfig(
                identifier="front",
                name="Ön",
                source_type="udp",
                source_uri="127.0.0.1:5600",
                codec="h264",
            )
            output = VideoShareConfig(
                identifier="live",
                name="Yayın",
                endpoint="239.1.1.1:10021",
                source_id="front",
                output_type="stream",
                enabled=False,
            )
            from dataclasses import asdict

            settings.update_extension(
                "camera",
                config={
                    "video_sources": [asdict(source)],
                    "active_video_source_id": "front",
                    "video_shares": [asdict(output)],
                },
            )
            store = CameraSettingsStore(root, settings)
            self.assertEqual(store.current.sources[0].name, "Ön")
            self.assertEqual(store.current.outputs[0].identifier, "live")
            store.save(store.current)
            self.assertTrue(store.path.exists())
            self.assertEqual(CameraSettingsStore(root, settings).current, store.current)

    def test_record_all_creates_one_fixed_fps_output_per_camera(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            container = create_container(root)
            try:
                container.resolve(ExtensionHost).set_enabled("camera", True)
            finally:
                container.shutdown()
            container = create_container(root)
            try:
                runtime = container.resolve(CameraRuntime)
                current = runtime.settings.current
                second = VideoSourceConfig(
                    identifier="rear",
                    name="Arka",
                    source_type="usb",
                    source_uri="/dev/video2",
                    codec="mjpeg",
                )
                runtime.save_sources(
                    (*current.sources, second), current.active_source_id
                )
                runtime.record_all(True)
                outputs = [
                    item
                    for item in runtime.settings.current.outputs
                    if item.output_type == "record"
                ]
                self.assertEqual(
                    {item.source_id for item in outputs}, {"video-primary", "rear"}
                )
                self.assertTrue(
                    all(item.fps == 30 and item.enabled for item in outputs)
                )
                runtime.record_all(False)
                self.assertTrue(
                    all(not item.enabled for item in runtime.settings.current.outputs)
                )
                runtime.stop()
            finally:
                container.shutdown()

    def test_enabled_camera_restores_archived_wall_and_connection_controls(
        self,
    ) -> None:
        script = """
import tempfile
from pathlib import Path
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication
from extensions.camera.ui.video import VideoPanel
from extensions.camera.models import VideoShareConfig
from src.container import create_container
from src.extension.host import ExtensionHost
from src.services.abstracts.telemetry import TelemetryService
from src.ui.container import UiContainer
from src.ui.themes.palette import DARK
from src.ui.views.main_window import MainWindow
app = QApplication(["camera-tests"])
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    first = create_container(root)
    first.resolve(ExtensionHost).set_enabled("camera", True)
    first.shutdown()
    backend = create_container(root)
    backend.resolve(ExtensionHost).start()
    backend.resolve(TelemetryService).start()
    ui = UiContainer(backend.container, base_dir=root, theme=DARK)
    window = ui.resolve(MainWindow)
    assert 'camera.wall' in window._extension_visual_by_id
    camera = window._extension_visual_by_id['camera.wall']
    assert isinstance(camera, VideoPanel)
    assert not hasattr(camera.viewport, "aim_button")
    assert tuple(camera.split_buttons) == (1, 2, 3, 4)
    assert len([key for key in window._extension_status_by_id if key.startswith("camera.")]) == 1
    status = window._extension_status_by_id["camera.connections"]
    assert status.video_chip.text_label.text() == "KAMERA"
    assert status.share_chip.text_label.text() == "YAYIN / KAYIT"
    assert backend.resolve(ExtensionHost).settings_contribution("camera") is not None
    camera_settings = (
        backend.resolve(ExtensionHost)
        .settings_contribution("camera")
        .page_factory(window)
    )
    assert [camera_settings.tabText(i) for i in range(camera_settings.count())] in (
        ["Kamera", "Yayın", "Kayıt"], ["Camera", "Streaming", "Recording"]
    )
    outputs = (
        VideoShareConfig(
            "stream", "Yayın", "127.0.0.1:5000", source_id="video-primary"
        ),
        VideoShareConfig(
            "record",
            "Kayıt",
            "data/recordings",
            source_id="video-primary",
            output_type="record",
        ),
    )
    camera_settings.streaming.show_shares(outputs, ())
    camera_settings.recording.show_shares(outputs, ())
    assert camera_settings.streaming.list_layout.count() == 2
    assert camera_settings.recording.list_layout.count() == 2
    camera_settings.prepare_shutdown()
    camera_settings.deleteLater()
    camera_dock = window.workspace.panels["camera.wall"]
    assert camera_dock.toggleViewAction().isCheckable()
    window.show()
    app.processEvents()
    assert camera_dock.geometry().x() > window.primary_dock.geometry().x()
    window.workspace._tabify_panel(window.primary_dock, camera_dock)
    app.processEvents()
    window.extension_panel._toggle("camera", False)
    assert 'camera.wall' not in window._extension_visual_by_id
    assert not any(key.startswith("camera.") for key in window._extension_status_by_id)
    assert not any(key.startswith("camera.") for key in window.workspace.panels)
    assert "camera.wall" not in window.workspace.panels
    assert not backend.resolve(ExtensionHost).is_active("camera")
    window.extension_panel._toggle("camera", True)
    assert 'camera.wall' in window._extension_visual_by_id
    assert len([key for key in window._extension_status_by_id if key.startswith("camera.")]) == 1
    camera_dock = window.workspace.panels["camera.wall"]
    assert camera_dock.toggleViewAction().isCheckable()
    assert camera_dock in window.workspace.tabifiedDockWidgets(window.primary_dock)
    window.extension_panel._toggle("vehicle_connection", False)
    assert not any(key.startswith("vehicle_connection.") for key in window._extension_status_by_id)
    assert not window._extension_status_by_id["mavlink.gps"].isHidden()
    assert not window._extension_status_by_id["mavlink.autopilot"].isHidden()
    window.extension_panel._toggle("vehicle_connection", True)
    assert "vehicle_connection.connection" in window._extension_status_by_id
    assert "vehicle_connection.quality" in window._extension_status_by_id
    window.close()
    ui.shutdown()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    backend.resolve(TelemetryService).stop()
    backend.resolve(ExtensionHost).stop()
    backend.shutdown()
print("camera-ui-ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("camera-ui-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
