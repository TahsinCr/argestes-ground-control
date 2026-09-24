from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, pyqtSignal, Qt
from PyQt6.QtGui import QPainter, QPainterPath, QPalette, QPen
from PyQt6.QtWidgets import QButtonGroup, QToolButton, QVBoxLayout, QWidget

from src.ui.i18n import t


class _ActivityButton(QToolButton):
    def __init__(
        self, key: str, parent: QWidget | None = None, *, checkable: bool = True
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("ActivityBarButton")
        self.setFixedSize(48, 46)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.ButtonText), 1.7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x = (self.width() - 20) / 2
        y = (self.height() - 20) / 2

        if self.key == "explorer":
            painter.drawRoundedRect(QRectF(x + 5, y + 2, 12, 16), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(x + 2, y + 5, 12, 15), 1.5, 1.5)
        elif self.key == "search":
            painter.drawEllipse(QPointF(x + 8, y + 8), 5.5, 5.5)
            painter.drawLine(QPointF(x + 12, y + 12), QPointF(x + 18, y + 18))
        elif self.key == "run":
            path = QPainterPath()
            path.moveTo(x + 5, y + 2)
            path.lineTo(x + 18, y + 10)
            path.lineTo(x + 5, y + 18)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawRoundedRect(QRectF(x + 2, y + 1, 2, 18), 1, 1)
        elif self.key == "source_control":
            painter.drawLine(QPointF(x + 5, y + 4), QPointF(x + 5, y + 16))
            painter.drawLine(QPointF(x + 5, y + 10), QPointF(x + 15, y + 4))
            for cx, cy in ((5, 4), (5, 16), (15, 4)):
                painter.drawEllipse(QPointF(x + cx, y + cy), 2.2, 2.2)
        elif self.key == "extensions":
            for cx, cy in ((3, 3), (11, 3), (3, 11), (11, 11)):
                painter.drawRoundedRect(QRectF(x + cx, y + cy, 6, 6), 1, 1)
        elif self.key == "account":
            painter.drawEllipse(QPointF(x + 10, y + 6), 3.2, 3.2)
            path = QPainterPath()
            path.moveTo(x + 3, y + 19)
            path.cubicTo(x + 3, y + 12, x + 17, y + 12, x + 17, y + 19)
            painter.drawPath(path)
        elif self.key == "settings":
            painter.drawEllipse(QPointF(x + 10, y + 10), 6.5, 6.5)
            painter.drawEllipse(QPointF(x + 10, y + 10), 2.2, 2.2)
            for angle in range(0, 360, 45):
                painter.save()
                painter.translate(x + 10, y + 10)
                painter.rotate(angle)
                painter.drawLine(QPointF(0, -9), QPointF(0, -6.5))
                painter.restore()
        painter.end()


class ActivityBar(QWidget):
    """Compact left navigation rail; content views can be attached incrementally."""

    activity_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()

    _ACTIVITIES = ("explorer", "search", "run", "source_control", "extensions")

    def __init__(self, language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        self.setObjectName("ActivityBar")
        self.setFixedWidth(48)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, _ActivityButton] = {}

        for key in self._ACTIVITIES:
            button = self._make_button(key)
            self._group.addButton(button)
            layout.addWidget(button)
            self.buttons[key] = button
            button.clicked.connect(lambda checked=False, name=key: self.activity_requested.emit(name))

        self.buttons["explorer"].setChecked(True)
        layout.addStretch(1)
        self.account_button = self._make_button("account")
        self.settings_button = self._make_button("settings")
        layout.addWidget(self.account_button)
        layout.addWidget(self.settings_button)
        self.account_button.clicked.connect(
            lambda: self.activity_requested.emit("account")
        )
        self.settings_button.clicked.connect(self.settings_requested.emit)

    def _make_button(self, key: str) -> _ActivityButton:
        button = _ActivityButton(key, self, checkable=key in self._ACTIVITIES)
        button.setAccessibleName(t(f"activity.{key}", self.language))
        button.setToolTip(t(f"activity.{key}", self.language))
        return button

    def set_language(self, language: str) -> None:
        self.language = language
        for key, button in self.buttons.items():
            label = t(f"activity.{key}", language)
            button.setAccessibleName(label)
            button.setToolTip(label)
        for key, button in (("account", self.account_button), ("settings", self.settings_button)):
            label = t(f"activity.{key}", language)
            button.setAccessibleName(label)
            button.setToolTip(label)
