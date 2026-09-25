from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, pyqtSignal, Qt
from PyQt6.QtGui import QContextMenuEvent, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QMenu, QToolButton, QVBoxLayout, QWidget

from src.ui.i18n import t


class _ActivityButton(QToolButton):
    side_by_side_requested = pyqtSignal(str)

    def __init__(
        self,
        key: str,
        parent: QWidget | None = None,
        *,
        checkable: bool = True,
        language: str = "tr",
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.language = language
        self.setObjectName("ActivityBarButton")
        self.setFixedSize(48, 46)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self.key not in {"logs", "parameters"} or self.isChecked():
            event.ignore()
            return
        menu = QMenu(self)
        action = menu.addAction(t("activity.open_beside", self.language))
        if menu.exec(event.globalPos()) is action:
            self.setChecked(True)
            self.side_by_side_requested.emit(self.key)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.ButtonText), 1.7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x = (self.width() - 20) / 2
        y = (self.height() - 20) / 2

        if self.key == "flight_panels":
            painter.drawRoundedRect(QRectF(x + 2, y + 2, 16, 16), 1.5, 1.5)
            painter.drawLine(QPointF(x + 8, y + 2), QPointF(x + 8, y + 18))
        elif self.key == "extensions":
            for left, top in ((3, 3), (12, 3), (3, 12), (12, 12)):
                painter.drawRoundedRect(QRectF(x + left, y + top, 6, 6), 1, 1)
        elif self.key == "logs":
            for offset, width in ((4, 13), (9, 16), (14, 11)):
                painter.drawEllipse(QPointF(x + 3, y + offset), 0.8, 0.8)
                painter.drawLine(QPointF(x + 7, y + offset), QPointF(x + width, y + offset))
        elif self.key == "parameters":
            for offset, knob in ((4, 10), (10, 5), (16, 13)):
                painter.drawLine(QPointF(x + 3, y + offset), QPointF(x + 17, y + offset))
                painter.drawEllipse(QPointF(x + knob, y + offset), 2, 2)
        painter.end()


class ActivityBar(QWidget):
    """Compact activity rail for showing and arranging the main work panels."""

    flight_sidebar_toggled = pyqtSignal(bool)
    bottom_panel_toggled = pyqtSignal(str, bool)
    bottom_panel_side_by_side_requested = pyqtSignal(str)
    sidebar_view_requested = pyqtSignal(str)

    _BOTTOM_PANELS = ("logs", "parameters")

    def __init__(self, language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        self.setObjectName("ActivityBar")
        self.setFixedWidth(48)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        self.sidebar_button = self._make_button("flight_panels")
        self.sidebar_button.setChecked(True)
        layout.addWidget(self.sidebar_button)
        self.sidebar_button.clicked.connect(self.flight_sidebar_toggled.emit)
        self.sidebar_button.clicked.connect(self._on_flight_clicked)
        self.extensions_button = self._make_button("extensions")
        layout.addWidget(self.extensions_button)
        self.extensions_button.clicked.connect(self._on_extensions_clicked)

        layout.addStretch(1)
        self.buttons: dict[str, _ActivityButton] = {}
        self.buttons["logs"] = self._make_button("logs")
        self.buttons["parameters"] = self._make_button("parameters")
        self.buttons["logs"].setChecked(True)
        for key in self._BOTTOM_PANELS:
            button = self.buttons[key]
            button.side_by_side_requested.connect(
                self.bottom_panel_side_by_side_requested.emit
            )
            button.clicked.connect(
                lambda checked=False, name=key: self._on_panel_clicked(name, checked)
            )
            layout.addWidget(button)

    def _make_button(self, key: str) -> _ActivityButton:
        button = _ActivityButton(key, self, language=self.language)
        label = t(f"activity.{key}", self.language)
        button.setAccessibleName(label)
        button.setToolTip(label)
        return button

    def _on_panel_clicked(self, key: str, checked: bool) -> None:
        if checked:
            other_key = "parameters" if key == "logs" else "logs"
            self.buttons[other_key].setChecked(False)
        self.bottom_panel_toggled.emit(key, checked)

    def _on_extensions_clicked(self, checked: bool) -> None:
        if checked:
            self.sidebar_button.setChecked(False)
        self.sidebar_view_requested.emit("extensions" if checked else "")

    def _on_flight_clicked(self, checked: bool) -> None:
        if checked:
            self.extensions_button.setChecked(False)
        self.sidebar_view_requested.emit("flight" if checked else "")

    def set_bottom_panel_checked(self, key: str, checked: bool) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(checked)

    def set_language(self, language: str) -> None:
        self.language = language
        for key, button in (("flight_panels", self.sidebar_button), ("extensions", self.extensions_button), *self.buttons.items()):
            label = t(f"activity.{key}", language)
            button.language = language
            button.setAccessibleName(label)
            button.setToolTip(label)
