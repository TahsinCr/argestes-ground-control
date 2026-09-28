from __future__ import annotations

import base64
import json
from pathlib import Path

from PyQt6.QtCore import QByteArray, QEvent, QTimer, Qt
from PyQt6.QtWidgets import QDockWidget, QMainWindow, QMenu, QTabWidget, QWidget


class PanelWorkspace(QMainWindow):
    """Dockable shell around the unchanged content widgets."""

    STATE_VERSION = 2
    LEGACY_STATE_VERSIONS = (1,)

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
        )
        self.setTabPosition(
            Qt.DockWidgetArea.AllDockWidgetAreas,
            QTabWidget.TabPosition.South,
        )
        self.panels: dict[str, QDockWidget] = {}
        self._panel_keys: dict[QDockWidget, str] = {}
        self._panel_sizes: dict[str, tuple[int, int]] = {}
        self._size_restore_pending = False
        self._restoring_panel_sizes = False
        self._panel_size_restore_enabled = False
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
        self._panel_keys[dock] = key
        dock.installEventFilter(self)
        dock.visibilityChanged.connect(
            lambda visible, panel_key=key: self._on_panel_visibility_changed(
                panel_key, visible
            )
        )
        quiet_title = QWidget(dock)
        quiet_title.setFixedHeight(0)
        self._quiet_titles[key] = quiet_title
        self._apply_panel_mode(key)
        dock.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        dock.customContextMenuRequested.connect(
            lambda point, panel=dock: self._show_panel_menu(panel, point)
        )
        return dock

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.Type.Resize
            and not self._restoring_panel_sizes
            and isinstance(watched, QDockWidget)
            and watched in self._panel_keys
            and watched.isVisible()
        ):
            self._remember_panel_size(self._panel_keys[watched], watched)
        return super().eventFilter(watched, event)

    def _on_panel_visibility_changed(self, key: str, visible: bool) -> None:
        if self._restoring_panel_sizes:
            return
        dock = self.panels.get(key)
        if dock is None:
            return
        if not visible:
            if not self._size_restore_pending:
                for panel_key, panel in self.panels.items():
                    if panel.isVisible() or panel is dock:
                        self._remember_panel_size(panel_key, panel)
                self._size_restore_pending = True
                self._restoring_panel_sizes = True
                QTimer.singleShot(0, self._finish_panel_visibility_change)
        else:
            self._schedule_panel_size_restore()

    def _finish_panel_visibility_change(self) -> None:
        self._restoring_panel_sizes = False
        self._size_restore_pending = False

    def _remember_panel_size(self, key: str, dock: QDockWidget) -> None:
        width, height = dock.width(), dock.height()
        if width > 0 and height > 0:
            self._panel_sizes[key] = (width, height)

    def _schedule_panel_size_restore(self) -> None:
        if (
            not self._panel_size_restore_enabled
            or self._size_restore_pending
            or not self._panel_sizes
        ):
            return
        self._size_restore_pending = True
        self._restoring_panel_sizes = True
        QTimer.singleShot(0, self._restore_panel_sizes)

    def enable_panel_size_restore(self, *, capture_current: bool = False) -> None:
        self._panel_size_restore_enabled = True
        if capture_current:
            for key, dock in self.panels.items():
                if dock.isVisible():
                    self._remember_panel_size(key, dock)

    def _restore_panel_sizes(self) -> None:
        try:
            areas = (
                Qt.DockWidgetArea.LeftDockWidgetArea,
                Qt.DockWidgetArea.RightDockWidgetArea,
                Qt.DockWidgetArea.TopDockWidgetArea,
                Qt.DockWidgetArea.BottomDockWidgetArea,
            )
            for area in areas:
                docks = [
                    dock for key, dock in self.panels.items()
                    if dock.isVisible()
                    and self.dockWidgetArea(dock) == area
                    and key in self._panel_sizes
                ]
                if not docks:
                    continue
                for orientation, dimension in (
                    (Qt.Orientation.Horizontal, 0),
                    (Qt.Orientation.Vertical, 1),
                ):
                    self.resizeDocks(
                        docks,
                        [self._panel_sizes[self._panel_keys[dock]][dimension]
                         for dock in docks],
                        orientation,
                    )
        finally:
            self._restoring_panel_sizes = False
            self._size_restore_pending = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._schedule_panel_size_restore()

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
        tab_targets = [
            (key, candidate) for key, candidate in self.panels.items()
            if candidate is not dock
        ]
        if tab_targets:
            tabs = menu.addMenu("Tab with Panel" if english else "Sekme olarak birleştir")
            for key, candidate in tab_targets:
                tabs.addAction(
                    candidate.windowTitle() or key,
                    lambda target=candidate: self._tabify_panel(target, dock),
                )
        menu.addSeparator()
        menu.addAction("Hide Panel" if english else "Paneli gizle", dock.hide)
        menu.exec(dock.mapToGlobal(point))

    def _tabify_panel(self, target: QDockWidget, dock: QDockWidget) -> None:
        if target.isFloating():
            target.setFloating(False)
        if dock.isFloating():
            dock.setFloating(False)
        area = self.dockWidgetArea(target)
        if area == Qt.DockWidgetArea.NoDockWidgetArea:
            area = Qt.DockWidgetArea.RightDockWidgetArea
        if self.dockWidgetArea(dock) != area:
            self.addDockWidget(area, dock)
        self.tabifyDockWidget(target, dock)
        dock.show()
        dock.raise_()

    def remove_panel(self, key: str) -> QWidget | None:
        dock = self.panels.pop(key, None)
        if dock is None:
            return None
        self._panel_keys.pop(dock, None)
        self._panel_sizes.pop(key, None)
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
            for key, dock in self.panels.items():
                if dock.isVisible() and not self._restoring_panel_sizes:
                    self._remember_panel_size(key, dock)
            state = base64.b64encode(bytes(self.saveState(self.STATE_VERSION))).decode("ascii")
            payload = {
                "version": self.STATE_VERSION,
                "state": state,
                "panel_sizes": {
                    key: {"width": width, "height": height}
                    for key, (width, height) in self._panel_sizes.items()
                },
            }
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
            state_version = payload.get("version")
            if state_version not in (*self.LEGACY_STATE_VERSIONS, self.STATE_VERSION):
                return False
            state = QByteArray.fromBase64(payload["state"].encode("ascii"))
            restored_sizes: dict[str, tuple[int, int]] = {}
            sizes = payload.get("panel_sizes", {})
            if isinstance(sizes, dict):
                for key, size in sizes.items():
                    if key not in self.panels or not isinstance(size, dict):
                        continue
                    width, height = size.get("width"), size.get("height")
                    if (
                        isinstance(width, int) and not isinstance(width, bool)
                        and width > 0
                        and isinstance(height, int) and not isinstance(height, bool)
                        and height > 0
                    ):
                        restored_sizes[key] = (width, height)
            self._panel_sizes = restored_sizes
            self._restoring_panel_sizes = True
            restored = self.restoreState(state, state_version)
            if restored:
                if state_version in self.LEGACY_STATE_VERSIONS:
                    self._restore_map_into_legacy_workspace()
                self._panel_size_restore_enabled = True
                if restored_sizes:
                    self._schedule_panel_size_restore()
                else:
                    self._restoring_panel_sizes = False
            else:
                self._restoring_panel_sizes = False
            return restored
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def _restore_map_into_legacy_workspace(self) -> None:
        """Reattach the map dock when upgrading layouts that used a central map."""
        map_dock = self.panels.get("map")
        if map_dock is None:
            return
        area = Qt.DockWidgetArea.RightDockWidgetArea
        map_dock.setFloating(False)
        self.addDockWidget(area, map_dock)
        adjacent = next(
            (
                dock for key, dock in self.panels.items()
                if key not in {
                    "map", "flight", "telemetry", "commands", "logs",
                    "parameters", "extensions",
                }
                and dock.isVisible()
                and not dock.isFloating()
                and self.dockWidgetArea(dock) == area
            ),
            None,
        )
        if adjacent is not None:
            self.splitDockWidget(map_dock, adjacent, Qt.Orientation.Horizontal)
        map_dock.show()
