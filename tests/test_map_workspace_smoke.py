from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MapWorkspaceSmokeTests(unittest.TestCase):
    def test_single_map_view_creation_restore_and_switch(self):
        # Chromium resources outlive individual widgets in a shared Qt suite.
        # Give the real map scenario its own application process and teardown.
        env = dict(os.environ)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
        env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
        result = subprocess.run(
            [sys.executable, "-m", "tests.test_map_workspace_smoke", "--smoke"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout)


def _run_smoke():
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    from src.container import create_container
    from src.ui.container import UiContainer
    from src.ui.themes.palette import DARK
    from src.ui.views.main_window import MainWindow

    app = QApplication(["map-workspace-smoke"])
    with tempfile.TemporaryDirectory() as directory:
        backend = create_container(Path(directory))
        ui = UiContainer(backend.container, base_dir=Path(directory), theme=DARK)
        window = ui.resolve(MainWindow)
        try:
            window.resize(1400, 900)
            window.show()
            QTest.qWait(100)
            original = window.workspace_views.active_id
            mission = window.workspace_views.create("Mission", "map")
            dock = window.workspace.panels["map_autonomy.view"]
            dock.toggleViewAction().trigger()
            QTest.qWait(30)
            assert dock.geometry() == window.workspace.rect(), dock.geometry()
            assert dock.widget().isVisibleTo(window)
            map_panel = dock.widget()
            for _ in range(200):
                if getattr(map_panel, "_js_ready", False):
                    break
                QTest.qWait(50)
            assert getattr(map_panel, "_js_ready", False), (
                "Leaflet/WebChannel did not initialize"
            )
            for _ in range(2):
                map_panel._build_webview()
                for _attempt in range(200):
                    if getattr(map_panel, "_js_ready", False):
                        break
                    QTest.qWait(50)
                assert getattr(map_panel, "_js_ready", False), (
                    "Leaflet/WebChannel did not recover after WebEngine recreation"
                )
            window.workspace_views.switch(original)
            window.workspace_views.switch(mission)
            QTest.qWait(30)
            assert dock.geometry() == window.workspace.rect(), dock.geometry()
            assert [
                key for key, panel in window.workspace.panels.items()
                if not panel.isHidden()
            ] == ["map_autonomy.view"]
            fixture = ROOT / "tests" / "fixtures" / "orphaned_dock_state.json"
            payload = json.loads(fixture.read_text(encoding="utf-8"))
            assert window.workspace.restore_layout(payload, restore_window=False)
            QTest.qWait(30)
            assert dock.geometry() == window.workspace.rect(), dock.geometry()
            assert dock.widget().isVisibleTo(window)
        finally:
            window.close()
            ui.shutdown()
            backend.shutdown()
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        _run_smoke()
    else:
        unittest.main()
