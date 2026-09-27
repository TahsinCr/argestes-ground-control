from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

from src.ui.widgets.panel_workspace import PanelWorkspace


class PanelWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["yki-workspace-tests"])

    def test_dock_position_visibility_and_size_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "layout.json"
            first = PanelWorkspace(state_path)
            first.resize(1200, 800)
            first.setCentralWidget(QLabel("Map"))
            camera = first.add_panel("camera.visual", "Camera", QLabel("Video"),
                                     Qt.DockWidgetArea.RightDockWidgetArea)
            logs = first.add_panel("logs", "Logs", QLabel("Events"),
                                   Qt.DockWidgetArea.BottomDockWidgetArea)
            first.show()
            self.app.processEvents()
            first.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, camera)
            logs.hide()
            first.save_layout()
            first.close()

            second = PanelWorkspace(state_path)
            second.resize(1200, 800)
            second.setCentralWidget(QLabel("Map"))
            restored_camera = second.add_panel("camera.visual", "Camera", QLabel("Video"),
                                               Qt.DockWidgetArea.RightDockWidgetArea)
            restored_logs = second.add_panel("logs", "Logs", QLabel("Events"),
                                             Qt.DockWidgetArea.BottomDockWidgetArea)
            second.show()
            self.app.processEvents()
            self.assertTrue(second.restore_layout())
            self.assertEqual(
                second.dockWidgetArea(restored_camera),
                Qt.DockWidgetArea.LeftDockWidgetArea,
            )
            self.assertTrue(restored_logs.isHidden())
            second.close()

    def test_bad_state_uses_default_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "layout.json"
            state_path.write_text("not json", encoding="utf-8")
            workspace = PanelWorkspace(state_path)
            self.assertFalse(workspace.restore_layout())
            workspace.close()

    def test_panel_visibility_stays_available_outside_edit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            dock = workspace.add_panel(
                "logs", "Logs", QLabel("Events"),
                Qt.DockWidgetArea.BottomDockWidgetArea,
            )
            workspace.show()
            self.app.processEvents()
            self.assertIsNotNone(dock.titleBarWidget())
            self.assertEqual(
                dock.features(),
                dock.DockWidgetFeature.DockWidgetClosable,
            )
            dock.toggleViewAction().trigger()
            self.assertTrue(dock.isHidden())
            dock.toggleViewAction().trigger()
            self.assertFalse(dock.isHidden())
            workspace.set_edit_mode(True)
            self.assertIsNone(dock.titleBarWidget())
            self.assertTrue(
                dock.features() & dock.DockWidgetFeature.DockWidgetMovable
            )
            workspace.set_edit_mode(False)
            self.assertIsNotNone(dock.titleBarWidget())
            workspace.close()


if __name__ == "__main__":
    unittest.main()
