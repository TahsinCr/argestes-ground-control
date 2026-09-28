from __future__ import annotations

import base64
import json
from pathlib import Path

from PyQt6.QtCore import QByteArray, QEvent, QPoint, QTimer, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QDockWidget,
    QMainWindow,
    QMenu,
    QStyle,
    QTabWidget,
    QWidget,
)


class PanelWorkspace(QMainWindow):
    """Dockable shell around the unchanged content widgets."""

    STATE_VERSION = 4
    LEGACY_STATE_VERSIONS = (1, 2, 3)

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
        self._panel_groups: dict[str, str] = {}
        self._panel_options: dict[str, tuple[bool, bool, bool]] = {}
        self._panel_default_areas: dict[str, Qt.DockWidgetArea] = {}
        self._panel_sizes: dict[str, tuple[int, int]] = {}
        self._size_restore_pending = False
        self._restoring_panel_sizes = False
        self._panel_size_restore_enabled = False
        self.edit_mode = False
        self._quiet_titles: dict[str, QWidget] = {}
        self._native_drag_start: tuple[QDockWidget, QPoint, bool] | None = None

    def add_panel(
        self,
        key: str,
        title: str,
        widget: QWidget,
        area: Qt.DockWidgetArea,
        *,
        group: str = "workspace",
        allowed_areas: Qt.DockWidgetArea = Qt.DockWidgetArea.AllDockWidgetAreas,
        movable: bool = True,
        floatable: bool = True,
        closable: bool = True,
    ) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"panel.{key}")
        dock.setAllowedAreas(allowed_areas)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        self.panels[key] = dock
        self._panel_keys[dock] = key
        self._panel_groups[key] = group
        self._panel_options[key] = (closable, movable, floatable)
        self._panel_default_areas[key] = area
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
        if isinstance(event, QMouseEvent):
            position = event.globalPosition().toPoint()
            if event.type() == QEvent.Type.MouseButtonPress:
                dock = self._dock_from_mouse_target(watched)
                if dock is not None and self.edit_mode:
                    local = dock.mapFromGlobal(position)
                    title_height = dock.style().pixelMetric(
                        QStyle.PixelMetric.PM_TitleBarHeight, None, dock
                    )
                    if 0 <= local.y() <= max(24, title_height + 4):
                        self._native_drag_start = (dock, position, False)
            elif event.type() == QEvent.Type.MouseMove and self._native_drag_start:
                dock, start, dragging = self._native_drag_start
                if (
                    event.buttons() & Qt.MouseButton.LeftButton
                    and (position - start).manhattanLength()
                    >= QApplication.startDragDistance()
                ):
                    self._native_drag_start = (dock, start, True)
                    return True
                if dragging:
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease and self._native_drag_start:
                dock, start, dragging = self._native_drag_start
                self._native_drag_start = None
                if event.button() == Qt.MouseButton.LeftButton and dragging:
                    placed = self.drop_panel_at(dock, position)
                    if not placed and self._panel_options[
                        self._panel_keys[dock]
                    ][2]:
                        dock.setFloating(True)
                    return True
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
        closable, movable, floatable = self._panel_options[key]
        features = QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
        if closable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetClosable
        if self.edit_mode and movable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetMovable
        if self.edit_mode and floatable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetFloatable
        dock.setFeatures(features)
        if self.edit_mode and movable:
            dock.setTitleBarWidget(None)
            self._watch_dock_mouse_events(dock, enabled=True)
        else:
            self._watch_dock_mouse_events(dock, enabled=False)
            dock.setTitleBarWidget(self._quiet_titles[key])

    def _watch_dock_mouse_events(self, dock: QDockWidget, *, enabled: bool) -> None:
        for child in dock.findChildren(QWidget):
            if child is self._quiet_titles.get(self._panel_keys.get(dock, "")):
                continue
            if enabled:
                child.installEventFilter(self)
            else:
                child.removeEventFilter(self)

    def _dock_from_mouse_target(self, target) -> QDockWidget | None:
        if isinstance(target, QAbstractButton):
            return None
        widget = target if isinstance(target, QWidget) else None
        while widget is not None and widget is not self:
            if isinstance(widget, QAbstractButton):
                return None
            if isinstance(widget, QDockWidget):
                return widget if widget in self._panel_keys else None
            widget = widget.parentWidget()
        return None

    def drop_panel_at(self, dock: QDockWidget, global_position: QPoint) -> bool:
        """Resolve drag drops to a panel center, edge, or workspace outer edge."""
        if not self.edit_mode or dock not in self._panel_keys:
            return False
        source_key = self._panel_keys[dock]
        source_group = self._panel_groups.get(source_key)
        candidates = [
            target for key, target in self.panels.items()
            if target is not dock and target.isVisible()
            and self._panel_groups.get(key) == source_group
        ]
        target = next(
            (candidate for candidate in candidates
             if candidate.rect().contains(candidate.mapFromGlobal(global_position))),
            None,
        )
        if target is not None:
            rect = target.rect()
            x = global_position.x() - target.mapToGlobal(rect.topLeft()).x()
            y = global_position.y() - target.mapToGlobal(rect.topLeft()).y()
            nx, ny = x / max(1, rect.width()), y / max(1, rect.height())
            center = 0.28 <= nx <= 0.72 and 0.25 <= ny <= 0.75
            if center:
                return self.place_panel(target, dock, "tab")
            relation = min(
                ((nx, "left"), (1 - nx, "right"), (ny, "above"), (1 - ny, "below")),
                key=lambda item: item[0],
            )[1]
            return self.place_panel(target, dock, relation)

        workspace_rect = self.rect()
        workspace_top_left = self.mapToGlobal(workspace_rect.topLeft())
        local = global_position - workspace_top_left
        if not workspace_rect.contains(local):
            return False
        edge_margin = max(36, min(64, workspace_rect.width() // 30))
        edge_area = None
        if local.x() >= workspace_rect.width() - edge_margin:
            edge_area = Qt.DockWidgetArea.RightDockWidgetArea
        elif local.x() <= edge_margin:
            edge_area = Qt.DockWidgetArea.LeftDockWidgetArea
        elif local.y() <= edge_margin:
            edge_area = Qt.DockWidgetArea.TopDockWidgetArea
        elif local.y() >= workspace_rect.height() - edge_margin:
            edge_area = Qt.DockWidgetArea.BottomDockWidgetArea
        if edge_area is None or not dock.allowedAreas() & edge_area:
            return False
        edge_targets = [
            target for target in candidates
            if self.dockWidgetArea(target) == edge_area
        ]
        if edge_targets:
            if edge_area == Qt.DockWidgetArea.RightDockWidgetArea:
                target = max(edge_targets, key=lambda item: item.geometry().right())
                return self.place_panel(target, dock, "right")
            if edge_area == Qt.DockWidgetArea.LeftDockWidgetArea:
                target = min(edge_targets, key=lambda item: item.geometry().left())
                return self.place_panel(target, dock, "left")
            if edge_area == Qt.DockWidgetArea.BottomDockWidgetArea:
                target = max(edge_targets, key=lambda item: item.geometry().bottom())
                return self.place_panel(target, dock, "below")
            target = min(edge_targets, key=lambda item: item.geometry().top())
            return self.place_panel(target, dock, "above")
        return self.move_panel_to_area(dock, edge_area)

    def _show_panel_menu(self, dock: QDockWidget, point) -> None:
        if not self.edit_mode:
            return
        english = self.language == "en"
        menu = QMenu(dock)
        move = menu.addMenu("Move Panel" if english else "Paneli taşı")
        for title, area in (
            ("Left" if english else "Sola", Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Right" if english else "Sağa", Qt.DockWidgetArea.RightDockWidgetArea),
            ("Top" if english else "Yukarı", Qt.DockWidgetArea.TopDockWidgetArea),
            ("Bottom" if english else "Aşağıya", Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            if not dock.allowedAreas() & area:
                continue
            move.addAction(
                title,
                lambda selected_area=area: self.move_panel_to_area(dock, selected_area),
            )
        if self._panel_options[self._panel_keys[dock]][2]:
            menu.addAction(
                ("Float" if english else "Ayrı pencere")
                if not dock.isFloating() else
                ("Dock" if english else "Pencereye sabitle"),
                lambda: dock.setFloating(not dock.isFloating()),
            )
        tab_targets = [
            (key, candidate) for key, candidate in self.panels.items()
            if candidate is not dock
            and self._panel_groups.get(key) == self._panel_groups.get(self._panel_keys[dock])
        ]
        if tab_targets:
            place = menu.addMenu("Yerleştir" if not english else "Place Panel")
            for key, candidate in tab_targets:
                candidate_area = self.dockWidgetArea(candidate)
                if not dock.allowedAreas() & candidate_area:
                    continue
                target_menu = place.addMenu(candidate.windowTitle() or key)
                for label, relation in (
                    ("Sekme yap" if not english else "Tab with", "tab"),
                    ("Soluna yerleştir" if not english else "Place left", "left"),
                    ("Sağına yerleştir" if not english else "Place right", "right"),
                    ("Üstüne yerleştir" if not english else "Place above", "above"),
                    ("Altına yerleştir" if not english else "Place below", "below"),
                ):
                    target_menu.addAction(
                        label,
                        lambda target=candidate, direction=relation: self.place_panel(
                            target, dock, direction
                        ),
                    )
        menu.addSeparator()
        menu.addAction("Hide Panel" if english else "Paneli gizle", dock.hide)
        menu.exec(dock.mapToGlobal(point))

    def _tabify_panel(self, target: QDockWidget, dock: QDockWidget) -> None:
        self.place_panel(target, dock, "tab")

    def move_panel_to_area(self, dock: QDockWidget, area: Qt.DockWidgetArea) -> bool:
        if dock not in self._panel_keys or not dock.allowedAreas() & area:
            return False
        if dock.isFloating():
            dock.setFloating(False)
        self.addDockWidget(area, dock)
        return True

    def place_panel(
        self, target: QDockWidget, dock: QDockWidget, relation: str
    ) -> bool:
        """Place a panel relative to another using the workspace's shared rules."""
        if target is dock or relation not in {"tab", "left", "right", "above", "below"}:
            return False
        target_key = self._panel_keys.get(target)
        dock_key = self._panel_keys.get(dock)
        if target_key is None or dock_key is None:
            return False
        if self._panel_groups.get(target_key) != self._panel_groups.get(dock_key):
            return False
        area = self.dockWidgetArea(target)
        if area == Qt.DockWidgetArea.NoDockWidgetArea or not dock.allowedAreas() & area:
            return False
        if relation == "tab":
            self.move_panel_to_area(dock, area)
            self.tabifyDockWidget(target, dock)
            dock.show()
            dock.raise_()
            return True
        if self.dockWidgetArea(dock) != area:
            self.move_panel_to_area(dock, area)
        if relation in {"left", "right"}:
            orientation = Qt.Orientation.Horizontal
            before = relation == "left"
        else:
            orientation = Qt.Orientation.Vertical
            before = relation == "above"
        first, second = (dock, target) if before else (target, dock)
        self.splitDockWidget(first, second, orientation)
        dock.show()
        dock.raise_()
        return True

    def remove_panel(self, key: str) -> QWidget | None:
        dock = self.panels.pop(key, None)
        if dock is None:
            return None
        self._panel_keys.pop(dock, None)
        self._panel_groups.pop(key, None)
        self._panel_options.pop(key, None)
        self._panel_default_areas.pop(key, None)
        self._panel_sizes.pop(key, None)
        self._quiet_titles.pop(key, None)
        dock.removeEventFilter(self)
        for child in dock.findChildren(QWidget):
            child.removeEventFilter(self)
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
                if state_version == 1:
                    self._restore_map_into_legacy_workspace()
                self._normalize_panel_areas()
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

    def _normalize_panel_areas(self) -> None:
        preferred_areas = (
            Qt.DockWidgetArea.LeftDockWidgetArea,
            Qt.DockWidgetArea.RightDockWidgetArea,
            Qt.DockWidgetArea.TopDockWidgetArea,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        for key, dock in self.panels.items():
            current_area = self.dockWidgetArea(dock)
            if dock.allowedAreas() & current_area:
                continue
            destination = self._panel_default_areas[key]
            if not dock.allowedAreas() & destination:
                destination = next(
                    area for area in preferred_areas if dock.allowedAreas() & area
                )
            self.move_panel_to_area(dock, destination)

    def _restore_map_into_legacy_workspace(self) -> None:
        """Reattach the map dock when upgrading layouts that used a central map."""
        map_dock = self.panels.get("map")
        if map_dock is None:
            return
        area = Qt.DockWidgetArea.RightDockWidgetArea
        map_dock.setFloating(False)
        self.move_panel_to_area(map_dock, area)
        adjacent = next(
            (
                dock for key, dock in self.panels.items()
                if key not in {
                    "map", "flight", "telemetry", "commands", "logs",
                    "parameters",
                }
                and dock.isVisible()
                and not dock.isFloating()
                and self.dockWidgetArea(dock) == area
            ),
            None,
        )
        if adjacent is not None:
            self.place_panel(map_dock, adjacent, "right")
        map_dock.show()
