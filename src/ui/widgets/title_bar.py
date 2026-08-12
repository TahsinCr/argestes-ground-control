from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from src.ui.themes.style import ThemePalette


class AppMark(QWidget):
    def __init__(
        self,
        icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon = icon or QIcon()
        self._accent = QColor("#f0a024")
        self._text = QColor("#1f1f1f")
        self.setFixedSize(24, 24)

    def set_theme(self, theme: ThemePalette) -> None:
        self._accent = QColor(theme.warning)
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._icon.isNull():
            pixmap = self._icon.pixmap(self.size())
            painter.drawPixmap(
                (self.width() - pixmap.width()) // 2,
                (self.height() - pixmap.height()) // 2,
                pixmap,
            )
            painter.end()
            return
        painter.setPen(QPen(self._accent.darker(120), 1))
        painter.setBrush(self._accent)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 5, 5)

        font = QFont("Noto Sans", 10, QFont.Weight.Black)
        painter.setFont(font)
        painter.setPen(self._text)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "K")
        painter.end()


class WindowControlButton(QPushButton):
    def __init__(self, kind: str, theme: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._theme = theme
        self._hovered = False
        self._pressed = False
        self._restore = False
        self.setFixedSize(QSize(44, 36))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFlat(True)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.update()

    def set_restore(self, restore: bool) -> None:
        self._restore = restore
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        if self._hovered:
            color = QColor(self._theme.danger if self._kind == "close" else self._theme.surface_hover)
            if self._pressed:
                color = color.darker(112)
            painter.fillRect(rect, color)

        icon_color = QColor("#ffffff" if self._kind == "close" and self._hovered else self._theme.text)
        pen = QPen(icon_color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        center_x = rect.center().x()
        center_y = rect.center().y()
        icon_left = center_x - 6
        icon_right = center_x + 6

        if self._kind == "minimize":
            painter.drawLine(icon_left, center_y + 4, icon_right, center_y + 4)
        elif self._kind == "maximize":
            if self._restore:
                painter.drawRect(center_x - 2, center_y - 7, 9, 8)
                painter.drawRect(center_x - 7, center_y - 2, 9, 8)
            else:
                painter.drawRect(center_x - 6, center_y - 5, 12, 10)
        else:
            painter.drawLine(center_x - 5, center_y - 5, center_x + 5, center_y + 5)
            painter.drawLine(center_x + 5, center_y - 5, center_x - 5, center_y + 5)
        painter.end()


class FramelessResizeController(QObject):
    def __init__(self, window: QWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._border = 10
        self._resizing = False
        self._resize_edges = None
        self._start_pos = QPoint()
        self._start_geometry = QRect()
        self._pending_resize_pos: QPoint | None = None
        self._cursor_active = False
        self._app = QApplication.instance()
        self._filter_installed = False
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(16)
        self._resize_timer.timeout.connect(self._flush_pending_resize)
        self._edge_handles = self._create_edge_handles()
        self._window.setMouseTracking(True)
        if self._app:
            self._app.installEventFilter(self)
            self._app.aboutToQuit.connect(self._remove_filter)
            self._filter_installed = True
        self._window.destroyed.connect(self._remove_filter)
        self._layout_edge_handles()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._window and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.WindowStateChange,
        }:
            self._layout_edge_handles()
        if not isinstance(obj, QWidget) or obj.window() is not self._window:
            if self._cursor_active and event.type() in {
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.WindowDeactivate,
            }:
                self._clear_cursor()
            return False
        if self._window.isMaximized() or self._window.isFullScreen():
            self._clear_cursor()
            return False
        if event.type() == QEvent.Type.MouseMove:
            if self._resizing:
                self._queue_resize(event.globalPosition().toPoint())
                return True
            self._set_cursor(self._edge_at(event.globalPosition().toPoint()))
        elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            edges = self._edge_at(event.globalPosition().toPoint())
            if edges:
                if self._start_native_resize(edges):
                    self._clear_cursor()
                    return True
                self._begin_manual_resize(edges, event.globalPosition().toPoint())
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._finish_manual_resize(event.globalPosition().toPoint())
            self._clear_cursor()
        elif event.type() in {QEvent.Type.Leave, QEvent.Type.WindowDeactivate, QEvent.Type.Hide}:
            if self._resizing and event.type() != QEvent.Type.Leave:
                self._finish_manual_resize()
            if not self._resizing:
                self._clear_cursor()
        return False

    def _start_native_resize(self, edges: Qt.Edge) -> bool:
        # Qt's XCB backend can report a successful system resize even when the
        # window manager never starts the operation. Manual geometry updates
        # are reliable on X11; compositors that restrict them keep the native
        # path.
        platform = str(self._app.platformName()).lower() if self._app else ""
        if platform in {"xcb", "offscreen", "minimal"}:
            return False
        handle = self._window.windowHandle()
        start_resize = getattr(handle, "startSystemResize", None) if handle else None
        return bool(start_resize and start_resize(edges))

    def _begin_manual_resize(self, edges: Qt.Edge, global_pos: QPoint) -> None:
        self._resizing = True
        self._resize_edges = edges
        self._start_pos = QPoint(global_pos)
        self._start_geometry = QRect(self._window.geometry())
        self._pending_resize_pos = None

    def _edge_at(self, global_pos: QPoint):
        rect = self._window.frameGeometry()
        if not rect.adjusted(-self._border, -self._border, self._border, self._border).contains(global_pos):
            return None
        edges = None
        if abs(global_pos.x() - rect.left()) <= self._border:
            edges = Qt.Edge.LeftEdge
        elif abs(global_pos.x() - rect.right()) <= self._border:
            edges = Qt.Edge.RightEdge
        if abs(global_pos.y() - rect.top()) <= self._border:
            edges = Qt.Edge.TopEdge if edges is None else edges | Qt.Edge.TopEdge
        elif abs(global_pos.y() - rect.bottom()) <= self._border:
            edges = Qt.Edge.BottomEdge if edges is None else edges | Qt.Edge.BottomEdge
        return edges

    def _set_cursor(self, edges) -> None:
        if not edges:
            self._clear_cursor()
            return
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            if edges in (Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
                self._window.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self._window.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif horizontal:
            self._window.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self._window.setCursor(Qt.CursorShape.SizeVerCursor)
        self._cursor_active = True

    def _clear_cursor(self) -> None:
        if not self._cursor_active:
            return
        # unsetCursor emits a synchronous CursorChange event. Keep the guard
        # active until the global eventFilter has observed that event, or
        # _clear_cursor will be called recursively.
        self._cursor_active = False
        self._window.unsetCursor()

    def _remove_filter(self, *_args) -> None:
        if self._app and self._filter_installed:
            self._app.removeEventFilter(self)
            self._filter_installed = False
        self._resize_timer.stop()
        self._pending_resize_pos = None
        self._cursor_active = False

    def _queue_resize(self, global_pos: QPoint) -> None:
        self._pending_resize_pos = QPoint(global_pos)
        if not self._resize_timer.isActive():
            self._resize_timer.start()

    def _flush_pending_resize(self) -> None:
        position = self._pending_resize_pos
        self._pending_resize_pos = None
        if self._resizing and position is not None:
            self._resize_to(position)

    def _finish_manual_resize(self, global_pos: QPoint | None = None) -> None:
        if not self._resizing:
            return
        self._resize_timer.stop()
        position = global_pos or self._pending_resize_pos
        self._pending_resize_pos = None
        if position is not None:
            self._resize_to(position)
        self._resizing = False
        self._resize_edges = None

    def _resize_to(self, global_pos: QPoint) -> None:
        if not self._resize_edges:
            return
        delta = global_pos - self._start_pos
        geometry = QRect(self._start_geometry)
        minimum = self._window.minimumSize()
        if self._resize_edges & Qt.Edge.LeftEdge:
            next_left = min(geometry.right() - minimum.width(), geometry.left() + delta.x())
            geometry.setLeft(next_left)
        if self._resize_edges & Qt.Edge.RightEdge:
            geometry.setRight(max(geometry.left() + minimum.width(), geometry.right() + delta.x()))
        if self._resize_edges & Qt.Edge.TopEdge:
            next_top = min(geometry.bottom() - minimum.height(), geometry.top() + delta.y())
            geometry.setTop(next_top)
        if self._resize_edges & Qt.Edge.BottomEdge:
            geometry.setBottom(max(geometry.top() + minimum.height(), geometry.bottom() + delta.y()))
        self._window.setGeometry(geometry)

    def _create_edge_handles(self) -> list[tuple[QWidget, Qt.Edge]]:
        edge_sets = (
            Qt.Edge.TopEdge,
            Qt.Edge.BottomEdge,
            Qt.Edge.LeftEdge,
            Qt.Edge.RightEdge,
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
            Qt.Edge.TopEdge | Qt.Edge.RightEdge,
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
        )
        handles: list[tuple[QWidget, Qt.Edge]] = []
        for edges in edge_sets:
            handle = QWidget(self._window)
            handle.setObjectName("FramelessResizeHandle")
            handle.setStyleSheet("background: transparent; border: none;")
            handle.setMouseTracking(True)
            handle.setCursor(self._cursor_for_edges(edges))
            handles.append((handle, edges))
        return handles

    def _layout_edge_handles(self) -> None:
        hidden = self._window.isMaximized() or self._window.isFullScreen()
        width = self._window.width()
        height = self._window.height()
        border = self._border
        geometries = (
            QRect(border, 0, max(0, width - 2 * border), border),
            QRect(border, max(0, height - border), max(0, width - 2 * border), border),
            QRect(0, border, border, max(0, height - 2 * border)),
            QRect(max(0, width - border), border, border, max(0, height - 2 * border)),
            QRect(0, 0, border, border),
            QRect(max(0, width - border), 0, border, border),
            QRect(0, max(0, height - border), border, border),
            QRect(max(0, width - border), max(0, height - border), border, border),
        )
        for (handle, _edges), geometry in zip(self._edge_handles, geometries):
            handle.setGeometry(geometry)
            handle.setVisible(not hidden)
            if not hidden:
                handle.raise_()

    @staticmethod
    def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape:
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            if edges in (
                Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
            ):
                return Qt.CursorShape.SizeFDiagCursor
            return Qt.CursorShape.SizeBDiagCursor
        if horizontal:
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeVerCursor


class AppTitleBar(QWidget):
    def __init__(self, window: QWidget, theme: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._theme = theme
        self._drag_offset: QPoint | None = None
        self.setObjectName("AppTitleBar")
        self.setFixedHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._resize_controller = FramelessResizeController(window, self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(9)

        self.mark = AppMark(window.windowIcon(), self)
        self.mark.set_theme(theme)
        layout.addWidget(self.mark)

        self.title_label = QLabel("", self)
        self.title_label.setObjectName("WindowTitleLabel")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.title_label, 1)

        self.minimize_button = self._control_button("minimize")
        self.maximize_button = self._control_button("maximize")
        self.close_button = self._control_button("close")

        self.minimize_button.clicked.connect(self._window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(self._window.close)

        controls = QWidget(self)
        controls.setObjectName("WindowControls")
        controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(0)
        controls_layout.addWidget(self.minimize_button)
        controls_layout.addWidget(self.maximize_button)
        controls_layout.addWidget(self.close_button)
        layout.addWidget(controls)

    def _control_button(self, kind: str) -> WindowControlButton:
        return WindowControlButton(kind, self._theme, self)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.mark.set_theme(theme)
        self.minimize_button.set_theme(theme)
        self.maximize_button.set_theme(theme)
        self.close_button.set_theme(theme)

    def sync_window_state(self) -> None:
        self.maximize_button.set_restore(self._window.isMaximized())

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync_window_state()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._window.isMaximized():
                handle = self._window.windowHandle()
                start_system_move = getattr(handle, "startSystemMove", None) if handle else None
                if start_system_move and start_system_move():
                    self._drag_offset = None
                    event.accept()
                    return
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_offset is not None:
            if self._window.isMaximized():
                self._window.showNormal()
                self.sync_window_state()
                self._drag_offset = QPoint(self._window.width() // 2, self.height() // 2)
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)
