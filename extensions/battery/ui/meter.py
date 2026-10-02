from __future__ import annotations
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import QWidget
from src.ui.widgets.common import SignalMeter
from src.ui.i18n import t


class BatteryMeter(SignalMeter):
    """Compact threshold-colored battery indicator for the bottom status bar."""

    clicked = pyqtSignal()

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self._language = "tr"
        self._settings_hint = ""
        self._alarm_active = False
        self._alarm_phase = False
        self._alarm_timer = QTimer(self)
        self._alarm_timer.setInterval(450)
        self._alarm_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._alarm_timer.timeout.connect(self._toggle_alarm_phase)
        for child in (self.label, self.value, self.detail):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @property
    def alarm_active(self) -> bool:
        return self._alarm_active

    def set_language(self, language: str) -> None:
        self._language = language
        if self._value <= 0:
            tooltip = self._tooltip_with_settings_hint(
                [t("battery.tooltip.waiting", self._language)]
            )
            if tooltip != self.toolTip():
                self.setToolTip(tooltip)

    def set_settings_hint(self, text: str) -> None:
        previous_hint = self._settings_hint
        self._settings_hint = str(text).strip()
        current_lines = [
            line
            for line in self.toolTip().splitlines()
            if line and line != previous_hint
        ]
        self.setToolTip(self._tooltip_with_settings_hint(current_lines))

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

        tooltip = [
            t(
                "battery.tooltip.summary",
                self._language,
                percent=max(0, min(100, int(percent))),
            )
        ]
        tooltip.extend(details)
        if consumed_mah is not None:
            tooltip.append(
                t(
                    "battery.tooltip.consumed",
                    self._language,
                    mah=consumed_mah,
                )
            )
        tooltip_text = self._tooltip_with_settings_hint(tooltip)
        if tooltip_text != self.toolTip():
            self.setToolTip(tooltip_text)

    def set_alarm(self, active: bool) -> None:
        normalized = bool(active)
        if normalized == self._alarm_active:
            return
        self._alarm_active = normalized
        self.value.setProperty("batteryAlarm", normalized)
        if normalized:
            self._alarm_phase = True
            self._alarm_timer.start()
        else:
            self._alarm_timer.stop()
            self._alarm_phase = False
        self._restyle()

    def _toggle_alarm_phase(self) -> None:
        if not self._alarm_active:
            return
        self._alarm_phase = not self._alarm_phase
        self._restyle()

    def set_unavailable(self, detail: str = "") -> None:
        self.set_alarm(False)
        super().set_unavailable(detail)
        tooltip = self._tooltip_with_settings_hint(
            [t("battery.tooltip.waiting", self._language)]
        )
        if tooltip != self.toolTip():
            self.setToolTip(tooltip)

    def _tooltip_with_settings_hint(self, lines: list[str]) -> str:
        values = [line for line in lines if line and line != self._settings_hint]
        if self._settings_hint:
            values.append(self._settings_hint)
        return "\n".join(values)

    def _level_color(self) -> str:
        if not self._theme:
            return "#888888"
        if self._value <= 15:
            return self._theme.danger
        if self._value < 30:
            return self._theme.warning
        return self._theme.success

    def _restyle(self) -> None:
        super()._restyle()
        if not self._theme or not self._alarm_active:
            return
        danger = QColor(self._theme.danger)
        if self._alarm_phase:
            color = self._theme.danger
            border = self._theme.danger
            background = (
                f"rgba({danger.red()}, {danger.green()}, {danger.blue()}, 0.28)"
            )
        else:
            color = self._theme.muted_text
            border = "transparent"
            background = "transparent"
        self.value.setStyleSheet(
            f"""
            color: {color};
            background: {background};
            border: 1px solid {border};
            border-radius: 4px;
            padding: 1px 6px;
            font-weight: 900;
            """
        )
