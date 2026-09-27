from __future__ import annotations

import base64
import json
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtWidgets import QDockWidget, QMainWindow, QMenu, QTabWidget, QWidget


class PanelWorkspace(QMainWindow):
    """Dockable shell around the unchanged content widgets."""

    STATE_VERSION = 1

    def __init__(
        self,
        state_path: Path,
        parent: QWidget | None = None,
        *,
        language: str = "tr",
    ) -> None:
        super().__init__(parent, Qt.WindowType.Widget)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.state_path = state_path
        self.language = language
        self.setObjectName("PanelWorkspace")
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.GroupedDragging
            | QMainWindow.DockOption.AnimatedDocks
        )
        self.setTabPosition(
            Qt.DockWidgetArea.AllDockWidgetAreas,
            QTabWidget.TabPosition.South,
        )
        self.panels: dict[str, QDockWidget] = {}
        self.edit_mode = False
        self._quiet_titles: dict[str, QWidget] = {}

    def add_panel(
        self,
        key: str,
        title: str,
        widget: QWidget,
        area: Qt.DockWidgetArea,
    ) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"panel.{key}")
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        self.panels[key] = dock
        quiet_title = QWidget(dock)
        quiet_title.setFixedHeight(0)
        self._quiet_titles[key] = quiet_title
        self._apply_panel_mode(key)
        dock.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        dock.customContextMenuRequested.connect(
            lambda point, panel=dock: self._show_panel_menu(panel, point)
        )
        return dock

    def set_edit_mode(self, enabled: bool) -> None:
        self.edit_mode = enabled
        for key in self.panels:
            self._apply_panel_mode(key)

    def _apply_panel_mode(self, key: str) -> None:
        dock = self.panels[key]
        if self.edit_mode:
            dock.setTitleBarWidget(None)
            dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetClosable
                | QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )
        else:
            dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
            dock.setTitleBarWidget(self._quiet_titles[key])

    def _show_panel_menu(self, dock: QDockWidget, point) -> None:
        if not self.edit_mode:
            return
        english = self.language == "en"
        menu = QMenu(dock)
        move = menu.addMenu("Move Panel" if english else "Paneli taşı")
        for title, area in (
            ("Left" if english else "Sola", Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Right" if english else "Sağa", Qt.DockWidgetArea.RightDockWidgetArea),
            ("Bottom" if english else "Aşağıya", Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            move.addAction(
                title,
                lambda selected_area=area: self.addDockWidget(selected_area, dock),
            )
        menu.addAction(
            ("Float" if english else "Ayrı pencere")
            if not dock.isFloating() else
            ("Dock" if english else "Pencereye sabitle"),
            lambda: dock.setFloating(not dock.isFloating()),
        )
        menu.addSeparator()
        menu.addAction("Hide Panel" if english else "Paneli gizle", dock.hide)
        menu.exec(dock.mapToGlobal(point))

    def remove_panel(self, key: str) -> QWidget | None:
        dock = self.panels.pop(key, None)
        if dock is None:
            return None
        self._quiet_titles.pop(key, None)
        widget = dock.widget()
        if widget is not None:
            dock.setWidget(None)
        self.removeDockWidget(dock)
        dock.deleteLater()
        return widget

    def save_layout(self) -> bool:
        temporary = self.state_path.with_suffix(".tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            state = base64.b64encode(bytes(self.saveState(self.STATE_VERSION))).decode("ascii")
            payload = {"version": self.STATE_VERSION, "state": state}
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(self.state_path)
            return True
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def restore_layout(self) -> bool:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if payload.get("version") != self.STATE_VERSION:
                return False
            state = QByteArray.fromBase64(payload["state"].encode("ascii"))
            return self.restoreState(state, self.STATE_VERSION)
        except (OSError, ValueError, TypeError, KeyError):
            return False
