from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette


class BatterySettingsPopup(QDialog):
    """Compact editor opened from the bottom-bar battery indicator."""

    save_requested = pyqtSignal(dict)
    settings_requested = pyqtSignal()

    def __init__(
        self,
        theme: ThemePalette,
        parent: QWidget | None = None,
        *,
        language: str = "tr",
    ) -> None:
        super().__init__(
            parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setObjectName("BatterySettingsPopup")
        self.setMinimumWidth(410)
        self._theme = theme
        self._language = language
        self._build_ui()
        self.set_theme(theme)
        self.set_language(language)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(9)

        self.title_label = QLabel(self)
        self.title_label.setProperty("role", "popup-title")
        root.addWidget(self.title_label)

        status_box = QFrame(self)
        status_box.setObjectName("PopupStatus")
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(9, 7, 9, 7)
        status_layout.setSpacing(2)
        self.summary_label = QLabel(status_box)
        self.summary_label.setStyleSheet("font-weight: 700;")
        self.detail_label = QLabel(status_box)
        self.detail_label.setProperty("role", "popup-detail")
        self.detail_label.setWordWrap(True)
        status_layout.addWidget(self.summary_label)
        status_layout.addWidget(self.detail_label)
        root.addWidget(status_box)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        self.full_voltage = self._voltage_input()
        self.minimum_voltage = self._voltage_input()
        self.minimum_percent = self._percent_input(maximum=99.0)
        self.use_pixhawk_percent = QCheckBox(self)
        self.warning_mode = self._mode_input()
        self.warning_threshold = self._percent_input()
        self.failsafe_enabled = QCheckBox(self)
        self.failsafe_mode = self._mode_input()
        self.failsafe_threshold = self._percent_input()

        self.full_voltage_label = QLabel(self)
        self.minimum_voltage_label = QLabel(self)
        self.minimum_percent_label = QLabel(self)
        self.use_pixhawk_percent_label = QLabel(self)
        self.warning_mode_label = QLabel(self)
        self.warning_threshold_label = QLabel(self)
        self.failsafe_enabled_label = QLabel(self)
        self.failsafe_mode_label = QLabel(self)
        self.failsafe_threshold_label = QLabel(self)
        for label, widget in (
            (self.full_voltage_label, self.full_voltage),
            (self.minimum_voltage_label, self.minimum_voltage),
            (self.minimum_percent_label, self.minimum_percent),
            (self.use_pixhawk_percent_label, self.use_pixhawk_percent),
            (self.warning_mode_label, self.warning_mode),
            (self.warning_threshold_label, self.warning_threshold),
            (self.failsafe_enabled_label, self.failsafe_enabled),
            (self.failsafe_mode_label, self.failsafe_mode),
            (self.failsafe_threshold_label, self.failsafe_threshold),
        ):
            form.addRow(label, widget)
        root.addLayout(form)

        self.result_label = QLabel(self)
        self.result_label.setProperty("role", "popup-detail")
        self.result_label.setWordWrap(True)
        root.addWidget(self.result_label)

        buttons = QHBoxLayout()
        self.all_settings_button = QPushButton(self)
        self.save_button = QPushButton(self)
        self.save_button.setProperty("role", "primary")
        buttons.addWidget(self.all_settings_button)
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        root.addLayout(buttons)

        self.warning_mode.currentIndexChanged.connect(
            lambda _index: self._sync_threshold_unit(
                self.warning_threshold,
                self.warning_mode,
            )
        )
        self.failsafe_mode.currentIndexChanged.connect(
            lambda _index: self._sync_threshold_unit(
                self.failsafe_threshold,
                self.failsafe_mode,
            )
        )
        self.use_pixhawk_percent.toggled.connect(self._sync_percentage_source)
        self.all_settings_button.clicked.connect(self.settings_requested.emit)
        self.save_button.clicked.connect(
            lambda: self.save_requested.emit(self.values())
        )

    def show_settings(
        self,
        settings: SettingsModel,
        snapshot: TelemetrySnapshot,
    ) -> None:
        telemetry = settings.backend.battery_telemetry
        alarm = settings.backend.battery_alarm
        self.full_voltage.setValue(telemetry.full_voltage)
        self.minimum_voltage.setValue(telemetry.reserve_voltage)
        self.minimum_percent.setValue(telemetry.reserve_percent)
        use_pixhawk_percent = bool(telemetry.use_pixhawk_percent)
        self.use_pixhawk_percent.setChecked(use_pixhawk_percent)
        self._sync_percentage_source(use_pixhawk_percent)
        self._set_mode(self.warning_mode, alarm.warning_mode)
        self.warning_threshold.setValue(alarm.warning_threshold)
        self.failsafe_enabled.setChecked(alarm.failsafe_enabled)
        self._set_mode(self.failsafe_mode, alarm.failsafe_mode)
        self.failsafe_threshold.setValue(alarm.failsafe_threshold)
        self._sync_threshold_unit(self.warning_threshold, self.warning_mode)
        self._sync_threshold_unit(self.failsafe_threshold, self.failsafe_mode)
        self.result_label.clear()

        if snapshot.battery_available:
            self.summary_label.setText(
                t(
                    "battery.popup.summary",
                    self._language,
                    percent=snapshot.battery_percent,
                    voltage=snapshot.battery_voltage,
                )
            )
        else:
            self.summary_label.setText(t("battery.popup.unavailable", self._language))
        self.detail_label.setText(
            t(
                "battery.popup.estimate",
                self._language,
                full=telemetry.full_voltage,
                minimum=telemetry.reserve_voltage,
                percent=telemetry.reserve_percent,
                empty=telemetry.empty_voltage,
            )
        )

    def values(self) -> dict[str, object]:
        return {
            "full_voltage": self.full_voltage.value(),
            "reserve_voltage": self.minimum_voltage.value(),
            "reserve_percent": self.minimum_percent.value(),
            "use_pixhawk_percent": self.use_pixhawk_percent.isChecked(),
            "warning_mode": str(self.warning_mode.currentData()),
            "warning_threshold": self.warning_threshold.value(),
            "failsafe_enabled": self.failsafe_enabled.isChecked(),
            "failsafe_mode": str(self.failsafe_mode.currentData()),
            "failsafe_threshold": self.failsafe_threshold.value(),
        }

    def set_result(self, success: bool, message: str) -> None:
        color = self._theme.success if success else self._theme.danger
        self.result_label.setStyleSheet(f"color: {color};")
        self.result_label.setText(message)

    def set_language(self, language: str) -> None:
        self._language = language
        self.title_label.setText(t("battery.popup.title", language))
        self.full_voltage_label.setText(
            t("dialog.settings.battery_estimate_full_voltage", language)
        )
        self.minimum_voltage_label.setText(
            t("dialog.settings.battery_estimate_min_voltage", language)
        )
        self.minimum_percent_label.setText(
            t("dialog.settings.battery_estimate_min_percent", language)
        )
        self.use_pixhawk_percent_label.setText(
            t("dialog.settings.battery_use_pixhawk_percent", language)
        )
        self.warning_mode_label.setText(
            t("dialog.settings.battery_warning_mode", language)
        )
        self.warning_threshold_label.setText(
            t("dialog.settings.battery_warning_threshold", language)
        )
        self.failsafe_enabled_label.setText(
            t("dialog.settings.battery_failsafe_enabled", language)
        )
        self.failsafe_mode_label.setText(
            t("dialog.settings.battery_failsafe_mode", language)
        )
        self.failsafe_threshold_label.setText(
            t("dialog.settings.battery_failsafe_threshold", language)
        )
        for combo in (self.warning_mode, self.failsafe_mode):
            selected = combo.currentData()
            combo.setItemText(0, t("battery.alarm.mode.percent", language))
            combo.setItemText(1, t("battery.alarm.mode.voltage", language))
            self._set_mode(combo, str(selected))
        self.all_settings_button.setText(t("battery.popup.all_settings", language))
        self.save_button.setText(t("battery.popup.save", language))

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"""
            QDialog#BatterySettingsPopup {{
                background: {theme.surface};
                border: 1px solid {theme.border_strong};
                border-radius: 7px;
            }}
            QLabel {{ background: transparent; color: {theme.text}; }}
            QLabel[role="popup-title"] {{ font-size: 11px; font-weight: 750; }}
            QLabel[role="popup-detail"] {{ color: {theme.muted_text}; font-size: 10px; }}
            QFrame#PopupStatus {{
                background: {theme.surface_alt};
                border: 1px solid {theme.border};
                border-radius: 5px;
            }}
            """
        )

    def _mode_input(self) -> QComboBox:
        combo = QComboBox(self)
        combo.addItem("", "percent")
        combo.addItem("", "voltage")
        return combo

    def _voltage_input(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(1.0, 100.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSuffix(" V")
        return spin

    def _percent_input(self, *, maximum: float = 100.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(0.1, maximum)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setSuffix(" %")
        return spin

    @staticmethod
    def _set_mode(combo: QComboBox, mode: str) -> None:
        index = combo.findData(mode)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _sync_threshold_unit(spin: QDoubleSpinBox, combo: QComboBox) -> None:
        spin.setSuffix(" V" if combo.currentData() == "voltage" else " %")

    def _sync_percentage_source(self, use_pixhawk_percent: bool) -> None:
        for widget in (
            self.full_voltage,
            self.minimum_voltage,
            self.minimum_percent,
        ):
            widget.setEnabled(not use_pixhawk_percent)
        for label in (
            self.full_voltage_label,
            self.minimum_voltage_label,
            self.minimum_percent_label,
        ):
            label.setEnabled(not use_pixhawk_percent)


__all__ = ["BatterySettingsPopup"]
