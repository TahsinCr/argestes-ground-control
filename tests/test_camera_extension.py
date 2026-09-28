from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from extensions.camera.models import VideoShareConfig, VideoSourceConfig
from extensions.camera.runtime import CameraRuntime, CameraSettingsStore
from src.container import create_container
from src.extension.host import ExtensionHost


class CameraExtensionTests(unittest.TestCase):
    def test_camera_is_optional_and_does_not_auto_connect_without_profile_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            container = create_container(root)
            try:
                host = container.resolve(ExtensionHost)
                self.assertFalse(host.is_active("camera"))
                self.assertEqual(host.visual_views(), ())
                host.set_enabled("camera", True)
            finally:
                container.shutdown()
            container = create_container(root)
            try:
                host = container.resolve(ExtensionHost)
                self.assertTrue(host.is_active("camera"))
                self.assertIsNotNone(host.settings_action("camera"))
                self.assertEqual(len(host.status_widgets()), 1)
                runtime = container.resolve(CameraRuntime)
                runtime.start()
                self.assertFalse(runtime.video._started)
                runtime.stop()
            finally:
                container.shutdown()

    def test_camera_configuration_migrates_legacy_profiles_and_keeps_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "data/settings.json"
            settings.parent.mkdir(parents=True)
            source = VideoSourceConfig(identifier="front", name="Ön", source_type="udp",
                                       source_uri="127.0.0.1:5600", codec="h264")
            output = VideoShareConfig(identifier="live", name="Yayın", endpoint="239.1.1.1:10021",
                                      source_id="front", output_type="stream", enabled=False)
            import json
            from dataclasses import asdict
            settings.write_text(json.dumps({"backend": {
                "video_sources": [asdict(source)], "active_video_source_id": "front",
                "video_shares": [asdict(output)]}}), encoding="utf-8")
            store = CameraSettingsStore(root)
            self.assertEqual(store.current.sources[0].name, "Ön")
            self.assertEqual(store.current.outputs[0].identifier, "live")
            store.save(store.current)
            self.assertTrue(store.path.exists())
            self.assertEqual(CameraSettingsStore(root).current, store.current)

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
                second = VideoSourceConfig(identifier="rear", name="Arka", source_type="usb",
                                           source_uri="/dev/video2", codec="mjpeg")
                runtime.save_sources((*current.sources, second), current.active_source_id)
                runtime.record_all(True)
                outputs = [item for item in runtime.settings.current.outputs if item.output_type == "record"]
                self.assertEqual({item.source_id for item in outputs}, {"video-primary", "rear"})
                self.assertTrue(all(item.fps == 30 and item.enabled for item in outputs))
                runtime.record_all(False)
                self.assertTrue(all(not item.enabled for item in runtime.settings.current.outputs))
                runtime.stop()
            finally:
                container.shutdown()

    def test_enabled_camera_restores_archived_wall_and_connection_controls(self) -> None:
        script = '''
import tempfile
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from extensions.camera.ui.video import VideoPanel
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
    assert len(window._extension_visual_widgets) == 1
    camera = window._extension_visual_widgets[0]
    assert isinstance(camera, VideoPanel)
    assert tuple(camera.split_buttons) == (1, 2, 3, 4)
    assert len(window._extension_status_widgets) == 1
    status = window._extension_status_widgets[0]
    assert status.video_chip.text_label.text() == "VIDEO"
    assert status.share_chip.text_label.text() == "VIDEO SHARE"
    assert backend.resolve(ExtensionHost).settings_action("camera") is not None
    camera_dock = window.workspace.panels["camera.wall"]
    window.show()
    app.processEvents()
    assert camera_dock.geometry().x() > window.map_dock.geometry().x()
    window.workspace._tabify_panel(window.map_dock, camera_dock)
    app.processEvents()
    window.extension_panel._toggle("camera", False)
    assert not window._extension_visual_widgets
    assert not window._extension_status_widgets
    assert not any(key.startswith("camera.") for key in window.workspace.panels)
    assert not backend.resolve(ExtensionHost).is_active("camera")
    window.extension_panel._toggle("camera", True)
    assert len(window._extension_visual_widgets) == 1
    assert len(window._extension_status_widgets) == 1
    camera_dock = window.workspace.panels["camera.wall"]
    assert camera_dock in window.workspace.tabifiedDockWidgets(window.map_dock)
    window.extension_panel._toggle("telemetry", False)
    assert window.telemetry_status.isHidden()
    assert not window.gps_status.isHidden()
    assert not window.autopilot_status.isHidden()
    window.extension_panel._toggle("telemetry", True)
    assert not window.telemetry_status.isHidden()
    window.close()
    ui.shutdown()
    backend.resolve(TelemetryService).stop()
    backend.resolve(ExtensionHost).stop()
    backend.shutdown()
print("camera-ui-ok")
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("camera-ui-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
