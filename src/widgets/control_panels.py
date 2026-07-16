from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

from src.config.i18n import t
from src.models.telemetry import TelemetrySnapshot
from src.themes.style import ThemePalette
from src.widgets.common import MetricCard, PanelFrame, compact_button


class TelemetryPanel(PanelFrame):
    def __init__(self, title: str, subtitle: str, theme: ThemePalette, parent=None) -> None:
        super().__init__(title, subtitle, parent)
        self.setMinimumWidth(300)
        self._theme = theme

        summary = QWidget(self)
        summary_layout = QGridLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setHorizontalSpacing(4)
        summary_layout.setVerticalSpacing(4)

        self.cards = {
            "mode": MetricCard("MODE", "DISCONNECTED", "system"),
            "battery": MetricCard("BAT", "0%", "0.0 V"),
            "alt": MetricCard("ALT", "0.0 m", "MSL 0.0 m"),
            "speed": MetricCard("IAS/GS", "0.0/0.0", "m/s"),
        }
        for index, card in enumerate(self.cards.values()):
            summary_layout.addWidget(card, index // 2, index % 2)
        self.body_layout.addWidget(summary)

        details = QWidget(self)
        details_layout = QGridLayout(details)
        details_layout.setContentsMargins(0, 2, 0, 0)
        details_layout.setHorizontalSpacing(10)
        details_layout.setVerticalSpacing(4)
        self.readouts: dict[str, QLabel] = {}
        labels = [
            ("GPS", 0, 0),
            ("SAT", 0, 2),
            ("EKF", 1, 0),
            ("RC", 1, 2),
            ("HOME", 2, 0),
            ("LAT", 3, 0),
            ("LON", 3, 2),
            ("SRV TIME", 4, 0),
            ("UAV TIME", 4, 2),
        ]
        for key, row, column in labels:
            label = QLabel(key, details)
            label.setProperty("role", "readout-label")
            value = QLabel("--", details)
            value.setProperty("role", "readout-value")
            details_layout.addWidget(label, row, column)
            details_layout.addWidget(value, row, column + 1)
            self.readouts[key.lower().replace(" ", "_")] = value
        self.body_layout.addWidget(details, 1)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme

    def set_status_labels(self, labels: dict[str, str]) -> None:
        del labels

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self.cards["mode"].update_value(snapshot.flight_mode, snapshot.system_status)
        self.cards["alt"].update_value(f"{snapshot.altitude_agl:0.1f} m", f"MSL {snapshot.altitude_msl:0.1f} m")
        self.cards["speed"].update_value(f"{snapshot.air_speed:0.1f} / {snapshot.ground_speed:0.1f}", "m/s")
        self.cards["battery"].update_value(f"{snapshot.battery_percent}%", f"{snapshot.battery_voltage:0.1f} V")

        self.readouts["lat"].setText(f"{snapshot.latitude:.5f}")
        self.readouts["lon"].setText(f"{snapshot.longitude:.5f}")
        self.readouts["gps"].setText(snapshot.gps_fix)
        self.readouts["sat"].setText(str(snapshot.gps_satellites))
        self.readouts["home"].setText(f"{snapshot.home_distance:0.0f} m / {snapshot.home_heading:03.0f}")
        self.readouts["rc"].setText(str(snapshot.rc_rssi))
        self.readouts["ekf"].setText("OK" if snapshot.ekf_ok else "WAIT")
        self.readouts["srv_time"].setText(self._format_external_time(snapshot.server_time))
        self.readouts["uav_time"].setText(self._format_external_time(snapshot.uav_time))

    def _format_external_time(self, value: datetime | str | None) -> str:
        if isinstance(value, datetime):
            return value.strftime("%H:%M:%S")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "00:00:00"


class CommandPanel(PanelFrame):
    command_requested = pyqtSignal(str, dict)

    TASK_OPTIONS = [
        ("TARGET_LOCK", "Kitlenme"),
        ("KAMIKAZE", "Kamikaze"),
        ("WAYPOINT_TRACK", "Waypoint Takip"),
        ("AUTO_TAKEOFF", "Otonom Kalkis"),
        ("AUTO_LAND", "Otonom Inis"),
        ("SURVEY_AREA", "Alan Tarama"),
    ]

    def __init__(self, title: str, subtitle: str, parent=None) -> None:
        super().__init__(title, subtitle, parent)
        self._language = "tr"
        self._armed = False
        self._mission_running = False
        self.setMinimumWidth(300)

        self.mode_label = QLabel(self)
        self.mode_label.setProperty("role", "section-label")
        self.body_layout.addWidget(self.mode_label)

        mode_row = QWidget(self)
        mode_layout = QGridLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setHorizontalSpacing(4)
        mode_layout.setVerticalSpacing(4)
        self.mode_combo = QComboBox(self)
        for mode in ("GUIDED", "AUTO", "LOITER", "FBWA", "MANUAL", "TAKEOFF", "QLAND"):
            self.mode_combo.addItem(mode, mode)
        self.mode_apply_button = compact_button("APPLY", "primary")
        self.mode_apply_button.clicked.connect(self._emit_mode_update)
        mode_layout.addWidget(self.mode_combo, 0, 0)
        mode_layout.addWidget(self.mode_apply_button, 0, 1)
        self.body_layout.addWidget(mode_row)

        self.arm_label = QLabel(self)
        self.arm_label.setProperty("role", "section-label")
        self.arm_label.setVisible(False)
        self.body_layout.addWidget(self.arm_label)

        arm_row = QWidget(self)
        arm_layout = QGridLayout(arm_row)
        arm_layout.setContentsMargins(0, 0, 0, 0)
        arm_layout.setHorizontalSpacing(4)
        arm_layout.setVerticalSpacing(4)

        self.arm_toggle = compact_button("DISARMED", "danger")
        self.arm_toggle.setCheckable(True)
        self.arm_toggle.clicked.connect(self._toggle_arm_state)
        arm_layout.addWidget(self.arm_toggle, 0, 0)

        self.start_stop_toggle = compact_button("START", "primary")
        self.start_stop_toggle.setCheckable(True)
        self.start_stop_toggle.clicked.connect(self._toggle_mission_state)
        arm_layout.addWidget(self.start_stop_toggle, 0, 1)
        self.body_layout.addWidget(arm_row)

        self.profile_label = QLabel(self)
        self.profile_label.setProperty("role", "section-label")
        self.profile_label.setVisible(False)
        self.body_layout.addWidget(self.profile_label)

        target_row = QWidget(self)
        target_layout = QGridLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setHorizontalSpacing(4)
        target_layout.setVerticalSpacing(3)

        self.target_altitude = QDoubleSpinBox(self)
        self.target_altitude.setRange(20.0, 1000.0)
        self.target_altitude.setDecimals(1)
        self.target_altitude.setValue(140.0)
        self.target_altitude.setSuffix(" m")

        self.air_speed = QDoubleSpinBox(self)
        self.air_speed.setRange(10.0, 70.0)
        self.air_speed.setDecimals(1)
        self.air_speed.setValue(28.0)
        self.air_speed.setSuffix(" m/s")

        self.loiter_radius = QDoubleSpinBox(self)
        self.loiter_radius.setRange(20.0, 800.0)
        self.loiter_radius.setDecimals(1)
        self.loiter_radius.setValue(75.0)
        self.loiter_radius.setSuffix(" m")

        for column, (label, widget) in enumerate(
            (
                ("ALT", self.target_altitude),
                ("SPD", self.air_speed),
                ("LTR", self.loiter_radius),
            )
        ):
            field_label = QLabel(label, self)
            field_label.setProperty("role", "readout-label")
            target_layout.addWidget(field_label, 0, column)
            target_layout.addWidget(widget, 1, column)

        self.profile_apply_button = compact_button("APPLY", "primary")
        self.profile_apply_button.clicked.connect(self._emit_profile_update)
        target_layout.addWidget(self.profile_apply_button, 1, 3)
        self.body_layout.addWidget(target_row)

        self.tasks_label = QLabel(self)
        self.tasks_label.setProperty("role", "section-label")
        self.body_layout.addWidget(self.tasks_label)

        self.task_list = QListWidget(self)
        self.task_list.setUniformItemSizes(True)
        self.task_list.setAlternatingRowColors(False)
        self.task_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.task_list.setMinimumHeight(96)
        for code, label in self.TASK_OPTIONS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, code)
            self.task_list.addItem(item)
        self.task_list.setCurrentRow(0)
        self.body_layout.addWidget(self.task_list, 1)

        self.task_apply_button = compact_button("TASK APPLY", "primary")
        self.task_apply_button.clicked.connect(self._emit_task_command)
        self.body_layout.addWidget(self.task_apply_button)

        self.set_language(self._language)

    def set_language(self, language: str) -> None:
        self._language = language
        self.mode_label.setText(t("command.quick", language).upper())
        self.arm_label.setText("")
        self.profile_label.setText("")
        self.tasks_label.setText(t("command.tasks", language).upper())
        self.mode_apply_button.setText(t("command.apply", language).upper())
        self.profile_apply_button.setText(t("command.apply", language).upper())
        self.task_apply_button.setText(t("command.task_apply", language).upper())
        self._sync_arm_button()
        self._sync_start_stop_button()

        task_labels = {
            "TARGET_LOCK": t("command.target_lock", language),
            "KAMIKAZE": t("command.kamikaze", language),
            "WAYPOINT_TRACK": t("command.waypoint_track", language),
            "AUTO_TAKEOFF": t("command.auto_takeoff", language),
            "AUTO_LAND": t("command.auto_land", language),
            "SURVEY_AREA": t("command.survey", language),
        }
        for index in range(self.task_list.count()):
            item = self.task_list.item(index)
            item.setText(task_labels.get(item.data(Qt.ItemDataRole.UserRole), item.text()))

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._armed = snapshot.armed
        self.arm_toggle.blockSignals(True)
        self.arm_toggle.setChecked(snapshot.armed)
        self.arm_toggle.blockSignals(False)
        self._sync_arm_button()

        index = self.mode_combo.findData(snapshot.flight_mode)
        if index >= 0 and self.mode_combo.currentIndex() != index:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(index)
            self.mode_combo.blockSignals(False)

    def _profile_params(self) -> dict:
        return {
            "target_altitude": round(self.target_altitude.value(), 1),
            "air_speed": round(self.air_speed.value(), 1),
            "loiter_radius": round(self.loiter_radius.value(), 1),
        }

    def _sync_arm_button(self) -> None:
        if self._armed:
            self.arm_toggle.setText(t("command.arm_toggle.on", self._language))
            self.arm_toggle.setProperty("role", "success")
        else:
            self.arm_toggle.setText(t("command.arm_toggle.off", self._language))
            self.arm_toggle.setProperty("role", "danger")
        self.arm_toggle.style().unpolish(self.arm_toggle)
        self.arm_toggle.style().polish(self.arm_toggle)

    def _toggle_arm_state(self, checked: bool) -> None:
        self._armed = checked
        self._sync_arm_button()
        self._emit_command("ARM" if checked else "DISARM")

    def _sync_start_stop_button(self) -> None:
        if self._mission_running:
            self.start_stop_toggle.setText(t("command.start_stop.on", self._language))
            self.start_stop_toggle.setProperty("role", "danger")
        else:
            self.start_stop_toggle.setText(t("command.start_stop.off", self._language))
            self.start_stop_toggle.setProperty("role", "primary")
        self.start_stop_toggle.style().unpolish(self.start_stop_toggle)
        self.start_stop_toggle.style().polish(self.start_stop_toggle)

    def _toggle_mission_state(self, checked: bool) -> None:
        self._mission_running = checked
        self._sync_start_stop_button()
        self._emit_command("MISSION_START" if checked else "MISSION_STOP")

    def _emit_mode_update(self) -> None:
        params = self._profile_params()
        params["mode"] = self.mode_combo.currentData()
        self.command_requested.emit("SET_MODE", params)

    def _emit_profile_update(self) -> None:
        self.command_requested.emit("SET_TARGET_PROFILE", self._profile_params())

    def _emit_task_command(self) -> None:
        item = self.task_list.currentItem()
        if not item:
            return
        params = self._profile_params()
        params["mode"] = self.mode_combo.currentData()
        params["task"] = item.data(Qt.ItemDataRole.UserRole)
        self.command_requested.emit("SET_MISSION_TASK", params)

    def _emit_command(self, command: str) -> None:
        params = self._profile_params()
        params["mode"] = self.mode_combo.currentData()
        self.command_requested.emit(command, params)
