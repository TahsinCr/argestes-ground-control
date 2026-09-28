from __future__ import annotations

import json
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
            map_dock = first.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            camera = first.add_panel("camera.visual", "Camera", QLabel("Video"),
                                     Qt.DockWidgetArea.RightDockWidgetArea)
            logs = first.add_panel("logs", "Logs", QLabel("Events"),
                                   Qt.DockWidgetArea.BottomDockWidgetArea)
            first.show()
            self.app.processEvents()
            first.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, camera)
            map_dock.hide()
            logs.hide()
            first.save_layout()
            first.close()

            second = PanelWorkspace(state_path)
            second.resize(1200, 800)
            restored_map = second.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
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
            self.assertTrue(restored_map.isHidden())
            self.assertTrue(restored_logs.isHidden())
            second.close()

    def test_bad_state_uses_default_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "layout.json"
            state_path.write_text("not json", encoding="utf-8")
            workspace = PanelWorkspace(state_path)
            self.assertFalse(workspace.restore_layout())
            workspace.close()

    def test_panel_sizes_survive_hide_show_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "layout.json"
            first = PanelWorkspace(state_path)
            first.resize(1000, 700)
            first.add_panel("map", "Map", QLabel("Map"),
                            Qt.DockWidgetArea.RightDockWidgetArea)
            docks = [
                first.add_panel(key, key, QLabel(key), Qt.DockWidgetArea.LeftDockWidgetArea)
                for key in ("flight", "telemetry", "commands")
            ]
            first.splitDockWidget(docks[0], docks[1], Qt.Orientation.Vertical)
            first.splitDockWidget(docks[1], docks[2], Qt.Orientation.Vertical)
            first.show()
            self.app.processEvents()
            first.resizeDocks(docks, [150, 300, 180], Qt.Orientation.Vertical)
            self.app.processEvents()
            first.enable_panel_size_restore(capture_current=True)
            expected_heights = [dock.height() for dock in docks]

            for dock in docks:
                dock.hide()
            self.app.processEvents()
            for dock in docks:
                dock.show()
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual([dock.height() for dock in docks], expected_heights)
            self.assertTrue(first.save_layout())
            first.close()

            second = PanelWorkspace(state_path)
            second.resize(1000, 700)
            second.add_panel("map", "Map", QLabel("Map"),
                             Qt.DockWidgetArea.RightDockWidgetArea)
            restored = [
                second.add_panel(key, key, QLabel(key), Qt.DockWidgetArea.LeftDockWidgetArea)
                for key in ("flight", "telemetry", "commands")
            ]
            second.splitDockWidget(restored[0], restored[1], Qt.Orientation.Vertical)
            second.splitDockWidget(restored[1], restored[2], Qt.Orientation.Vertical)
            second.show()
            self.app.processEvents()
            self.assertTrue(second.restore_layout())
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual([dock.height() for dock in restored], expected_heights)
            second.close()

    def test_panels_can_be_explicitly_tabified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            first = workspace.add_panel(
                "flight", "Flight", QLabel("Flight"),
                Qt.DockWidgetArea.LeftDockWidgetArea,
            )
            second = workspace.add_panel(
                "telemetry", "Telemetry", QLabel("Telemetry"),
                Qt.DockWidgetArea.LeftDockWidgetArea,
            )
            workspace.show()
            self.app.processEvents()
            workspace._tabify_panel(first, second)
            self.app.processEvents()
            self.assertIn(second, workspace.tabifiedDockWidgets(first))
            self.assertTrue(second.isVisible())
            workspace.close()

    def test_map_and_camera_can_move_split_and_tabify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            map_dock = workspace.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            camera = workspace.add_panel(
                "camera.visual", "Camera", QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            workspace.splitDockWidget(map_dock, camera, Qt.Orientation.Horizontal)
            workspace.resize(1200, 700)
            workspace.show()
            self.app.processEvents()
            self.assertIsNone(workspace.centralWidget())
            self.assertGreater(camera.x(), map_dock.x())
            workspace._tabify_panel(map_dock, camera)
            self.app.processEvents()
            self.assertIn(camera, workspace.tabifiedDockWidgets(map_dock))
            workspace.save_layout()
            workspace.close()

    def test_legacy_central_map_layout_migrates_without_losing_camera(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "layout.json"
            old = PanelWorkspace(state_path)
            old.STATE_VERSION = 1
            old.resize(1200, 700)
            old.setCentralWidget(QLabel("Map"))
            old.add_panel(
                "camera.visual", "Camera", QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            old.show()
            self.app.processEvents()
            old.save_layout()
            old.close()
            legacy_payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(legacy_payload["version"], 1)

            workspace = PanelWorkspace(state_path)
            workspace.resize(1200, 700)
            map_dock = workspace.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            camera = workspace.add_panel(
                "camera.visual", "Camera", QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            workspace.splitDockWidget(map_dock, camera, Qt.Orientation.Horizontal)
            workspace.show()
            self.app.processEvents()

            self.assertTrue(workspace.restore_layout())
            self.app.processEvents()
            self.assertEqual(
                workspace.dockWidgetArea(map_dock), Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.assertEqual(
                workspace.dockWidgetArea(camera), Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.assertGreater(map_dock.width(), 100)
            self.assertGreater(camera.width(), 100)
            self.assertGreater(camera.x(), map_dock.x())
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
            dock.close()
            self.assertTrue(dock.isHidden())
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
