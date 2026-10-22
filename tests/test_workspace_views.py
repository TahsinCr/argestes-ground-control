from __future__ import annotations
import os
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
from pathlib import Path
import unittest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
from src.ui.widgets.panel_workspace import PanelWorkspace
from src.ui.workspace_views import WorkspaceViews
from src.ui.widgets.panel_selection import PanelSelection
from src.ui.widgets.workspace_dialog import WorkspaceItemDialog


class WorkspaceViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "layout.json"
        self.workspace = PanelWorkspace(self.path)
        self.a = self.workspace.add_panel(
            "a", "First", QLabel("A"), Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.b = self.workspace.add_panel(
            "b", "Second", QLabel("B"), Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.c = self.workspace.add_panel(
            "c", "Third", QLabel("C"), Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.workspace.resize(1000, 700)
        self.workspace.show()
        self.app.processEvents()
        self.controller = WorkspaceViews(self.workspace)
        for dock in self.workspace.panels.values():
            self.controller.track_panel(dock)
        self.controller.initialize()
        self.app.processEvents()

    def tearDown(self):
        self.controller._save_timer.stop()
        self.workspace.close()
        self.workspace.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def test_blank_view_and_original_round_trip_preserve_geometry(self):
        original = self.controller.active_id
        before = {key: dock.geometry() for key, dock in self.workspace.panels.items()}
        empty = self.controller.create("Blank", "SP_DesktopIcon")
        self.app.processEvents()
        self.assertTrue(all(dock.isHidden() for dock in self.workspace.panels.values()))
        self.b.show()
        self.app.processEvents()
        self.controller.switch(original)
        QTest.qWait(30)
        self.assertEqual(
            {key: dock.geometry() for key, dock in self.workspace.panels.items()},
            before,
        )
        self.controller.switch(empty)
        self.app.processEvents()
        self.assertTrue(self.a.isHidden())
        self.assertFalse(self.b.isHidden())
        self.assertTrue(self.c.isHidden())

    def test_categories_belong_to_one_view_and_keep_other_panels_open(self):
        original = self.controller.active_id
        self.controller.add_category("Tools", "SP_ComputerIcon", ["a", "b", "a"])
        group = self.controller.active["categories"][0]
        self.assertEqual(group["panels"], ["a", "b"])
        before = self.a.geometry(), self.b.geometry(), self.c.geometry()
        self.controller.toggle_category(group["id"])
        self.app.processEvents()
        self.assertTrue(self.a.isHidden() and self.b.isHidden())
        self.assertFalse(self.c.isHidden())
        self.controller.toggle_category(group["id"])
        self.app.processEvents()
        self.assertEqual(
            (self.a.geometry(), self.b.geometry(), self.c.geometry()), before
        )
        self.controller.create("Other", "SP_DesktopIcon")
        self.assertFalse(self.controller.active["categories"])
        self.controller.switch(original)
        self.assertEqual(len(self.controller.active["categories"]), 1)

    def test_new_view_has_fresh_topology_and_each_first_panel_fills_workspace(self):
        original = self.controller.active_id
        self.workspace.place_panel(self.a, self.b, "tab")
        self.app.processEvents()
        self.workspace.set_panel_size_locked(self.a, True)
        self.c.setFloating(True)
        self.app.processEvents()

        empty = self.controller.create("Fresh", "layout")
        self.app.processEvents()
        self.assertEqual(self.controller.active["layout"]["locked_sizes"], {})
        for dock in (self.a, self.b, self.c):
            with self.subTest(panel=dock.objectName()):
                self.assertFalse(dock.isFloating())
                dock.toggleViewAction().trigger()
                QTest.qWait(20)
                self.assertEqual(dock.geometry(), self.workspace.rect())
                self.assertFalse(self.workspace.tabifiedDockWidgets(dock))
                dock.toggleViewAction().trigger()
                self.app.processEvents()

        self.b.toggleViewAction().trigger()
        QTest.qWait(20)
        self.controller.switch(original)
        QTest.qWait(20)
        self.assertIn(self.b, self.workspace.tabifiedDockWidgets(self.a))
        self.assertIn("a", self.workspace._locked_sizes)
        self.assertTrue(self.c.isFloating())
        self.controller.switch(empty)
        QTest.qWait(20)
        self.assertEqual(self.b.geometry(), self.workspace.rect())
        self.assertTrue(self.a.isHidden() and self.c.isHidden())

    def test_open_detached_panel_is_recovered_without_breaking_other_tabs(self):
        self.workspace.place_panel(self.b, self.c, "tab")
        self.app.processEvents()
        self.workspace.removeDockWidget(self.a)
        self.a.show()
        QTest.qWait(20)
        self.assertEqual(
            self.workspace.dockWidgetArea(self.a),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.assertTrue(self.workspace.rect().intersects(self.a.geometry()))
        self.assertIn(self.c, self.workspace.tabifiedDockWidgets(self.b))

    def test_partial_category_opens_missing_member_without_closing_others(self):
        self.controller.add_category("Tools", "SP_ComputerIcon", ["a", "b"])
        group = self.controller.active["categories"][0]
        self.b.hide()
        self.controller.toggle_category(group["id"])
        self.assertFalse(self.a.isHidden() or self.b.isHidden() or self.c.isHidden())

    def test_group_snapshot_never_reverts_subsequent_layout_edits(self):
        self.controller.add_category("Tools", "SP_ComputerIcon", ["a"])
        group = self.controller.active["categories"][0]
        self.controller.toggle_category(group["id"])
        self.workspace.place_panel(self.b, self.c, "tab")
        self.assertNotIn("layout", group)
        self.controller.toggle_category(group["id"])
        self.assertIn(self.c, self.workspace.tabifiedDockWidgets(self.b))

    def test_saved_views_and_empty_active_view_survive_restart(self):
        original = self.controller.active_id
        self.controller.add_category("A", "SP_ComputerIcon", ["a"])
        empty = self.controller.create("Blank", "SP_DesktopIcon")
        self.controller.save()
        restored = WorkspaceViews(self.workspace)
        restored.initialize()
        self.assertEqual(restored.active_id, empty)
        self.assertTrue(all(dock.isHidden() for dock in self.workspace.panels.values()))
        restored.switch(original)
        self.assertEqual(restored.active["categories"][0]["name"], "A")
        restored._save_timer.stop()

    def test_view_switch_does_not_restore_window_size(self):
        original = self.controller.active_id
        self.controller.create("Other", "SP_DesktopIcon")
        self.workspace.resize(1100, 850)
        self.app.processEvents()
        self.controller.switch(original)
        self.assertEqual(self.workspace.width(), 1100)
        self.assertEqual(self.workspace.height(), 850)

    def test_invalid_names_and_empty_groups_are_rejected(self):
        with self.assertRaises(ValueError):
            self.controller.create(" ", "SP_DesktopIcon")
        with self.assertRaises(ValueError):
            self.controller.add_category("Tools", "SP_ComputerIcon", [])
        self.assertEqual(len(self.controller.views), 1)

    def test_last_view_cannot_be_removed(self):
        self.controller.remove(self.controller.active_id)
        self.assertEqual(len(self.controller.views), 1)

    def test_pending_resize_does_not_leak_across_views(self):
        original = self.controller.active_id
        before = self.a.geometry(), self.b.geometry(), self.c.geometry()
        self.controller.create("Other", "SP_DesktopIcon")
        self.a.show()
        self.controller.switch(original)
        QTest.qWait(40)
        self.assertEqual(
            (self.a.geometry(), self.b.geometry(), self.c.geometry()), before
        )

    def test_removed_panel_does_not_break_category(self):
        self.controller.add_category("Tools", "SP_ComputerIcon", ["a", "b"])
        group = self.controller.active["categories"][0]
        self.workspace.remove_panel("a")
        self.controller.toggle_category(group["id"])
        self.assertTrue(self.b.isHidden())
        self.controller.toggle_category(group["id"])
        self.assertFalse(self.b.isHidden())

    def test_invalid_target_snapshot_rolls_back_active_view(self):
        original = self.controller.active_id
        before = self.a.geometry(), self.b.geometry(), self.c.geometry()
        other = self.controller.create("Other", "SP_DesktopIcon")
        self.controller.switch(original)
        target = next(view for view in self.controller.views if view["id"] == other)
        target["layout"]["state"] = "not-a-qt-state"
        self.controller.switch(other)
        self.assertEqual(self.controller.active_id, original)
        self.assertEqual(
            (self.a.geometry(), self.b.geometry(), self.c.geometry()), before
        )

    def test_loaded_categories_sanitize_invalid_and_duplicate_members(self):
        self.controller.save()
        payload = json.loads(self.path.read_text())
        view = payload["views"]["items"][0]
        view["categories"] = [
            {
                "id": "valid",
                "name": "Group",
                "icon": None,
                "panels": ["a", "a", {}, 3],
                "layout": "invalid",
            },
            {"id": "valid", "name": "Duplicate", "panels": ["b"]},
            {"id": "bad", "name": "Invalid", "panels": None},
        ]
        # write_text is a test fixture, not a workspace mutation.
        self.path.write_text(json.dumps(payload))
        restored = WorkspaceViews(self.workspace)
        restored.initialize()
        groups = restored.active["categories"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["panels"], ["a"])
        self.assertEqual(groups[0]["icon"], "SP_ComputerIcon")
        self.assertNotIn("layout", groups[0])
        restored.toggle_category("valid")
        self.assertTrue(self.a.isHidden())
        restored._save_timer.stop()

    def test_selection_intercepts_clicks_and_cancels_without_commands(self):
        command = QPushButton("Command")
        dock = self.workspace.add_panel(
            "command", "Command", command, Qt.DockWidgetArea.RightDockWidgetArea
        )
        calls = []
        command.clicked.connect(lambda: calls.append(True))
        self.app.processEvents()
        selection = PanelSelection(self.workspace, "en")
        surface = next(
            surface for surface in selection.surfaces if surface.parentWidget() is dock
        )
        QTest.mouseClick(surface, Qt.MouseButton.LeftButton)
        self.assertIn("command", selection.selected)
        self.assertTrue(selection.add.isEnabled())
        self.assertFalse(calls)
        finished = []
        selection.completed.connect(finished.append)
        selection.add.click()
        self.assertEqual(finished, [["command"]])
        selection.dispose()
        self.app.processEvents()
        self.assertFalse(command.isHidden())

    def test_shared_picker_has_names_and_standard_icon_catalog(self):
        dialog = WorkspaceItemDialog("en", category=True)
        self.assertGreater(dialog.icons.count(), 50)
        dialog.name_input.setText("  Mission  ")
        dialog.filter_icons("Desktop")
        self.assertTrue(
            any(
                not dialog.icons.item(i).isHidden() for i in range(dialog.icons.count())
            )
        )
        self.assertEqual(dialog.values()[0], "Mission")
        dialog.deleteLater()

    def test_workspace_icons_are_monochrome_and_follow_theme(self):
        from PyQt6.QtGui import QPalette
        from src.ui.widgets.workspace_icons import ICON_NAMES, workspace_icon
        from src.ui.themes.manager import apply_theme

        for theme in ("dark", "light"):
            apply_theme(self.app, theme)
            for name in (*ICON_NAMES, "SP_DesktopIcon", "SP_MessageBoxWarning"):
                icon = workspace_icon(name)
                pixmap = icon.pixmap(24, 24)
                self.assertFalse(pixmap.isNull(), name)
                image = pixmap.toImage()
                colors = [
                    image.pixelColor(x, y)
                    for x in range(image.width())
                    for y in range(image.height())
                    if image.pixelColor(x, y).alpha() > 150
                ]
                self.assertTrue(colors, (theme, name))
                expected = self.app.palette().color(QPalette.ColorRole.ButtonText)
                for color in colors:
                    self.assertLessEqual(
                        max(
                            abs(color.red() - expected.red()),
                            abs(color.green() - expected.green()),
                            abs(color.blue() - expected.blue()),
                        ),
                        2,
                        (theme, name),
                    )
        apply_theme(self.app, "dark")

    def test_activity_refresh_reuses_buttons_and_layout_order(self):
        from src.ui.widgets.activity_bar import ActivityBar
        from src.ui.themes.manager import apply_theme

        bar = ActivityBar("en")
        bar.refresh(self.controller)
        button = bar.view_buttons[self.controller.active_id]
        for _ in range(10):
            bar.refresh(self.controller)
        self.assertIs(bar.view_buttons[self.controller.active_id], button)
        self.assertEqual(bar._top_layout.count(), 1)
        self.assertEqual(bar._top_layout.indexOf(button), 0)
        self.assertIsNone(bar.add_button.text() or None)
        previous = button.icon().cacheKey()
        apply_theme(self.app, "light")
        self.app.processEvents()
        self.assertNotEqual(button.icon().cacheKey(), previous)
        self.assertIs(bar.view_buttons[self.controller.active_id], button)
        apply_theme(self.app, "dark")
        bar.deleteLater()

    def test_active_view_remains_reachable_in_a_long_navigation_list(self):
        from src.ui.widgets.activity_bar import ActivityBar

        bar = ActivityBar("en")
        bar.resize(48, 500)
        bar.show()
        for index in range(10):
            self.controller.create(f"View {index}", "layout")
        bar.refresh(self.controller)
        QTest.qWait(30)
        self.assertGreater(bar._top.verticalScrollBar().value(), 0)
        self.assertTrue(bar.view_buttons[self.controller.active_id].isChecked())
        bar.close()
        bar.deleteLater()


if __name__ == "__main__":
    unittest.main()
