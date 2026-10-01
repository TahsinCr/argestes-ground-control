from __future__ import annotations

import base64
import json
from pathlib import Path

from PyQt6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QRect, QTimer, Qt
from PyQt6.QtGui import QColor, QDrag, QMouseEvent, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QMainWindow,
    QMenu,
    QStyle,
    QTabWidget,
    QWidget,
)


_PANEL_DRAG_MIME = "application/x-yki-panel-drag"
_TARGET_LABELS = {
    "tr": {"left": "Sol", "right": "Sağ", "above": "Üst", "below": "Alt", "tab": "Sekme"},
    "en": {"left": "Left", "right": "Right", "above": "Top", "below": "Bottom", "tab": "Tab"},
}


class _PanelDropOverlay(QWidget):
    """Show the same five drop targets for every workspace dock."""

    def __init__(self, workspace: PanelWorkspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.active: tuple[QDockWidget | None, str] | None = None

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_PANEL_DRAG_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if not event.mimeData().hasFormat(_PANEL_DRAG_MIME):
            event.ignore()
            return
        self.active = self.workspace._drop_target(self.mapToGlobal(event.position().toPoint()))
        self.update()
        if self.active is None:
            event.ignore()
        else:
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.active = None
        self.update()
        event.accept()

    def dropEvent(self, event) -> None:
        source = self.workspace._drag_source
        target = self.workspace._drop_target(self.mapToGlobal(event.position().toPoint()))
        if source is not None and target is not None and self.workspace._apply_drop(source, target):
            event.acceptProposedAction()
        else:
            event.ignore()

    def paintEvent(self, event) -> None:
        if self.active is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self.workspace.palette().color(QPalette.ColorRole.Window).lightness() < 128
        ink = QColor(246, 247, 249) if dark else QColor(27, 34, 43)
        badge_ink = QColor(27, 34, 43) if dark else QColor(255, 255, 255)
        dock, active_relation = self.active
        if dock is not None:
            origin = self.mapFromGlobal(dock.mapToGlobal(QPoint()))
            bounds = QRect(origin, dock.size()).intersected(self.rect())
            for relation, zone in self.workspace._drop_zones(bounds).items():
                selected = active_relation == relation
                border = QColor(ink)
                border.setAlpha(235 if selected else 125)
                fill = QColor(ink)
                fill.setAlpha(70 if selected else 16)
                painter.setPen(QPen(border, 2 if selected else 1))
                painter.setBrush(fill)
                painter.drawRoundedRect(zone.adjusted(2, 2, -2, -2), 4, 4)
                if zone.width() >= 60 and zone.height() >= 32:
                    language = "en" if self.workspace.language == "en" else "tr"
                    label = _TARGET_LABELS[language][relation]
                    badge = QRect(0, 0, painter.fontMetrics().horizontalAdvance(label) + 18, 24)
                    badge.moveCenter(zone.center())
                    painter.setPen(Qt.PenStyle.NoPen)
                    badge_color = QColor(ink)
                    badge_color.setAlpha(230 if selected else 155)
                    painter.setBrush(badge_color)
                    painter.drawRoundedRect(badge, 5, 5)
                    painter.setPen(badge_ink)
                    painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        else:
            margin = max(24, min(48, self.width() // 30))
            outer_zones = {
                "left": QRect(0, margin, margin, self.height() - 2 * margin),
                "right": QRect(self.width() - margin, margin, margin, self.height() - 2 * margin),
                "above": QRect(0, 0, self.width(), margin),
                "below": QRect(0, self.height() - margin, self.width(), margin),
            }
            fill = QColor(ink)
            fill.setAlpha(70)
            painter.setPen(QPen(ink, 2))
            painter.setBrush(fill)
            painter.drawRoundedRect(
                outer_zones[active_relation].adjusted(2, 2, -2, -2), 4, 4
            )
        painter.end()


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
            QTabWidget.TabPosition.North,
        )
        self._configure_dock_corners()
        self.panels: dict[str, QDockWidget] = {}
        self._panel_keys: dict[QDockWidget, str] = {}
        self._panel_options: dict[str, tuple[bool, bool, bool]] = {}
        self._panel_default_areas: dict[str, Qt.DockWidgetArea] = {}
        self._panel_sizes: dict[str, tuple[int, int]] = {}
        self._size_restore_pending = False
        self._restoring_panel_sizes = False
        self._panel_size_restore_enabled = False
        self.edit_mode = False
        self._quiet_titles: dict[str, QWidget] = {}
        self._drag_start: tuple[QDockWidget, QPoint] | None = None
        self._drag_source: QDockWidget | None = None
        self._drop_overlay = _PanelDropOverlay(self)
        self._drop_overlay.hide()

    def _configure_dock_corners(self) -> None:
        self.setCorner(Qt.Corner.TopLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)

    def add_panel(
        self,
        key: str,
        title: str,
        widget: QWidget,
        area: Qt.DockWidgetArea,
        *,
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
        if isinstance(watched, QDockWidget) and watched in self._panel_keys:
            if isinstance(event, QMouseEvent):
                if event.type() == QEvent.Type.MouseButtonPress:
                    if (
                        self.edit_mode
                        and self._panel_options[self._panel_keys[watched]][1]
                        and event.button() == Qt.MouseButton.LeftButton
                    ):
                        point = event.position().toPoint()
                        if 0 <= point.y() <= watched.style().pixelMetric(
                            QStyle.PixelMetric.PM_TitleBarHeight, None, watched
                        ) + 4:
                            self._drag_start = (watched, event.globalPosition().toPoint())
                            return True
                elif event.type() == QEvent.Type.MouseMove and self._drag_start:
                    dock, start = self._drag_start
                    if event.buttons() & Qt.MouseButton.LeftButton:
                        if (event.globalPosition().toPoint() - start).manhattanLength() >= QApplication.startDragDistance():
                            self._drag_start = None
                            self._start_panel_drag(dock)
                        return True
                elif event.type() == QEvent.Type.MouseButtonRelease and self._drag_start:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._drag_start = None
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
        self._drag_start = None
        for key in self.panels:
            self._apply_panel_mode(key)

    def _apply_panel_mode(self, key: str) -> None:
        dock = self.panels[key]
        closable, movable, _floatable = self._panel_options[key]
        features = QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
        if closable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetClosable
        # Dragging is handled by the shared overlay, so Qt must not start a
        # second, independent dock drag with different target rules.
        dock.setFeatures(features)
        if self.edit_mode and movable:
            dock.setTitleBarWidget(None)
        else:
            dock.setTitleBarWidget(self._quiet_titles[key])

    def _start_panel_drag(self, dock: QDockWidget) -> None:
        self._drag_source = dock
        self._drop_overlay.setGeometry(self.rect())
        self._drop_overlay.active = None
        self._drop_overlay.show()
        self._drop_overlay.raise_()
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_PANEL_DRAG_MIME, self._panel_keys[dock].encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(dock.grab(QRect(0, 0, min(280, dock.width()), 28)))
        drag.setHotSpot(QPoint(24, 12))
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._drop_overlay.hide()
            self._drag_source = None

    def _drop_zones(self, bounds: QRect) -> dict[str, QRect]:
        w, h = bounds.width(), bounds.height()
        edge_x, edge_y = max(1, int(w * .28)), max(1, int(h * .25))
        x, y = bounds.x(), bounds.y()
        return {
            "left": QRect(x, y + edge_y, edge_x, h - 2 * edge_y),
            "right": QRect(x + w - edge_x, y + edge_y, edge_x, h - 2 * edge_y),
            "above": QRect(x, y, w, edge_y),
            "below": QRect(x, y + h - edge_y, w, edge_y),
            "tab": QRect(x + edge_x, y + edge_y, w - 2 * edge_x, h - 2 * edge_y),
        }

    def _drop_target(
        self, global_position: QPoint, *, source: QDockWidget | None = None
    ) -> tuple[QDockWidget | None, str] | None:
        local = self.mapFromGlobal(global_position)
        if not self.rect().contains(local):
            return None
        margin = max(24, min(48, self.width() // 30))
        if local.x() >= self.width() - margin:
            return None, "right"
        if local.x() < margin:
            return None, "left"
        if local.y() < margin:
            return None, "above"
        if local.y() >= self.height() - margin:
            return None, "below"
        source = source or self._drag_source
        for dock in reversed(tuple(self.panels.values())):
            if dock is source or not dock.isVisible() or dock.isFloating():
                continue
            point = dock.mapFromGlobal(global_position)
            if not dock.rect().contains(point):
                continue
            for relation, zone in self._drop_zones(dock.rect()).items():
                if zone.contains(point):
                    return dock, relation
        return None

    def _apply_drop(self, source: QDockWidget, target: tuple[QDockWidget | None, str]) -> bool:
        if source not in self._panel_keys:
            return False
        dock, relation = target
        if dock is not None:
            return self.place_panel(dock, source, relation)
        area = {
            "left": Qt.DockWidgetArea.LeftDockWidgetArea,
            "right": Qt.DockWidgetArea.RightDockWidgetArea,
            "above": Qt.DockWidgetArea.TopDockWidgetArea,
            "below": Qt.DockWidgetArea.BottomDockWidgetArea,
        }[relation]
        candidates = [
            candidate for candidate in self.panels.values()
            if candidate is not source and candidate.isVisible()
            and not candidate.isFloating() and self.dockWidgetArea(candidate) == area
        ]
        if candidates:
            if relation in {"right", "below"}:
                anchor = max(candidates, key=lambda item: item.geometry().right() if relation == "right" else item.geometry().bottom())
            else:
                anchor = min(candidates, key=lambda item: item.geometry().left() if relation == "left" else item.geometry().top())
            return self.place_panel(anchor, source, relation)
        return self.move_panel_to_area(source, area)

    def drop_panel_at(self, dock: QDockWidget, global_position: QPoint) -> bool:
        """Resolve drag drops to a panel center, edge, or workspace outer edge."""
        if not self.edit_mode or dock not in self._panel_keys:
            return False
        target = self._drop_target(global_position, source=dock)
        return target is not None and self._apply_drop(dock, target)

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
        if self._drag_source is dock:
            self._drag_source = None
            self._drop_overlay.hide()
        if self._drop_overlay.active is not None and self._drop_overlay.active[0] is dock:
            self._drop_overlay.active = None
        self._panel_keys.pop(dock, None)
        self._panel_options.pop(key, None)
        self._panel_default_areas.pop(key, None)
        self._panel_sizes.pop(key, None)
        self._quiet_titles.pop(key, None)
        dock.removeEventFilter(self)
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
                self._configure_dock_corners()
                self.setTabPosition(
                    Qt.DockWidgetArea.AllDockWidgetAreas,
                    QTabWidget.TabPosition.North,
                )
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
