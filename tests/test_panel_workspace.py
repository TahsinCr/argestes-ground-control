from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PyQt6.QtGui import QDragMoveEvent, QDropEvent, QMouseEvent, QRegion
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDockWidget, QLabel, QStyle, QTabWidget

from src.ui.themes.style import DARK, LIGHT, build_stylesheet
from src.ui.widgets.panel_workspace import PanelWorkspace


class PanelWorkspaceTests(unittest.TestCase):
    def test_legacy_offscreen_singleton_map_recovers_and_round_trips(self):
        fixture = Path(__file__).parent / "fixtures" / "orphaned_dock_state.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            map_dock = workspace.add_panel(
                "map_autonomy.view",
                "Map",
                QLabel("Map"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            workspace.resize(1200, 800)
            workspace.show()
            self.app.processEvents()
            try:
                self.assertTrue(workspace.restore_layout(payload, restore_window=False))
                QTest.qWait(30)
                self.assertFalse(map_dock.isHidden())
                self.assertEqual(map_dock.geometry(), workspace.rect())
                repaired = workspace.capture_layout()
                self.assertTrue(
                    workspace.restore_layout(repaired, restore_window=False)
                )
                QTest.qWait(30)
                self.assertEqual(map_dock.geometry(), workspace.rect())
                workspace.clear_layout()
                retired = QDockWidget("Retired extension", workspace)
                retired.setObjectName("panel.siha_teknofest_server.servers")
                self.assertFalse(workspace.restoreDockWidget(retired))
                retired.deleteLater()
                map_dock.toggleViewAction().trigger()
                QTest.qWait(30)
                self.assertEqual(map_dock.geometry(), workspace.rect())
            finally:
                workspace.close()

    def test_drag_targets_and_preview_survive_overlay_paint_occlusion(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            source = workspace.add_panel(
                "source", "Source", QLabel("Source"), Qt.DockWidgetArea.LeftDockWidgetArea
            )
            target = workspace.add_panel(
                "target", "Target", QLabel("Target"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            workspace.resize(1000, 700)
            workspace.show()
            if self.app.platformName() != "offscreen":
                self.assertTrue(QTest.qWaitForWindowExposed(workspace))
            self.app.processEvents()
            workspace._drag_source = source
            overlay = workspace._drop_overlay
            overlay.setGeometry(workspace.rect())
            overlay.show()
            overlay.raise_()
            mime = QMimeData()
            mime.setData("application/x-yki-panel-drag", b"source")
            # Paint visibility is not a reliable measure of layout visibility.
            with patch.object(type(target), "visibleRegion", return_value=QRegion()):
                bounds = workspace._drop_bounds(target)
                self.assertFalse(bounds.isEmpty())
                for relation, zone in workspace._drop_zones(bounds).items():
                    event = QDragMoveEvent(
                        zone.center(), Qt.DropAction.MoveAction, mime,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                    )
                    overlay.dragMoveEvent(event)
                    self.assertTrue(event.isAccepted(), relation)
                    self.assertEqual(overlay.active, (target, relation))
                    self.assertFalse(overlay.grab().isNull())
            overlay.hide()
            workspace._drag_source = None
            workspace.close()

    def test_size_lock_survives_window_resize_and_layout_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            first = workspace.add_panel(
                "first", "First", QLabel("First"), Qt.DockWidgetArea.LeftDockWidgetArea
            )
            workspace.add_panel(
                "second",
                "Second",
                QLabel("Second"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            workspace.resize(900, 600)
            workspace.show()
            self.app.processEvents()
            workspace.set_panel_size_locked(first, True)
            before = first.widget().size()
            payload = workspace.capture_layout()
            workspace.resize(1200, 850)
            self.app.processEvents()
            self.assertEqual(first.widget().size(), before)
            workspace.restore_layout(payload, restore_window=False)
            self.app.processEvents()
            self.assertEqual(first.widget().size(), before)
            workspace.set_panel_size_locked(first, False)
            self.assertNotEqual(
                first.widget().minimumSize(), first.widget().maximumSize()
            )
            workspace.close()

    def test_visible_tab_can_split_out_at_its_own_group_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            first, second = [
                workspace.add_panel(
                    key, key, QLabel(key), Qt.DockWidgetArea.RightDockWidgetArea
                )
                for key in ("map", "camera")
            ]
            workspace.resize(1000, 600)
            workspace.show()
            for relation, point in (
                ("below", QPoint(400, 490)),
                ("right", QPoint(900, 300)),
                ("above", QPoint(400, 70)),
                ("left", QPoint(70, 300)),
            ):
                workspace.place_panel(first, second, "tab")
                second.raise_()
                self.app.processEvents()
                target = workspace._drop_target(
                    second.mapToGlobal(point), source=second
                )
                self.assertIsNotNone(target)
                self.assertEqual(target, (first, relation))
                bounds = workspace._drop_bounds(first)
                self.assertGreater(bounds.width(), 900)
                self.assertTrue(workspace._apply_drop(second, target))
                self.app.processEvents()
                self.assertNotIn(second, workspace.tabifiedDockWidgets(first))
            workspace.close()

    def test_outer_edge_drop_splits_a_tabbed_source_from_its_group(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            map_panel = workspace.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            camera = workspace.add_panel(
                "camera",
                "Camera",
                QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            workspace.resize(1000, 600)
            workspace.show()
            self.app.processEvents()
            workspace.place_panel(map_panel, camera, "tab")
            camera.raise_()
            self.app.processEvents()

            for relation in ("left", "right", "above", "below"):
                workspace.place_panel(map_panel, camera, "tab")
                camera.raise_()
                self.app.processEvents()
                bounds = workspace._panel_bounds(camera)
                edge_points = {
                    "left": QPoint(bounds.left() + 2, bounds.center().y()),
                    "right": QPoint(bounds.right() - 2, bounds.center().y()),
                    "above": QPoint(bounds.center().x(), bounds.top() + 2),
                    "below": QPoint(bounds.center().x(), bounds.bottom() - 2),
                }
                point = edge_points[relation]
                workspace._drag_source = camera
                overlay = workspace._drop_overlay
                overlay.setGeometry(workspace.rect())
                overlay.show()
                mime = QMimeData()
                mime.setData("application/x-yki-panel-drag", b"camera")
                event = QDropEvent(
                    QPointF(overlay.mapFromGlobal(workspace.mapToGlobal(point))),
                    Qt.DropAction.MoveAction,
                    mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                overlay.dropEvent(event)
                self.assertTrue(event.isAccepted())
                pending = workspace._pending_drop
                self.assertEqual(pending, (camera, (map_panel, relation)))
                overlay.hide()
                workspace._drag_source = None
                workspace._pending_drop = None
                self.assertTrue(workspace._apply_drop(*pending))
                self.app.processEvents()
                self.assertNotIn(camera, workspace.tabifiedDockWidgets(map_panel))
                self.assertTrue(map_panel.isVisible())
                self.assertTrue(camera.isVisible())
                self.assertTrue(workspace.rect().intersects(map_panel.geometry()))
                self.assertTrue(workspace.rect().intersects(camera.geometry()))
            workspace.close()

    def test_splitting_tab_group_does_not_add_source_as_another_tab(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            workspace.resize(1000, 700)
            docks = [
                workspace.add_panel(
                    key, key, QLabel(key), Qt.DockWidgetArea.RightDockWidgetArea
                )
                for key in ("map", "logs", "camera")
            ]
            workspace.show()
            self.app.processEvents()
            anchor, peer, source = docks
            for dock in docks:
                self.assertEqual(
                    dock.style().styleHint(
                        QStyle.StyleHint.SH_Widget_Animation_Duration, None, dock
                    ),
                    0,
                )
            for relation in ("below", "right", "above", "left") * 5:
                workspace.place_panel(anchor, peer, "tab")
                workspace.place_panel(anchor, source, "tab")
                self.app.processEvents()
                workspace.place_panel(anchor, source, relation)
                self.app.processEvents()
                self.assertIn(peer, workspace.tabifiedDockWidgets(anchor))
                self.assertNotIn(source, workspace.tabifiedDockWidgets(anchor))
                if relation == "below":
                    self.assertGreater(source.y(), anchor.y())
                elif relation == "above":
                    self.assertLess(source.y(), anchor.y())
                elif relation == "right":
                    self.assertGreater(source.x(), anchor.x())
                else:
                    self.assertLess(source.x(), anchor.x())
            workspace.close()

    def test_hover_between_stacked_panels_does_not_change_sizes_or_size_hints(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            workspace.setStyleSheet(build_stylesheet(DARK))
            docks = [
                workspace.add_panel(
                    key, key, QLabel(key), Qt.DockWidgetArea.LeftDockWidgetArea
                )
                for key in ("flight", "telemetry", "commands")
            ]
            for first, second in zip(docks, docks[1:]):
                workspace.splitDockWidget(first, second, Qt.Orientation.Vertical)
            workspace.add_panel(
                "map", "map", QLabel("map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            workspace.show()
            for size, theme in (
                ((740, 540), DARK),
                ((680, 480), LIGHT),
                ((780, 560), DARK),
            ):
                workspace.setStyleSheet(build_stylesheet(theme))
                workspace.resize(*size)
                # Native window-manager configure events arrive asynchronously.
                if self.app.platformName() != "offscreen":
                    QTest.qWait(30)
                else:
                    self.app.processEvents()
                workspace.enable_panel_size_restore(capture_current=True)
                before = [(dock.geometry(), dock.minimumSizeHint()) for dock in docks]
                remembered = dict(workspace._panel_sizes)
                for dock in docks * 2:
                    controls = workspace._hover_controls[workspace._panel_keys[dock]]
                    self.assertIs(controls.parentWidget(), dock)
                    QApplication.sendEvent(dock, QEvent(QEvent.Type.Enter))
                    self.app.processEvents()
                    controls.animation.setCurrentTime(130)
                    QTest.qWait(20)
                    expected = QPoint((dock.width() - controls.width()) // 2, 4)
                    self.assertEqual(controls.pos(), expected)
                    idle = QMouseEvent(
                        QEvent.Type.MouseMove,
                        QPointF(80, 60),
                        QPointF(dock.mapToGlobal(QPoint(80, 60))),
                        Qt.MouseButton.NoButton,
                        Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                    )
                    QApplication.sendEvent(dock, idle)
                    controls.reveal(False)
                    controls.animation.setCurrentTime(130)
                    self.app.processEvents()
                    self.assertFalse(controls.isHidden())
                    self.assertEqual(controls.effect.opacity(), 0)
                    self.assertTrue(
                        controls.testAttribute(
                            Qt.WidgetAttribute.WA_TransparentForMouseEvents
                        )
                    )
                    self.assertEqual(
                        [
                            (panel.geometry(), panel.minimumSizeHint())
                            for panel in docks
                        ],
                        before,
                    )
                    self.assertEqual(workspace._panel_sizes, remembered)
            workspace.close()

    def test_idle_mouse_move_discards_stale_panel_resize_and_drag(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            dock = workspace.add_panel(
                "map", "map", QLabel("map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            workspace.show()
            self.app.processEvents()
            workspace._panel_resize = object()
            workspace._drag_start = (dock, QPoint(1, 1))
            before = dock.geometry()
            idle = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(60, 60),
                QPointF(dock.mapToGlobal(QPoint(60, 60))),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            workspace.eventFilter(workspace, idle)
            self.assertIsNone(workspace._panel_resize)
            self.assertIsNone(workspace._drag_start)
            self.assertEqual(dock.geometry(), before)
            workspace.close()

    def test_restart_preserves_nested_splits_without_reapplying_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout.json"

            def build():
                workspace = PanelWorkspace(path)
                docks = [
                    workspace.add_panel(
                        key, key, QLabel(key), Qt.DockWidgetArea.RightDockWidgetArea
                    )
                    for key in ("map", "camera", "logs")
                ]
                workspace.splitDockWidget(docks[0], docks[1], Qt.Orientation.Horizontal)
                workspace.splitDockWidget(docks[0], docks[2], Qt.Orientation.Vertical)
                return workspace, docks

            original, docks = build()
            original.resize(740, 540)
            original.show()
            self.app.processEvents()
            original.resizeDocks(docks[:2], [440, 290], Qt.Orientation.Horizontal)
            original.resizeDocks(
                [docks[0], docks[2]], [310, 220], Qt.Orientation.Vertical
            )
            self.app.processEvents()
            geometry = [dock.geometry() for dock in docks]
            self.assertTrue(original.save_layout())
            original.close()
            restored, restored_docks = build()
            restored.resize(600, 400)
            with patch.object(restored, "_schedule_panel_size_restore") as resize:
                self.assertTrue(restored.restore_layout())
                restored.show()
                self.app.processEvents()
                resize.assert_not_called()
            self.assertEqual(restored.size(), original.size())
            for expected, dock in zip(geometry, restored_docks):
                self.assertLessEqual(abs(expected.width() - dock.width()), 3)
                self.assertLessEqual(abs(expected.height() - dock.height()), 3)
            restored.close()

    def test_tab_selection_does_not_restore_panel_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            first = workspace.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            second = workspace.add_panel(
                "camera",
                "Camera",
                QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            workspace.show()
            self.app.processEvents()
            workspace.enable_panel_size_restore(capture_current=True)
            workspace.place_panel(first, second, "tab")
            self.app.processEvents()
            with patch.object(workspace, "_schedule_panel_size_restore") as restore:
                first.raise_()
                self.app.processEvents()
                second.raise_()
                self.app.processEvents()
                restore.assert_not_called()
            workspace.close()

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["yki-workspace-tests"])

    def test_hover_controls_drag_and_menu_without_edit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            dock = workspace.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            workspace.resize(700, 500)
            workspace.show()
            self.app.processEvents()
            controls = workspace._hover_controls["map"]
            QApplication.sendEvent(dock, QEvent(QEvent.Type.Enter))
            QTest.qWait(160)
            self.assertTrue(controls.isVisible())
            self.assertEqual(controls.effect.opacity(), 1)
            self.assertFalse(workspace.edit_mode)
            self.assertEqual(controls.x(), (dock.width() - controls.width()) // 2)
            with patch.object(workspace, "_show_panel_menu") as menu:
                QTest.mouseClick(controls.arrow, Qt.MouseButton.LeftButton)
                self.assertEqual(menu.call_args.args[0], dock)
            with patch.object(workspace, "_start_panel_drag") as drag:
                QTest.mousePress(controls.grip, Qt.MouseButton.LeftButton)
                event = QMouseEvent(
                    QEvent.Type.MouseMove,
                    QPointF(35, 10),
                    QPointF(controls.grip.mapToGlobal(QPoint(35, 10))),
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                QApplication.sendEvent(controls.grip, event)
                drag.assert_called_once_with(dock)
            QApplication.sendEvent(dock, QEvent(QEvent.Type.Leave))
            QTest.qWait(160)
            self.assertEqual(controls.effect.opacity(), 0)
            self.assertTrue(
                controls.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            )
            workspace.close()

    def test_left_column_spans_beside_bottom_panel_after_layout_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout.json"
            workspace = PanelWorkspace(path)
            left = workspace.add_panel(
                "telemetry",
                "Telemetry",
                QLabel("Telemetry"),
                Qt.DockWidgetArea.LeftDockWidgetArea,
            )
            workspace.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            bottom = workspace.add_panel(
                "logs", "Logs", QLabel("Logs"), Qt.DockWidgetArea.BottomDockWidgetArea
            )
            workspace.resize(1000, 700)
            workspace.show()
            self.app.processEvents()
            self.assertGreaterEqual(
                left.geometry().bottom(), bottom.geometry().bottom()
            )
            self.assertGreater(bottom.x(), left.geometry().right())

            workspace.setCorner(
                Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.BottomDockWidgetArea
            )
            self.assertTrue(workspace.save_layout())
            self.assertTrue(workspace.restore_layout())
            self.app.processEvents()
            self.assertEqual(
                workspace.corner(Qt.Corner.BottomLeftCorner),
                Qt.DockWidgetArea.LeftDockWidgetArea,
            )
            self.assertGreaterEqual(
                left.geometry().bottom(), bottom.geometry().bottom()
            )
            self.assertGreater(bottom.x(), left.geometry().right())
            workspace.close()

    def test_bottom_panel_fills_workspace_when_right_column_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            side = [
                workspace.add_panel(
                    key, key, QLabel(key), Qt.DockWidgetArea.LeftDockWidgetArea
                )
                for key in ("flight", "telemetry", "commands")
            ]
            workspace.splitDockWidget(side[0], side[1], Qt.Orientation.Vertical)
            workspace.splitDockWidget(side[1], side[2], Qt.Orientation.Vertical)
            map_dock = workspace.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            camera = workspace.add_panel(
                "camera",
                "Camera",
                QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            workspace.splitDockWidget(map_dock, camera, Qt.Orientation.Horizontal)
            bottom = workspace.add_panel(
                "logs", "Logs", QLabel("Logs"), Qt.DockWidgetArea.BottomDockWidgetArea
            )
            workspace.resize(1200, 800)
            workspace.show()
            self.app.processEvents()
            self.assertEqual(
                workspace.corner(Qt.Corner.BottomLeftCorner),
                Qt.DockWidgetArea.LeftDockWidgetArea,
            )

            map_dock.hide()
            camera.hide()
            self.app.processEvents()

            self.assertEqual(
                workspace.corner(Qt.Corner.BottomLeftCorner),
                Qt.DockWidgetArea.BottomDockWidgetArea,
            )
            self.assertEqual(bottom.geometry().left(), 0)
            self.assertGreaterEqual(bottom.width(), workspace.width() - 2)
            self.assertLess(
                max(dock.geometry().bottom() for dock in side), bottom.geometry().top()
            )

            map_dock.show()
            self.app.processEvents()
            self.assertEqual(
                workspace.corner(Qt.Corner.BottomLeftCorner),
                Qt.DockWidgetArea.LeftDockWidgetArea,
            )
            workspace.close()

    def test_separator_resizes_only_neighbors_and_stops_at_minimum(self) -> None:
        for orientation in (Qt.Orientation.Horizontal, Qt.Orientation.Vertical):
            with (
                self.subTest(orientation=orientation),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = PanelWorkspace(Path(directory) / "layout.json")
                docks = [
                    workspace.add_panel(
                        key, key, QLabel(key), Qt.DockWidgetArea.LeftDockWidgetArea
                    )
                    for key in ("first", "second", "third")
                ]
                first, second, third = docks
                horizontal = orientation == Qt.Orientation.Horizontal
                relation = "right" if horizontal else "below"
                workspace.place_panel(first, second, relation)
                workspace.place_panel(second, third, relation)
                if horizontal:
                    second.setMinimumWidth(180)
                else:
                    second.setMinimumHeight(180)
                workspace.resize(1000, 800)
                workspace.show()
                self.app.processEvents()
                workspace.resizeDocks(docks, [250, 230, 300], orientation)
                self.app.processEvents()
                unchanged = third.geometry()
                total = (
                    first.width() + second.width()
                    if horizontal
                    else first.height() + second.height()
                )
                point = (
                    QPoint(first.geometry().right() + 3, first.height() // 2)
                    if horizontal
                    else QPoint(first.width() // 2, first.geometry().bottom() + 3)
                )
                end = point + (QPoint(150, 0) if horizontal else QPoint(0, 150))
                for event_type, position, button, buttons in (
                    (
                        QEvent.Type.MouseButtonPress,
                        point,
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                    ),
                    (
                        QEvent.Type.MouseMove,
                        end,
                        Qt.MouseButton.NoButton,
                        Qt.MouseButton.LeftButton,
                    ),
                    (
                        QEvent.Type.MouseButtonRelease,
                        end,
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton,
                    ),
                ):
                    self.app.sendEvent(
                        workspace,
                        QMouseEvent(
                            event_type,
                            QPointF(position),
                            QPointF(workspace.mapToGlobal(position)),
                            button,
                            buttons,
                            Qt.KeyboardModifier.NoModifier,
                        ),
                    )
                    self.app.processEvents()
                self.assertEqual(third.geometry(), unchanged)
                self.assertEqual(second.width() if horizontal else second.height(), 180)
                self.assertEqual(
                    first.width() + second.width()
                    if horizontal
                    else first.height() + second.height(),
                    total,
                )
                workspace.close()

    def test_stacked_panels_fill_shared_column_without_spacer_docks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            docks = [
                workspace.add_panel(
                    key, key, QLabel(key), Qt.DockWidgetArea.LeftDockWidgetArea
                )
                for key in ("flight", "telemetry", "commands")
            ]
            workspace.place_panel(docks[0], docks[1], "below")
            workspace.place_panel(docks[1], docks[2], "below")
            workspace.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            workspace.enable_independent_widths(("flight", "telemetry", "commands"))
            workspace.resize(1200, 800)
            workspace.show()
            self.app.processEvents()
            self.assertFalse(workspace._width_spacers)
            self.assertEqual(len({dock.width() for dock in docks}), 1)
            for dock in docks:
                self.assertGreater(dock.maximumWidth(), dock.minimumWidth())
            docks[1].hide()
            self.app.processEvents()
            docks[1].show()
            self.app.processEvents()
            self.assertEqual(len({dock.width() for dock in docks}), 1)
            workspace.close()

    def test_dock_position_visibility_and_size_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "layout.json"
            first = PanelWorkspace(state_path)
            first.resize(1200, 800)
            map_dock = first.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            camera = first.add_panel(
                "camera.visual",
                "Camera",
                QLabel("Video"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            logs = first.add_panel(
                "logs", "Logs", QLabel("Events"), Qt.DockWidgetArea.BottomDockWidgetArea
            )
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
            restored_camera = second.add_panel(
                "camera.visual",
                "Camera",
                QLabel("Video"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            restored_logs = second.add_panel(
                "logs", "Logs", QLabel("Events"), Qt.DockWidgetArea.BottomDockWidgetArea
            )
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
            first.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            docks = [
                first.add_panel(
                    key, key, QLabel(key), Qt.DockWidgetArea.LeftDockWidgetArea
                )
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
            second.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.RightDockWidgetArea
            )
            restored = [
                second.add_panel(
                    key, key, QLabel(key), Qt.DockWidgetArea.LeftDockWidgetArea
                )
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
                "flight",
                "Flight",
                QLabel("Flight"),
                Qt.DockWidgetArea.LeftDockWidgetArea,
            )
            second = workspace.add_panel(
                "telemetry",
                "Telemetry",
                QLabel("Telemetry"),
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
                "camera.visual",
                "Camera",
                QLabel("Camera"),
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

    def test_all_panels_share_relative_placement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            map_dock = workspace.add_panel(
                "map",
                "Map",
                QLabel("Map"),
                Qt.DockWidgetArea.RightDockWidgetArea,
                floatable=False,
            )
            camera = workspace.add_panel(
                "camera",
                "Camera",
                QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
                floatable=False,
            )
            logs = workspace.add_panel(
                "logs",
                "Logs",
                QLabel("Logs"),
                Qt.DockWidgetArea.BottomDockWidgetArea,
                floatable=False,
            )
            workspace.resize(1200, 700)
            workspace.show()
            self.app.processEvents()

            self.assertTrue(workspace.place_panel(map_dock, camera, "right"))
            self.assertGreater(camera.x(), map_dock.x())
            self.assertTrue(workspace.place_panel(map_dock, logs, "tab"))
            self.assertIn(logs, workspace.tabifiedDockWidgets(map_dock))
            self.assertTrue(
                map_dock.allowedAreas() & Qt.DockWidgetArea.BottomDockWidgetArea
            )
            self.assertFalse(
                camera.features() & camera.DockWidgetFeature.DockWidgetFloatable
            )
            workspace.close()

    def test_edit_mode_title_drag_and_placement_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            visual_areas = (
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
                | Qt.DockWidgetArea.TopDockWidgetArea
            )
            map_dock = workspace.add_panel(
                "map",
                "Map",
                QLabel("Map"),
                Qt.DockWidgetArea.RightDockWidgetArea,
                allowed_areas=visual_areas,
                floatable=False,
            )
            camera = workspace.add_panel(
                "camera",
                "Camera",
                QLabel("Camera"),
                Qt.DockWidgetArea.LeftDockWidgetArea,
                allowed_areas=visual_areas,
                floatable=False,
            )
            workspace.resize(1200, 700)
            workspace.show()
            workspace.set_edit_mode(True)
            self.app.processEvents()
            self.assertIsNone(camera.titleBarWidget())

            start = QPoint(45, 10)
            start_global = camera.mapToGlobal(start)
            QTest.mousePress(camera, Qt.MouseButton.LeftButton, pos=start)
            self.assertIsNotNone(workspace._drag_start)
            QTest.mouseRelease(camera, Qt.MouseButton.LeftButton, pos=start)
            self.assertIsNone(workspace._drag_start)
            content_point = QPoint(45, camera.height() // 2)
            content_press = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(content_point),
                QPointF(camera.mapToGlobal(content_point)),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            self.assertFalse(workspace.eventFilter(camera, content_press))
            map_center = map_dock.mapToGlobal(map_dock.rect().center())
            started = []
            workspace._start_panel_drag = lambda dock: started.append(dock)
            for event_type, local, global_pos, button, buttons in (
                (
                    QEvent.Type.MouseButtonPress,
                    start,
                    start_global,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                ),
                (
                    QEvent.Type.MouseMove,
                    camera.mapFromGlobal(map_center),
                    map_center,
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.LeftButton,
                ),
            ):
                event = QMouseEvent(
                    event_type,
                    QPointF(local),
                    QPointF(global_pos),
                    button,
                    buttons,
                    Qt.KeyboardModifier.NoModifier,
                )
                self.assertTrue(workspace.eventFilter(camera, event))
            self.assertEqual(started, [camera])
            expected = {
                "left": QPoint(2, map_dock.height() // 2),
                "right": QPoint(map_dock.width() - 3, map_dock.height() // 2),
                "above": QPoint(map_dock.width() // 2, 2),
                "below": QPoint(map_dock.width() // 2, map_dock.height() - 3),
                "tab": map_dock.rect().center(),
            }
            for relation, point in expected.items():
                target = workspace._drop_target(
                    map_dock.mapToGlobal(point), source=camera
                )
                self.assertIsNotNone(target)
                self.assertEqual(target[1], relation)
                if relation == "tab":
                    self.assertIs(target[0], map_dock)
            workspace._drag_source = camera
            overlay = workspace._drop_overlay
            overlay.setGeometry(workspace.rect())
            overlay.show()
            mime = QMimeData()
            mime.setData("application/x-yki-panel-drag", b"camera")
            event = QDropEvent(
                QPointF(overlay.mapFromGlobal(map_center)),
                Qt.DropAction.MoveAction,
                mime,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            overlay.dropEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertNotIn(camera, workspace.tabifiedDockWidgets(map_dock))
            overlay.hide()
            workspace._drag_source = None
            workspace._apply_drop(*workspace._pending_drop)
            self.app.processEvents()
            self.assertIn(camera, workspace.tabifiedDockWidgets(map_dock))
            self.assertEqual(
                workspace.tabPosition(Qt.DockWidgetArea.RightDockWidgetArea),
                QTabWidget.TabPosition.North,
            )

            self.assertTrue(
                workspace.move_panel_to_area(
                    camera, Qt.DockWidgetArea.LeftDockWidgetArea
                )
            )
            self.app.processEvents()
            right_edge = workspace.mapToGlobal(
                QPoint(workspace.width() - 2, workspace.height() // 2)
            )
            self.assertTrue(workspace.drop_panel_at(camera, right_edge))
            self.app.processEvents()
            self.assertGreater(camera.geometry().x(), map_dock.geometry().x())
            workspace.close()

    def test_legacy_central_map_layout_migrates_without_losing_camera(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "layout.json"
            old = PanelWorkspace(state_path)
            old.STATE_VERSION = 1
            old.resize(1200, 700)
            old.setCentralWidget(QLabel("Map"))
            old.add_panel(
                "camera.visual",
                "Camera",
                QLabel("Camera"),
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
                "camera.visual",
                "Camera",
                QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            workspace.splitDockWidget(map_dock, camera, Qt.Orientation.Horizontal)
            workspace.show()
            self.app.processEvents()

            self.assertTrue(workspace.restore_layout())
            self.app.processEvents()
            self.assertEqual(
                workspace.dockWidgetArea(map_dock),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            self.assertEqual(
                workspace.dockWidgetArea(camera), Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.assertGreater(map_dock.width(), 100)
            self.assertGreater(camera.width(), 100)
            self.assertGreater(camera.x(), map_dock.x())
            workspace.close()

    def test_v2_layout_is_normalized_to_panel_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "layout.json"
            old = PanelWorkspace(state_path)
            old.STATE_VERSION = 2
            old.resize(1000, 600)
            old_map = old.add_panel(
                "map", "Map", QLabel("Map"), Qt.DockWidgetArea.BottomDockWidgetArea
            )
            old_camera = old.add_panel(
                "camera",
                "Camera",
                QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            old.show()
            self.app.processEvents()
            old.save_layout()
            old.close()

            workspace = PanelWorkspace(state_path)
            workspace.resize(1000, 600)
            map_dock = workspace.add_panel(
                "map",
                "Map",
                QLabel("Map"),
                Qt.DockWidgetArea.RightDockWidgetArea,
                allowed_areas=(
                    Qt.DockWidgetArea.LeftDockWidgetArea
                    | Qt.DockWidgetArea.RightDockWidgetArea
                    | Qt.DockWidgetArea.TopDockWidgetArea
                ),
            )
            camera = workspace.add_panel(
                "camera",
                "Camera",
                QLabel("Camera"),
                Qt.DockWidgetArea.RightDockWidgetArea,
                allowed_areas=(
                    Qt.DockWidgetArea.LeftDockWidgetArea
                    | Qt.DockWidgetArea.RightDockWidgetArea
                    | Qt.DockWidgetArea.TopDockWidgetArea
                ),
            )
            workspace.show()
            self.app.processEvents()

            self.assertTrue(workspace.restore_layout())
            self.assertEqual(
                workspace.dockWidgetArea(map_dock),
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            self.assertEqual(
                workspace.dockWidgetArea(camera), Qt.DockWidgetArea.RightDockWidgetArea
            )
            workspace.close()

    def test_panel_visibility_stays_available_outside_edit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            dock = workspace.add_panel(
                "logs",
                "Logs",
                QLabel("Events"),
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
            self.assertEqual(dock.features(), dock.DockWidgetFeature.DockWidgetClosable)
            workspace.set_edit_mode(False)
            self.assertIsNotNone(dock.titleBarWidget())
            workspace.close()


if __name__ == "__main__":
    unittest.main()
