from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui.themes.style import ThemePalette


class PanelFrame(QFrame):
    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PanelFrame")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self.header = QWidget(self)
        self.header.setObjectName("PanelHeader")
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(8, 6, 8, 6)
        header_layout.setSpacing(1)

        header_row = QWidget(self.header)
        row_layout = QHBoxLayout(header_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        self.title_label = QLabel(title, header_row)
        self.title_label.setObjectName("PanelTitle")
        self.trailing_box = QWidget(header_row)
        self.trailing_layout = QHBoxLayout(self.trailing_box)
        self.trailing_layout.setContentsMargins(0, 0, 0, 0)
        self.trailing_layout.setSpacing(6)
        row_layout.addWidget(self.title_label)
        row_layout.addStretch(1)
        row_layout.addWidget(self.trailing_box)

        self.subtitle_label = QLabel(subtitle, self.header)
        self.subtitle_label.setObjectName("PanelSubtitle")
        self.subtitle_label.setWordWrap(True)

        header_layout.addWidget(header_row)
        header_layout.addWidget(self.subtitle_label)
        self._layout.addWidget(self.header)

        self.body = QWidget(self)
        self.body.setObjectName("PanelBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(8, 6, 8, 8)
        self.body_layout.setSpacing(4)
        self._layout.addWidget(self.body)

    def set_title(self, title: str, subtitle: str) -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))

    def add_header_widget(self, widget: QWidget) -> None:
        self.trailing_layout.addWidget(widget)


class MetricCard(QFrame):
    def __init__(
        self,
        title: str,
        value: str = "--",
        detail: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(1)

        value_row = QWidget(self)
        value_layout = QHBoxLayout(value_row)
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.setSpacing(6)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("MetricTitle")
        self.value_label = QLabel(value, self)
        self.value_label.setObjectName("MetricValue")
        self.detail_label = QLabel(detail, self)
        self.detail_label.setObjectName("MetricDetail")

        value_layout.addWidget(self.title_label)
        value_layout.addWidget(self.value_label, 1, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(value_row)
        layout.addWidget(self.detail_label)
        self.detail_label.setVisible(bool(detail))

    def update_value(self, value: str, detail: str = "") -> None:
        self.value_label.setText(value)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)


class StatusChip(QFrame):
    clicked = pyqtSignal()

    COLORS = {
        "success": "success",
        "warning": "warning",
        "danger": "danger",
        "neutral": "neutral",
    }

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme: ThemePalette | None = None
        self._state = "neutral"
        self._label = label
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(5)
        self.dot = QLabel("●", self)
        self.text_label = QLabel(label, self)
        self.dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.dot)
        layout.addWidget(self.text_label)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Bağlantı ayrıntıları ve yeniden bağlanma")

    @property
    def state(self) -> str:
        return self._state

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self._restyle()

    def set_label(self, text: str) -> None:
        self._label = text
        self.text_label.setText(text)

    def set_state(self, state: str) -> None:
        self._state = state
        self._restyle()

    def _restyle(self) -> None:
        if not self._theme:
            return
        colors = {
            "success": self._theme.success,
            "warning": self._theme.warning,
            "danger": self._theme.danger,
            "neutral": self._theme.muted_text,
        }
        color = colors.get(self._state, self._theme.muted_text)
        self.setStyleSheet(
            f"""
            StatusChip {{
                background: {self._theme.surface_alt};
                border: 1px solid {self._theme.border};
                border-radius: 4px;
            }}
            StatusChip:hover {{
                background: {self._theme.surface_hover};
                border-color: {self._theme.accent};
            }}
            QLabel {{
                color: {color};
                background: transparent;
                font-size: 10px;
                font-weight: 600;
            }}
            """
        )
        self.dot.setStyleSheet(f"color: {color};")
        self.text_label.setStyleSheet(f"color: {color};")


class SignalMeter(QFrame):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme: ThemePalette | None = None
        self._value = 0
        self._label = label
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)
        self.label = QLabel(label, self)
        self.value = QLabel("0%", self)
        self.detail = QLabel("", self)
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self._restyle()

    def set_label(self, text: str) -> None:
        self._label = text
        self.label.setText(text)

    def set_value(self, value: int, detail: str = "") -> None:
        self._value = max(0, min(100, int(value)))
        self.value.setText(f"{self._value}%")
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self._restyle()

    def set_unavailable(self, detail: str = "") -> None:
        self._value = 0
        self.value.setText("--%")
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self._restyle()

    def _level_color(self) -> str:
        if not self._theme:
            return "#888888"
        if self._value <= 0:
            return self._theme.danger
        if self._value < 40:
            return "#d18616"
        if self._value < 70:
            return self._theme.warning
        return self._theme.success

    def _restyle(self) -> None:
        if not self._theme:
            return
        color = self._level_color()
        self.setStyleSheet(
            f"""
            SignalMeter {{
                background: transparent;
                border: none;
            }}
            QLabel {{
                background: transparent;
                color: {self._theme.text};
                font-size: 10px;
                font-weight: 700;
            }}
            """
        )
        self.value.setStyleSheet(
            f"""
            color: {color};
            background: rgba(255,255,255,0.035);
            border: 1px solid {color};
            border-radius: 4px;
            padding: 1px 6px;
            font-weight: 800;
            """
        )
        self.detail.setStyleSheet(
            f"color: {self._theme.muted_text}; background: transparent; font-size: 10px;"
        )


class BatteryMeter(SignalMeter):
    """Alt durum çubuğu için kompakt, eşik renkli batarya göstergesi."""

    def set_battery(
        self,
        percent: int,
        voltage: float,
        current_amps: float | None = None,
        consumed_mah: int | None = None,
    ) -> None:
        details = [f"{voltage:0.1f} V"] if voltage > 0.0 else []
        if current_amps is not None:
            details.append(f"{current_amps:0.1f} A")
        self.set_value(percent, " · ".join(details))

        tooltip = [f"Batarya: %{max(0, min(100, int(percent)))}"]
        tooltip.extend(details)
        if consumed_mah is not None:
            tooltip.append(f"Tüketim: {consumed_mah} mAh")
        self.setToolTip("\n".join(tooltip))

    def set_unavailable(self, detail: str = "") -> None:
        super().set_unavailable(detail)
        self.setToolTip("Batarya telemetrisi bekleniyor")

    def _level_color(self) -> str:
        if not self._theme:
            return "#888888"
        if self._value <= 15:
            return self._theme.danger
        if self._value < 30:
            return self._theme.warning
        return self._theme.success


def action_button(text: str, role: str = "") -> QPushButton:
    button = QPushButton(text)
    if role:
        button.setProperty("role", role)
        button.style().polish(button)
    return button


def compact_button(text: str, role: str = "") -> QPushButton:
    button = action_button(text, role)
    button.setProperty("compact", "true")
    button.style().polish(button)
    return button


def horizontal_row(*widgets: QWidget, stretch: bool = False) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch(1)
    return row
