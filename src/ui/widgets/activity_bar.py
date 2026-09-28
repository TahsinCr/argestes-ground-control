from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, pyqtSignal, Qt
from PyQt6.QtGui import QContextMenuEvent, QPainter, QPalette, QPen, QPolygonF
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
        elif self.key.startswith("extension:"):
            painter.drawRoundedRect(QRectF(x + 2, y + 2, 16, 16), 2, 2)
            painter.drawLine(QPointF(x + 5, y + 7), QPointF(x + 15, y + 7))
        elif self.key == "logs":
            for offset, width in ((4, 13), (9, 16), (14, 11)):
                painter.drawEllipse(QPointF(x + 3, y + offset), 0.8, 0.8)
                painter.drawLine(QPointF(x + 7, y + offset), QPointF(x + width, y + offset))
        elif self.key == "parameters":
            for offset, knob in ((4, 10), (10, 5), (16, 13)):
                painter.drawLine(QPointF(x + 3, y + offset), QPointF(x + 17, y + offset))
                painter.drawEllipse(QPointF(x + knob, y + offset), 2, 2)
        elif self.key.startswith("panel:"):
            panel_key = self.key.removeprefix("panel:")
            if panel_key == "map":
                painter.drawPolygon(QPolygonF([
                    QPointF(x + 2, y + 5), QPointF(x + 8, y + 3),
                    QPointF(x + 14, y + 5), QPointF(x + 18, y + 3),
                    QPointF(x + 18, y + 16), QPointF(x + 14, y + 18),
                    QPointF(x + 8, y + 16), QPointF(x + 2, y + 18),
                ]))
                painter.drawLine(QPointF(x + 8, y + 3), QPointF(x + 8, y + 16))
                painter.drawLine(QPointF(x + 14, y + 5), QPointF(x + 14, y + 18))
            elif panel_key.startswith("camera"):
                painter.drawRoundedRect(QRectF(x + 2, y + 6, 16, 12), 2, 2)
                painter.drawRect(QRectF(x + 6, y + 3, 6, 3))
                painter.drawEllipse(QPointF(x + 10, y + 12), 3, 3)
            else:
                painter.drawRoundedRect(QRectF(x + 2, y + 2, 16, 16), 2, 2)
                painter.drawLine(QPointF(x + 5, y + 7), QPointF(x + 15, y + 7))
                painter.drawLine(QPointF(x + 5, y + 11), QPointF(x + 15, y + 11))
        painter.end()


class ActivityBar(QWidget):
    """Compact activity rail for showing and arranging the main work panels."""

    flight_sidebar_toggled = pyqtSignal(bool)
    bottom_panel_toggled = pyqtSignal(str, bool)
    bottom_panel_side_by_side_requested = pyqtSignal(str)
    sidebar_view_requested = pyqtSignal(str)
    panel_visibility_requested = pyqtSignal(str, bool)

    _BOTTOM_PANELS = ("logs", "parameters")

    def __init__(self, language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        self.setObjectName("ActivityBar")
        self.setFixedWidth(48)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        self._layout = layout
        self.extension_view_buttons: dict[str, _ActivityButton] = {}
        self.panel_buttons: dict[str, _ActivityButton] = {}

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

    def _make_button(self, key: str, *, checkable: bool = True) -> _ActivityButton:
        button = _ActivityButton(
            key, self, checkable=checkable, language=self.language
        )
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
            for button in self.extension_view_buttons.values():
                button.setChecked(False)
        self.sidebar_view_requested.emit("extensions" if checked else "")

    def _on_flight_clicked(self, checked: bool) -> None:
        if checked:
            self.extensions_button.setChecked(False)
            for button in self.extension_view_buttons.values():
                button.setChecked(False)
        self.sidebar_view_requested.emit("flight" if checked else "")

    def add_extension_view(self, identifier: str, title: str) -> None:
        button = _ActivityButton(f"extension:{identifier}", self, language=self.language)
        button.setAccessibleName(title)
        button.setToolTip(title)
        self.extension_view_buttons[identifier] = button
        self._layout.insertWidget(2 + len(self.extension_view_buttons) - 1, button)
        button.clicked.connect(lambda checked, key=identifier: self._on_extension_view_clicked(key, checked))

    def remove_extension_view(self, identifier: str) -> None:
        button = self.extension_view_buttons.pop(identifier, None)
        if button is not None:
            self._layout.removeWidget(button)
            button.hide()
            button.deleteLater()

    def add_panel_button(self, key: str, title: str) -> _ActivityButton:
        button = self.panel_buttons.get(key)
        if button is not None:
            self.set_panel_title(key, title)
            return button
        button = _ActivityButton(f"panel:{key}", self, language=self.language)
        self.panel_buttons[key] = button
        self.set_panel_title(key, title)
        button.clicked.connect(
            lambda checked, panel_key=key: self.panel_visibility_requested.emit(
                panel_key, checked
            )
        )
        index = 2 + len(self.extension_view_buttons) + len(self.panel_buttons) - 1
        self._layout.insertWidget(index, button)
        return button

    def remove_panel_button(self, key: str) -> None:
        button = self.panel_buttons.pop(key, None)
        if button is not None:
            self._layout.removeWidget(button)
            button.hide()
            button.deleteLater()

    def set_panel_title(self, key: str, title: str) -> None:
        button = self.panel_buttons.get(key)
        if button is not None:
            button.setAccessibleName(title)
            button.setToolTip(title)

    def set_panel_checked(self, key: str, checked: bool) -> None:
        button = self.panel_buttons.get(key)
        if button is not None:
            button.setChecked(checked)

    def _on_extension_view_clicked(self, identifier: str, checked: bool) -> None:
        if checked:
            self.sidebar_button.setChecked(False)
            self.extensions_button.setChecked(False)
            for key, button in self.extension_view_buttons.items():
                if key != identifier:
                    button.setChecked(False)
        self.sidebar_view_requested.emit(identifier if checked else "")

    def set_bottom_panel_checked(self, key: str, checked: bool) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(checked)

    def set_language(self, language: str) -> None:
        self.language = language
        for key, button in (
            ("flight_panels", self.sidebar_button),
            ("extensions", self.extensions_button),
            *self.buttons.items(),
        ):
            label = t(f"activity.{key}", language)
            button.language = language
            button.setAccessibleName(label)
            button.setToolTip(label)
