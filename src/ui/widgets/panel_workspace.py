from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QMimeData,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QDrag, QMouseEvent, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QStyle,
    QTabWidget,
    QToolButton,
    QWidget,
)

_PANEL_DRAG_MIME = "application/x-yki-panel-drag"
_TARGET_LABELS = {
    "tr": {
        "left": "Sol",
        "right": "Sağ",
        "above": "Üst",
        "below": "Alt",
        "tab": "Sekme",
    },
    "en": {
        "left": "Left",
        "right": "Right",
        "above": "Top",
        "below": "Bottom",
        "tab": "Tab",
    },
}


class _PanelGrip(QToolButton):
    """A small drag surface; content mouse events never initiate a drag."""

    def __init__(self, controls) -> None:
        super().__init__(controls)
        self.controls = controls
        self.start: QPoint | None = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedSize(36, 20)
        self.setToolTip(controls.dock.windowTitle())

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.Text))
        for x in (12, 18, 24):
            for y in (7, 13):
                painter.drawEllipse(QPoint(x, y), 1, 1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.globalPosition().toPoint()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not event.buttons() & Qt.MouseButton.LeftButton:
            self.start = None
        if self.start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (
                event.globalPosition().toPoint() - self.start
            ).manhattanLength() >= QApplication.startDragDistance():
                self.start = None
                self.controls.workspace._start_panel_drag(self.controls.dock)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self.start = None
        super().mouseReleaseEvent(event)


class _PanelControls(QWidget):
    """Hover-only controls animated independently of expensive panel content."""

    def __init__(self, workspace, dock) -> None:
        # Keep the overlay mounted: show/hide invalidates Qt's dock layout and
        # can progressively redistribute split widths even on idle hover.
        super().__init__(dock)
        self.workspace, self.dock = workspace, dock
        self.setObjectName("panelHoverControls")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 1, 3, 1)
        layout.setSpacing(0)
        self.grip = _PanelGrip(self)
        self.grip.setEnabled(workspace._panel_options[workspace._panel_keys[dock]][1])
        self.arrow = QToolButton(self)
        self.arrow.setArrowType(Qt.ArrowType.DownArrow)
        self.arrow.setFixedSize(20, 20)
        self.arrow.setToolTip(
            "Panel settings" if workspace.language == "en" else "Panel ayarları"
        )
        self.arrow.clicked.connect(self.open_menu)
        layout.addWidget(self.grip)
        layout.addWidget(self.arrow)
        self.setFixedSize(62, 22)
        self.setStyleSheet(
            "#panelHoverControls { border: 1px solid palette(mid); border-radius: 7px; background: palette(window); } QToolButton { border: none; border-radius: 5px; color: palette(text); background: transparent; } QToolButton:hover { background: palette(mid); }"
        )
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.effect.setOpacity(0)
        self.animation = QPropertyAnimation(self.effect, b"opacity", self)
        self.animation.setDuration(130)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self._finished)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.show()

    def reposition(self) -> None:
        self.move(max(0, (self.dock.width() - self.width()) // 2), 4)

    def dismiss(self) -> None:
        self.animation.stop()
        self.animation.setEndValue(0.0)
        self.effect.setOpacity(0.0)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def enterEvent(self, event) -> None:
        self.reveal(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.defer_hover_check()
        super().leaveEvent(event)

    def defer_hover_check(self) -> None:
        QTimer.singleShot(0, self._check_hover)

    def _check_hover(self) -> None:
        if not self.dock.isVisible() or self.workspace.property("selectingPanels"):
            self.dismiss()
        else:
            self.reveal(self.dock.underMouse() or self.underMouse())

    def reveal(self, visible: bool) -> None:
        end = 1.0 if visible else 0.0
        if self.animation.endValue() == end:
            return
        self.animation.stop()
        if visible:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.reposition()
            self.raise_()
        self.animation.setStartValue(self.effect.opacity())
        self.animation.setEndValue(end)
        self.animation.start()

    def _finished(self) -> None:
        if self.effect.opacity() == 0:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def open_menu(self) -> None:
        self.workspace._show_panel_menu(
            self.dock,
            self.dock.mapFromGlobal(self.mapToGlobal(QPoint(0, self.height()))),
        )
        self._check_hover()


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
        self.active = self.workspace._drop_target(
            self.mapToGlobal(event.position().toPoint())
        )
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
        target = self.workspace._drop_target(
            self.mapToGlobal(event.position().toPoint())
        )
        if source is not None and target is not None:
            # Do not mutate Qt's dock layout inside its nested drag event loop.
            self.workspace._pending_drop = (source, target)
            event.acceptProposedAction()
        else:
            event.ignore()

    def paintEvent(self, event) -> None:
        if self.active is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = (
            self.workspace.palette().color(QPalette.ColorRole.Window).lightness() < 128
        )
        ink = QColor(246, 247, 249) if dark else QColor(27, 34, 43)
        badge_ink = QColor(27, 34, 43) if dark else QColor(255, 255, 255)
        dock, active_relation = self.active
        if dock is not None:
            bounds = self.workspace._drop_bounds(dock).intersected(self.rect())
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
                    badge = QRect(
                        0, 0, painter.fontMetrics().horizontalAdvance(label) + 18, 24
                    )
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
                "right": QRect(
                    self.width() - margin, margin, margin, self.height() - 2 * margin
                ),
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


@dataclass(frozen=True)
class _PanelResize:
    first: QDockWidget
    second: QDockWidget
    orientation: Qt.Orientation
    start: QPoint
    first_size: int
    second_size: int


class PanelWorkspace(QMainWindow):
    layout_changed = pyqtSignal()
    """Dockable shell around the unchanged content widgets."""

    STATE_VERSION = 6
    LEGACY_STATE_VERSIONS = (1, 2, 3, 4, 5)

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
        # Qt still creates zero-duration, deferred-delete dock animations when
        # AnimatedDocks is disabled. Avoid that native lifetime path entirely.
        self.setStyleSheet(
            "QMainWindow#PanelWorkspace QWidget { widget-animation-duration: 0; }"
        )
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
        self._locked_sizes = {}
        self._unlocked_limits = {}
        self._size_restore_pending = False
        self._size_restore_timer = QTimer(self)
        self._size_restore_timer.setSingleShot(True)
        self._size_restore_timer.timeout.connect(self._restore_panel_sizes)
        self._visibility_timer = QTimer(self)
        self._visibility_timer.setSingleShot(True)
        self._visibility_timer.timeout.connect(self._finish_panel_visibility_change)
        self._restoring_panel_sizes = False
        self._panel_size_restore_enabled = False
        self.edit_mode = False
        self._quiet_titles: dict[str, QWidget] = {}
        self._hover_controls: dict[str, _PanelControls] = {}
        self._panel_hidden: dict[str, bool] = {}
        self._drag_start: tuple[QDockWidget, QPoint] | None = None
        self._drag_source: QDockWidget | None = None
        self._panel_resize: _PanelResize | None = None
        self._independent_widths: dict[str, int] = {}
        self._width_limits: dict[str, tuple[int, int, int]] = {}
        self._width_spacers: dict[str, QDockWidget] = {}
        self._width_sync_pending = False
        self._drop_overlay = _PanelDropOverlay(self)
        self._drop_overlay.hide()
        self.installEventFilter(self)

    def _configure_dock_corners(self) -> None:
        self.setCorner(Qt.Corner.TopLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        panels = getattr(self, "panels", {}).values()
        has_bottom = any(
            not dock.isHidden()
            and not dock.isFloating()
            and self.dockWidgetArea(dock) == Qt.DockWidgetArea.BottomDockWidgetArea
            for dock in panels
        )
        has_right = any(
            not dock.isHidden()
            and not dock.isFloating()
            and self.dockWidgetArea(dock) == Qt.DockWidgetArea.RightDockWidgetArea
            for dock in panels
        )
        # Keep the left column full-height while the right column is in use.
        # Once it is closed, let a visible bottom panel span the whole workspace
        # instead of leaving the lower-left corner assigned to the side column.
        bottom_left_area = (
            Qt.DockWidgetArea.BottomDockWidgetArea
            if has_bottom and not has_right
            else Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.setCorner(Qt.Corner.BottomLeftCorner, bottom_left_area)

    def paintEvent(self, event):
        super().paintEvent(event)
        if any(not dock.isHidden() for dock in self.panels.values()):
            return
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        english = self.language == "en"
        title = "Build your workspace" if english else "Çalışma alanınızı oluşturun"
        body = (
            "Open or hide panels from View → Panels.\nArrange panels using their top drag handle."
            if english
            else "Görünüm → Paneller menüsünden panel açıp kapatabilirsiniz.\nPanelleri üst tutamacından sürükleyerek yerleştirin."
        )
        center = self.rect().adjusted(35, 0, -35, 0)
        font = painter.font()
        font.setPointSize(18)
        painter.setFont(font)
        painter.drawText(
            center.adjusted(0, -70, 0, -70), Qt.AlignmentFlag.AlignCenter, title
        )
        font.setPointSize(11)
        painter.setFont(font)
        painter.drawText(
            center.adjusted(0, 25, 0, 25), Qt.AlignmentFlag.AlignCenter, body
        )

    def _separator_at(self, point: QPoint) -> _PanelResize | None:
        docks = [
            dock
            for dock in (*self.panels.values(), *self._width_spacers.values())
            if dock.isVisible()
            and not dock.isFloating()
            and self.rect().intersects(dock.geometry())
        ]
        extent = self.style().pixelMetric(
            QStyle.PixelMetric.PM_DockWidgetSeparatorExtent
        )
        for first in docks:
            a = first.geometry()
            for second in docks:
                if first is second:
                    continue
                b = second.geometry()
                if (
                    0 < b.top() - a.bottom() <= extent + 1
                    and a.bottom() < point.y() < b.top()
                    and max(a.left(), b.left())
                    <= point.x()
                    <= min(a.right(), b.right())
                ):
                    owners = {
                        spacer: self.panels[key]
                        for key, spacer in self._width_spacers.items()
                    }
                    return _PanelResize(
                        owners.get(first, first),
                        owners.get(second, second),
                        Qt.Orientation.Vertical,
                        point,
                        a.height(),
                        b.height(),
                    )
                if (
                    0 < b.left() - a.right() <= extent + 1
                    and a.right() < point.x() < b.left()
                    and max(a.top(), b.top())
                    <= point.y()
                    <= min(a.bottom(), b.bottom())
                ):
                    return _PanelResize(
                        first,
                        second,
                        Qt.Orientation.Horizontal,
                        point,
                        a.width(),
                        b.width(),
                    )
        return None

    def _resize_neighbors(self, point: QPoint) -> None:
        state = self._panel_resize
        if state is None:
            return
        first, second = state.first, state.second
        active_docks = {*self._panel_keys, *self._width_spacers.values()}
        if first not in active_docks or second not in active_docks:
            self._panel_resize = None
            return
        horizontal = state.orientation == Qt.Orientation.Horizontal
        if horizontal:
            for dock, start_size, direction in (
                (first, state.first_size, 1),
                (second, state.second_size, -1),
            ):
                key = self._panel_keys.get(dock)
                if key in self._width_spacers:
                    _, maximum, minimum = self._width_limits[key]
                    column_width = max(
                        self.panels[k].width() for k in self._width_spacers
                    )
                    available = max(0, self.width() - self.minimumSizeHint().width())
                    desired = start_size + direction * (point.x() - state.start.x())
                    desired = max(
                        minimum, min(maximum, column_width + available, desired)
                    )
                    self._independent_widths[key] = desired
                    if dock.width() != desired:
                        dock.setFixedWidth(desired)
                    return
        dimension = (
            (lambda size: size.width()) if horizontal else (lambda size: size.height())
        )
        minimum_first, maximum_first = self._resize_limits(first, state.orientation)
        minimum_second, maximum_second = self._resize_limits(second, state.orientation)
        total = state.first_size + state.second_size
        lower = max(minimum_first, total - maximum_second)
        upper = min(maximum_first, total - minimum_second)
        if lower > upper:
            return
        delta = (
            point.x() - state.start.x() if horizontal else point.y() - state.start.y()
        )
        size = max(lower, min(upper, state.first_size + delta))
        if dimension(first.size()) == size and dimension(second.size()) == total - size:
            return
        # Keep the shared pair's total constant; never borrow from a third dock.
        self.resizeDocks([first, second], [size, total - size], state.orientation)
        self.layout_changed.emit()

    def _resize_limits(
        self, dock: QDockWidget, orientation: Qt.Orientation
    ) -> tuple[int, int]:
        horizontal = orientation == Qt.Orientation.Horizontal
        span = (
            (lambda rect: (rect.left(), rect.right()))
            if horizontal
            else (lambda rect: (rect.top(), rect.bottom()))
        )
        dimension = (
            (lambda size: size.width()) if horizontal else (lambda size: size.height())
        )
        peers = {dock, *self.tabifiedDockWidgets(dock)}
        for panel in self.panels.values():
            if (
                panel.isVisible()
                and not panel.isFloating()
                and span(panel.geometry()) == span(dock.geometry())
            ):
                peers.add(panel)
        minimum = max(
            max(1, dimension(panel.minimumSize()), dimension(panel.minimumSizeHint()))
            for panel in peers
        )
        maximum = min(dimension(panel.maximumSize()) for panel in peers)
        return minimum, maximum

    def enable_independent_widths(
        self, keys: tuple[str, ...], *, default_width: int = 320
    ) -> None:
        """Remember preferred widths without adding empty layout cells."""
        for key in keys:
            dock = self.panels[key]
            self._width_limits.setdefault(
                key,
                (
                    dock.minimumWidth(),
                    dock.maximumWidth(),
                    max(1, dock.minimumWidth(), dock.minimumSizeHint().width()),
                ),
            )
            self._independent_widths.setdefault(key, default_width)
        self._sync_independent_widths()

    def _release_width_row(self, key: str) -> None:
        spacer = self._width_spacers.pop(key, None)
        if spacer is not None:
            self.removeDockWidget(spacer)
            spacer.deleteLater()
        dock = self.panels.get(key)
        if dock is not None and key in self._width_limits:
            minimum, maximum, _ = self._width_limits[key]
            dock.setMinimumWidth(minimum)
            dock.setMaximumWidth(maximum)

    def _schedule_width_sync(self) -> None:
        if self._width_sync_pending or not self._independent_widths:
            return
        self._width_sync_pending = True
        QTimer.singleShot(0, self._sync_independent_widths)

    def _sync_independent_widths(self, *, rebind: bool = False) -> None:
        self._width_sync_pending = False
        # Real split topology owns geometry: stacked siblings share a width,
        # horizontal siblings share a height. Dummy docks leave dead space and
        # fixed widths prevent Qt from filling available workspace on resize.
        for key in tuple(self._independent_widths):
            self._release_width_row(key)

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
        legacy_key: str | None = None,
    ) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"panel.{key}")
        dock.setProperty("legacyPanelKey", legacy_key)
        dock.setAllowedAreas(allowed_areas)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        self.panels[key] = dock
        self._panel_hidden[key] = dock.isHidden()
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
        self._hover_controls[key] = _PanelControls(self, dock)
        self._apply_panel_mode(key)
        dock.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        dock.customContextMenuRequested.connect(
            lambda point, panel=dock: self._show_panel_menu(panel, point)
        )
        return dock

    def eventFilter(self, watched, event) -> bool:
        # QObject can deliver child teardown events while Python's instance
        # dictionary is already being cleared (and during partial construction).
        if not hasattr(self, "_hover_controls"):
            return False
        if isinstance(event, QMouseEvent) and event.type() == QEvent.Type.MouseMove:
            if not event.buttons() & Qt.MouseButton.LeftButton:
                self._panel_resize = None
                self._drag_start = None
        if watched is self and isinstance(event, QMouseEvent):
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._panel_resize = self._separator_at(event.position().toPoint())
                    if self._panel_resize is not None:
                        return True
            elif (
                event.type() == QEvent.Type.MouseMove and self._panel_resize is not None
            ):
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self._resize_neighbors(event.position().toPoint())
                    return True
            elif (
                event.type() == QEvent.Type.MouseButtonRelease
                and self._panel_resize is not None
            ):
                if event.button() == Qt.MouseButton.LeftButton:
                    self._resize_neighbors(event.position().toPoint())
                    self._panel_resize = None
                    return True
        if (
            isinstance(watched, QDockWidget)
            and watched in self._panel_keys
            and self._panel_keys[watched] in self._hover_controls
        ):
            controls = self._hover_controls[self._panel_keys[watched]]
            if event.type() == QEvent.Type.Enter:
                if not self.property("selectingPanels"):
                    controls.reveal(True)
            elif event.type() == QEvent.Type.Leave:
                controls.defer_hover_check()
            elif event.type() == QEvent.Type.Hide:
                controls.dismiss()
            elif event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
                controls.reposition()
            if isinstance(event, QMouseEvent):
                if event.type() == QEvent.Type.MouseButtonPress:
                    if (
                        self.edit_mode
                        and self._panel_options[self._panel_keys[watched]][1]
                        and event.button() == Qt.MouseButton.LeftButton
                    ):
                        point = event.position().toPoint()
                        if (
                            0
                            <= point.y()
                            <= watched.style().pixelMetric(
                                QStyle.PixelMetric.PM_TitleBarHeight, None, watched
                            )
                            + 4
                        ):
                            self._drag_start = (
                                watched,
                                event.globalPosition().toPoint(),
                            )
                            return True
                elif event.type() == QEvent.Type.MouseMove and self._drag_start:
                    dock, start = self._drag_start
                    if event.buttons() & Qt.MouseButton.LeftButton:
                        if (
                            event.globalPosition().toPoint() - start
                        ).manhattanLength() >= QApplication.startDragDistance():
                            self._drag_start = None
                            self._start_panel_drag(dock)
                        return True
                elif (
                    event.type() == QEvent.Type.MouseButtonRelease and self._drag_start
                ):
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
        dock = self.panels.get(key)
        if dock is None:
            return
        hidden = dock.isHidden()
        was_hidden = self._panel_hidden.get(key, hidden)
        self._panel_hidden[key] = hidden
        if key in self._width_spacers:
            self._width_spacers[key].setVisible(not hidden)
        self._schedule_width_sync()
        self._configure_dock_corners()
        if self._restoring_panel_sizes:
            return
        # An inactive tab is not closed. Restoring geometry here would undo
        # tab selection, splitting and drag placement.
        if hidden == was_hidden:
            return
        if not visible:
            if not self._size_restore_pending:
                for panel_key, panel in self.panels.items():
                    if panel.isVisible() or panel is dock:
                        self._remember_panel_size(panel_key, panel)
                self._size_restore_pending = True
                self._restoring_panel_sizes = True
                self._visibility_timer.start(0)
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
        self._size_restore_timer.start(0)

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
                    dock
                    for key, dock in self.panels.items()
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
                        [
                            self._panel_sizes[self._panel_keys[dock]][dimension]
                            for dock in docks
                        ],
                        orientation,
                    )
        finally:
            self._restoring_panel_sizes = False
            self._size_restore_pending = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # A restored Qt dock state already owns the split proportions. Merely
        # showing the workspace must not replay independent pixel dimensions.

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
        self._pending_drop = None
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
            drag.deleteLater()
        pending = self._pending_drop
        self._pending_drop = None
        if pending is not None:
            self._apply_drop(*pending)

    def _drop_zones(self, bounds: QRect) -> dict[str, QRect]:
        w, h = bounds.width(), bounds.height()
        edge_x, edge_y = max(1, int(w * 0.28)), max(1, int(h * 0.25))
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
        source = source or self._drag_source
        if source is not None:
            peers = [
                peer for peer in self.tabifiedDockWidgets(source) if not peer.isHidden()
            ]
            bounds = self._panel_bounds(source)
            if peers and bounds is not None and bounds.contains(local):
                for relation, zone in self._drop_zones(bounds).items():
                    if zone.contains(local):
                        return (peers[0], relation) if relation != "tab" else None

        margin = max(24, min(48, self.width() // 30))
        if local.x() >= self.width() - margin:
            return None, "right"
        if local.x() < margin:
            return None, "left"
        if local.y() < margin:
            return None, "above"
        if local.y() >= self.height() - margin:
            return None, "below"
        for dock in reversed(tuple(self.panels.values())):
            if dock is source:
                continue
            bounds = self._panel_bounds(dock)
            if bounds is None or not bounds.contains(local):
                continue
            for relation, zone in self._drop_zones(bounds).items():
                if zone.contains(local):
                    return dock, relation
        return None

    def _apply_drop(
        self, source: QDockWidget, target: tuple[QDockWidget | None, str]
    ) -> bool:
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
            candidate
            for candidate in self.panels.values()
            if candidate is not source
            and candidate.isVisible()
            and not candidate.isFloating()
            and self.dockWidgetArea(candidate) == area
        ]
        if candidates:
            if relation in {"right", "below"}:
                anchor = max(
                    candidates,
                    key=lambda item: (
                        item.geometry().right()
                        if relation == "right"
                        else item.geometry().bottom()
                    ),
                )
            else:
                anchor = min(
                    candidates,
                    key=lambda item: (
                        item.geometry().left()
                        if relation == "left"
                        else item.geometry().top()
                    ),
                )
            return self.place_panel(anchor, source, relation)
        return self.move_panel_to_area(source, area)

    def _panel_bounds(self, dock: QDockWidget) -> QRect | None:
        """Layout visibility, independent of paint occlusion by the drag overlay.

        Qt moves inactive tabs outside the workspace. Do not use visibleRegion:
        a covering widget can empty it even for the active tab.
        """
        if not dock.isVisible() or dock.isFloating():
            return None
        bounds = QRect(dock.mapTo(self, QPoint()), dock.size())
        return bounds if bounds.intersects(self.rect()) else None

    def _drop_bounds(self, dock):
        for peer in [dock, *self.tabifiedDockWidgets(dock)]:
            bounds = self._panel_bounds(peer)
            if bounds is not None:
                return bounds.translated(-self._drop_overlay.pos())
        return QRect()

    def set_panel_size_locked(self, dock, locked):
        key = self._panel_keys[dock]
        content = dock.widget()
        if locked and key not in self._locked_sizes:
            self._unlocked_limits[key] = (content.minimumSize(), content.maximumSize())
            self._locked_sizes[key] = (content.width(), content.height())
            content.setFixedSize(content.size())
        elif not locked and key in self._locked_sizes:
            minimum, maximum = self._unlocked_limits.pop(key)
            content.setMinimumSize(minimum)
            content.setMaximumSize(maximum)
            self._locked_sizes.pop(key)
        self.layout_changed.emit()

    def drop_panel_at(self, dock: QDockWidget, global_position: QPoint) -> bool:
        """Resolve drag drops to a panel center, edge, or workspace outer edge."""
        if (
            dock not in self._panel_keys
            or not self._panel_options[self._panel_keys[dock]][1]
        ):
            return False
        target = self._drop_target(global_position, source=dock)
        return target is not None and self._apply_drop(dock, target)

    def _show_panel_menu(self, dock: QDockWidget, point) -> None:
        english = self.language == "en"
        menu = QMenu(dock)
        lock = menu.addAction("Lock panel size" if english else "Boyutunu sabitle")
        lock.setCheckable(True)
        lock.setChecked(self._panel_keys[dock] in self._locked_sizes)
        lock.toggled.connect(lambda checked: self.set_panel_size_locked(dock, checked))
        menu.addSeparator()
        move = menu.addMenu("Move Panel" if english else "Paneli taşı")
        for title, area in (
            ("Left" if english else "Sola", Qt.DockWidgetArea.LeftDockWidgetArea),
            ("Right" if english else "Sağa", Qt.DockWidgetArea.RightDockWidgetArea),
            ("Top" if english else "Yukarı", Qt.DockWidgetArea.TopDockWidgetArea),
            (
                "Bottom" if english else "Aşağıya",
                Qt.DockWidgetArea.BottomDockWidgetArea,
            ),
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
                if not dock.isFloating()
                else ("Dock" if english else "Pencereye sabitle"),
                lambda: dock.setFloating(not dock.isFloating()),
            )
        tab_targets = [
            (key, candidate)
            for key, candidate in self.panels.items()
            if candidate is not dock and not candidate.isHidden()
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
        menu.deleteLater()

    def _tabify_panel(self, target: QDockWidget, dock: QDockWidget) -> None:
        self.place_panel(target, dock, "tab")

    def move_panel_to_area(self, dock: QDockWidget, area: Qt.DockWidgetArea) -> bool:
        if dock not in self._panel_keys or not dock.allowedAreas() & area:
            return False
        if dock.isFloating():
            dock.setFloating(False)
        self._release_width_row(self._panel_keys[dock])
        self.addDockWidget(area, dock)
        self._schedule_width_sync()
        self.layout_changed.emit()
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
        self._release_width_row(target_key)
        self._release_width_row(dock_key)
        self._schedule_width_sync()
        target_size = target.size()
        if relation == "tab":
            self.move_panel_to_area(dock, area)
            self.tabifyDockWidget(target, dock)
            dock.show()
            dock.raise_()
            self.layout_changed.emit()
            return True
        # Qt splits a tabbed anchor by adding another tab instead. Detach its
        # peers temporarily and restore them after splitting the actual anchor.
        peers = [peer for peer in self.tabifiedDockWidgets(target) if peer is not dock]
        peer_visibility = {peer: not peer.isHidden() for peer in peers}
        for peer in peers:
            # addDockWidget clears Qt's saved tab placeholder too; plain
            # removeDockWidget leaves a singleton group marked as tabbed.
            self.addDockWidget(area, peer)
        self.addDockWidget(area, dock)
        if dock.isFloating():
            dock.setFloating(False)
        if relation in {"left", "right"}:
            orientation = Qt.Orientation.Horizontal
            before = relation == "left"
        else:
            orientation = Qt.Orientation.Vertical
            before = relation == "above"
        self.splitDockWidget(target, dock, orientation)
        if before:
            # The first split establishes the correct local anchor. Reversing
            # it must not first add the source at the area's unrelated edge.
            self.removeDockWidget(target)
            self.splitDockWidget(dock, target, orientation)
        for peer in peers:
            self.tabifyDockWidget(target, peer)
            peer.setVisible(peer_visibility[peer])
        target.show()
        target.raise_()
        dock.show()
        dock.raise_()
        span = (
            target_size.width()
            if orientation == Qt.Orientation.Horizontal
            else target_size.height()
        )
        self.resizeDocks([target, dock], [max(100, span // 2)] * 2, orientation)
        self.layout_changed.emit()
        return True

    def remove_panel(self, key: str) -> QWidget | None:
        if key in self._locked_sizes:
            self.set_panel_size_locked(self.panels[key], False)
        self._release_width_row(key)
        self._independent_widths.pop(key, None)
        self._width_limits.pop(key, None)
        dock = self.panels.pop(key, None)
        if dock is None:
            return None
        if self._panel_resize is not None and dock in (
            self._panel_resize.first,
            self._panel_resize.second,
        ):
            self._panel_resize = None
        if self._drag_source is dock:
            self._drag_source = None
            self._drop_overlay.hide()
        if (
            self._drop_overlay.active is not None
            and self._drop_overlay.active[0] is dock
        ):
            self._drop_overlay.active = None
        self._panel_keys.pop(dock, None)
        self._panel_options.pop(key, None)
        self._panel_default_areas.pop(key, None)
        self._panel_sizes.pop(key, None)
        self._quiet_titles.pop(key, None)
        controls = self._hover_controls.pop(key, None)
        if controls is not None:
            controls.dismiss()
            controls.deleteLater()
        self._panel_hidden.pop(key, None)
        dock.removeEventFilter(self)
        widget = dock.widget()
        if widget is not None:
            dock.setWidget(None)
        self.removeDockWidget(dock)
        dock.deleteLater()
        return widget

    def capture_layout(self) -> dict:
        """Capture dock topology without changing panel or window geometry."""
        for key, dock in self.panels.items():
            if dock.isVisible() and not self._restoring_panel_sizes:
                self._remember_panel_size(key, dock)
        return {
            "version": self.STATE_VERSION,
            "state": base64.b64encode(bytes(self.saveState(self.STATE_VERSION))).decode(
                "ascii"
            ),
            "panel_sizes": {
                key: {"width": width, "height": height}
                for key, (width, height) in self._panel_sizes.items()
            },
            "panel_widths": dict(self._independent_widths),
            "locked_sizes": dict(self._locked_sizes),
            "visible_panels": [
                key for key, dock in self.panels.items() if not dock.isHidden()
            ],
        }

    def save_layout(self, *, views: dict | None = None) -> bool:
        temporary = self.state_path.with_suffix(".tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self.capture_layout()
            payload["window_geometry"] = base64.b64encode(
                bytes(self.window().saveGeometry())
            ).decode("ascii")
            if views is not None:
                payload["views"] = views
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(self.state_path)
            return True
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def restore_layout(
        self, payload: dict | None = None, *, restore_window: bool = True
    ) -> bool:
        try:
            self._size_restore_timer.stop()
            self._visibility_timer.stop()
            self._size_restore_pending = False
            self._restoring_panel_sizes = False
            if payload is None:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            state_version = payload.get("version")
            if state_version not in (*self.LEGACY_STATE_VERSIONS, self.STATE_VERSION):
                return False
            state = QByteArray.fromBase64(payload["state"].encode("ascii"))
            for key in tuple(self._locked_sizes):
                self.set_panel_size_locked(self.panels[key], False)
            if restore_window and isinstance(payload.get("window_geometry"), str):
                self.window().restoreGeometry(
                    QByteArray.fromBase64(payload["window_geometry"].encode("ascii"))
                )
            legacy_docks = []
            renamed = []
            restored_sizes: dict[str, tuple[int, int]] = {}
            sizes = payload.get("panel_sizes", {})
            aliases = {"map.view": "map_autonomy.view"}
            if isinstance(sizes, dict):
                for key, size in sizes.items():
                    key = aliases.get(key, key)
                    key = next(
                        (
                            new_key
                            for new_key, dock in self.panels.items()
                            if dock.property("legacyPanelKey") == key
                        ),
                        key,
                    )
                    if key not in self.panels or not isinstance(size, dict):
                        continue
                    width, height = size.get("width"), size.get("height")
                    if (
                        isinstance(width, int)
                        and not isinstance(width, bool)
                        and width > 0
                        and isinstance(height, int)
                        and not isinstance(height, bool)
                        and height > 0
                    ):
                        restored_sizes[key] = (width, height)
            self._panel_sizes = restored_sizes
            widths = payload.get("panel_widths", {})
            for key in self._independent_widths:
                width = widths.get(key) if isinstance(widths, dict) else None
                if width is None and key in restored_sizes:
                    width = restored_sizes[key][0]
                if isinstance(width, int) and not isinstance(width, bool) and width > 0:
                    self._independent_widths[key] = width
            self._restoring_panel_sizes = True
            if state_version < self.STATE_VERSION:
                for key, dock in self.panels.items():
                    legacy_key = dock.property("legacyPanelKey")
                    if legacy_key:
                        renamed.append((dock, dock.objectName()))
                        if legacy_key == "map" and "map.view" in sizes:
                            legacy_key = "map.view"
                        dock.setObjectName(f"panel.{legacy_key}")
                for key in self._independent_widths:
                    spacer = QDockWidget(self)
                    spacer.setObjectName(f"panel-space.{key}")
                    spacer.setWidget(QWidget(spacer))
                    legacy_docks.append(spacer)
            try:
                restored = self.restoreState(state, state_version)
            finally:
                for dock, name in renamed:
                    dock.setObjectName(name)
                for spacer in legacy_docks:
                    self.removeDockWidget(spacer)
                    spacer.deleteLater()
            if restored:
                visible = payload.get("visible_panels")
                if isinstance(visible, list):
                    for key, dock in self.panels.items():
                        dock.setVisible(key in visible)
                self._configure_dock_corners()
                self.setTabPosition(
                    Qt.DockWidgetArea.AllDockWidgetAreas,
                    QTabWidget.TabPosition.North,
                )
                if state_version == 1:
                    self._restore_map_into_legacy_workspace()
                self._normalize_panel_areas()
                locks = payload.get("locked_sizes", {})
                for key, size in locks.items() if isinstance(locks, dict) else ():
                    if (
                        key in self.panels
                        and isinstance(size, (list, tuple))
                        and len(size) == 2
                        and all(
                            isinstance(n, int)
                            and not isinstance(n, bool)
                            and 0 < n < 16777215
                            for n in size
                        )
                    ):
                        content = self.panels[key].widget()
                        self._unlocked_limits[key] = (
                            content.minimumSize(),
                            content.maximumSize(),
                        )
                        self._locked_sizes[key] = tuple(size)
                        content.setFixedSize(*size)
                self._sync_independent_widths(rebind=True)
                self._panel_size_restore_enabled = True
                # Qt state already contains split ratios and tab selection.
                # Reapplying every dock's width AND height destroys nested
                # split proportions, especially across separate branches.
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
                dock
                for key, dock in self.panels.items()
                if key
                not in {
                    "map",
                    "flight",
                    "telemetry",
                    "commands",
                    "logs",
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
