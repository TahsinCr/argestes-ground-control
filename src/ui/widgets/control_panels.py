from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout,
    QDoubleSpinBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from src.models.telemetry import TelemetrySnapshot
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette
from src.ui.widgets.common import PanelFrame, compact_button


class TelemetryPanel(PanelFrame):
    SECTION_SPECS = (
        ("flight", (("flight_mode", "telemetry.flight_mode"), ("vehicle_state", "telemetry.vehicle_state"),
                    ("altitude_agl", "telemetry.altitude_agl"), ("altitude_msl", "telemetry.altitude_msl"),
                    ("climb_rate", "telemetry.vertical_speed"), ("throttle", "telemetry.throttle"),
                    ("load_factor", "telemetry.load_factor"), ("ekf", "telemetry.ekf"))),
        ("navigation", (("waypoint", "telemetry.waypoint"), ("home", "telemetry.home"),
                         ("mission", "telemetry.mission"), ("wind", "telemetry.wind"))),
        ("motion", (("roll", "telemetry.roll"), ("pitch", "telemetry.pitch"), ("yaw", "telemetry.yaw"),
                     ("acceleration_x", "telemetry.axis_x"), ("acceleration_y", "telemetry.axis_y"),
                     ("acceleration_z", "telemetry.axis_z"))),
    )

    def __init__(self, title: str, subtitle: str, theme: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(title, subtitle, parent)
        self.setMinimumWidth(300)
        self._theme = theme
        self._language = "tr"
        content = QWidget(self.body)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(3)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.body_layout.addWidget(content)
        self.readouts: dict[str, QLabel] = {}
        self.readout_labels: dict[str, QLabel] = {}
        self.section_titles: dict[str, QLabel] = {}
        self._readout_cache: dict[str, str] = {}
        for section_key, items in self.SECTION_SPECS:
            title_label = QLabel("", content)
            title_label.setProperty("role", "section-label")
            self.section_titles[section_key] = title_label
            self.content_layout.addWidget(title_label)
            section = QFrame(content)
            section.setObjectName("TelemetrySection")
            grid = QGridLayout(section)
            grid.setContentsMargins(7, 4, 7, 4)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(3)
            for index, (key, _) in enumerate(items):
                row, column = index // 2, (index % 2) * 2
                label = QLabel("", section)
                label.setProperty("role", "readout-label")
                value = QLabel("--", section)
                value.setProperty("role", "readout-value")
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                value.setMinimumWidth(0)
                value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                grid.addWidget(label, row, column)
                grid.addWidget(value, row, column + 1)
                grid.setColumnStretch(column + 1, 1)
                self.readout_labels[key] = label
                self.readouts[key] = value
            self.content_layout.addWidget(section)
        self.content_layout.addStretch(1)
        self.set_language(self._language)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme

    def set_language(self, language: str) -> None:
        self._language = language
        for section, items in self.SECTION_SPECS:
            self.section_titles[section].setText(t(f"telemetry.section.{section}", language).upper())
            for key, label_key in items:
                self.readout_labels[key].setText(t(label_key, language).upper())

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        if not (snapshot.telemetry_link and snapshot.autopilot_link):
            values = {key: "--" for key in self.readouts}
            values["flight_mode"] = snapshot.flight_mode
            self._set_readout_values(values)
            return
        arm_state = "ARMED" if snapshot.armed else "DISARMED"
        self._set_readout_values({
            "flight_mode": snapshot.flight_mode,
            "vehicle_state": f"{arm_state} · {snapshot.system_status}",
            "altitude_agl": f"{snapshot.altitude_agl:0.1f} m",
            "altitude_msl": f"{snapshot.altitude_msl:0.1f} m",
            "climb_rate": f"{snapshot.climb_rate:+0.1f} m/s",
            "throttle": f"{snapshot.throttle_percent:0.0f}%",
            "load_factor": self._format_optional(snapshot.load_factor_g, "g", 2),
            "waypoint": f"{snapshot.wp_distance:0.0f} m · {snapshot.nav_bearing:03.0f}°",
            "home": f"{snapshot.home_distance:0.0f} m · {snapshot.home_heading:03.0f}°",
            "mission": f"{snapshot.mission_index}/{snapshot.mission_total}",
            "roll": f"{snapshot.roll:+0.1f}°",
            "pitch": f"{snapshot.pitch:+0.1f}°",
            "yaw": f"{snapshot.yaw % 360:03.0f}°",
            "acceleration_x": self._format_optional(snapshot.acceleration_x_mps2, "m/s²", 2),
            "acceleration_y": self._format_optional(snapshot.acceleration_y_mps2, "m/s²", 2),
            "acceleration_z": self._format_optional(snapshot.acceleration_z_mps2, "m/s²", 2),
            "wind": f"{snapshot.wind_speed:0.1f} m/s · {snapshot.wind_heading:03.0f}°",
            "ekf": "OK" if snapshot.ekf_ok else "WAIT",
        })

    def _set_readout_values(self, values: dict[str, str]) -> None:
        for key, text in values.items():
            if self._readout_cache.get(key) == text:
                continue
            self._readout_cache[key] = text
            self.readouts[key].setText(text)

    @staticmethod
    def _format_optional(value: float | None, unit: str, decimals: int) -> str:
        return "--" if value is None else f"{value:.{decimals}f} {unit}"


class VehicleControlsPanel(PanelFrame):
    """Vehicle-neutral flight mode, arming and navigation controls."""

    command_requested = pyqtSignal(str, dict)

    def __init__(self, flight_modes=(), language: str = "tr", parent: QWidget | None = None) -> None:
        super().__init__("", "", parent)
        self._connected = False
        self._armed = False
        self._language = language
        self._mode_selection_dirty = False
        self._telemetry_mode = ""
        self._pending_commands: list[str] = []
        self._requested_mode: str | None = None
        self._requested_armed: bool | None = None
        self.setMinimumWidth(300)

        scroll = QScrollArea(self)
        scroll.setObjectName("CommandTabScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget(scroll)
        content.setObjectName("CommandTabContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        scroll.setWidget(content)
        self.body_layout.addWidget(scroll, 1)
        self.command_status = QLabel(self)
        self.command_status.setObjectName("CommandStatus")
        self.command_status.setProperty("role", "readout-label")
        self.command_status.setWordWrap(True)
        self.body_layout.addWidget(self.command_status)

        control_card = QFrame(content)
        control_card.setObjectName("QuickCommandCard")
        control_layout = QVBoxLayout(control_card)
        control_layout.setContentsMargins(8, 7, 8, 8)
        control_layout.setSpacing(4)
        self.mode_label = QLabel(self)
        self.mode_label.setProperty("role", "section-label")
        control_layout.addWidget(self.mode_label)
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self.mode_combo = QComboBox(self)
        self.mode_combo.setObjectName("VehicleModeCombo")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_selected)
        self.apply_mode_button = compact_button("UYGULA", "primary")
        self.apply_mode_button.clicked.connect(self._apply_mode)
        mode_row.addWidget(self.mode_combo, 1)
        mode_row.addWidget(self.apply_mode_button)
        control_layout.addLayout(mode_row)

        self.arm_label = QLabel(self)
        self.arm_label.setProperty("role", "section-label")
        control_layout.addWidget(self.arm_label)
        self.arm_button = compact_button("ARM", "success")
        self.arm_button.clicked.connect(self._toggle_arm)
        control_layout.addWidget(self.arm_button)
        content_layout.addWidget(control_card)

        profile_card = QFrame(content)
        profile_card.setObjectName("QuickCommandCard")
        profile_layout = QVBoxLayout(profile_card)
        profile_layout.setContentsMargins(8, 7, 8, 8)
        profile_layout.setSpacing(4)
        self.profile_label = QLabel(self)
        self.profile_label.setProperty("role", "section-label")
        profile_layout.addWidget(self.profile_label)
        profile_grid = QGridLayout()
        profile_grid.setContentsMargins(0, 0, 0, 0)
        profile_grid.setHorizontalSpacing(5)
        profile_grid.setVerticalSpacing(4)
        self.profile_inputs: dict[str, QDoubleSpinBox] = {}
        self.profile_apply_buttons: dict[str, QPushButton] = {}
        self.profile_labels: dict[str, QLabel] = {}
        for column, (key, label_text, low, high, value, suffix, decimals) in enumerate((
            ("target_altitude", "ALT", 20.0, 1000.0, 140.0, " m", 1),
            ("air_speed", "SPD", 10.0, 70.0, 28.0, " m/s", 1),
        )):
            label = QLabel(label_text, self)
            label.setProperty("role", "readout-label")
            field = QDoubleSpinBox(self)
            field.setRange(low, high)
            field.setDecimals(decimals)
            field.setValue(value)
            field.setSuffix(suffix)
            field.setMinimumWidth(0)
            self.profile_inputs[key] = field
            self.profile_labels[key] = label
            apply = compact_button("APPLY", "primary")
            apply.clicked.connect(
                lambda _checked=False, name=key: self._apply_profile(name)
            )
            profile_grid.addWidget(label, 0, column)
            profile_grid.addWidget(field, 1, column)
            profile_grid.addWidget(apply, 2, column)
            self.profile_apply_buttons[key] = apply
        profile_layout.addLayout(profile_grid)
        content_layout.addWidget(profile_card)
        content_layout.addStretch(1)
        self.set_language(language)
        self.set_flight_modes(flight_modes)
        self.set_available(False)
        self._set_command_status(t("command.status.disconnected", language), "warning")

    def set_language(self, language: str) -> None:
        self._language = language
        self.mode_label.setText(t("command.target_control", language).upper())
        self.profile_label.setText(t("command.target_profile", language).upper())
        self.apply_mode_button.setText(t("command.apply", language).upper())
        self.apply_mode_button.setToolTip(t("command.mode_apply.tooltip", language))
        self._update_arm_display()
        for key, button in self.profile_apply_buttons.items():
            button.setText(t(
                "command.apply_target_altitude" if key == "target_altitude" else "command.apply_air_speed",
                language,
            ).upper())
            button.setToolTip(t(
                "command.apply_target_altitude.tooltip" if key == "target_altitude" else "command.apply_air_speed.tooltip",
                language,
            ))

    def set_flight_modes(self, modes) -> None:
        selected = self.mode_combo.currentData()
        names = tuple(dict.fromkeys(str(mode).strip().upper() for mode in modes if str(mode).strip()))
        current = tuple(self.mode_combo.itemData(i) for i in range(self.mode_combo.count()))
        if names == current:
            return
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        for mode in names:
            self.mode_combo.addItem("LAND" if mode == "AUTOLAND" else mode, mode)
        index = self.mode_combo.findData(selected)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        self.mode_combo.blockSignals(False)

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        was_connected = self._connected
        self._connected = bool(snapshot.telemetry_link and snapshot.autopilot_link)
        self._armed = bool(snapshot.armed)
        self._telemetry_mode = str(snapshot.flight_mode or "").strip().upper()
        mode_index = self.mode_combo.findData(self._telemetry_mode)
        if self._mode_selection_dirty and self.mode_selection_matches(self._telemetry_mode):
            self._mode_selection_dirty = False
            self._requested_mode = None
        if self._requested_armed is self._armed:
            self._requested_armed = None
        if not self._connected:
            self._requested_armed = None
            self._requested_mode = None
        if mode_index >= 0 and not self._mode_selection_dirty:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(mode_index)
            self.mode_combo.blockSignals(False)
        self.set_available(self._connected)
        if was_connected != self._connected and not self._pending_commands:
            self._set_command_status(
                t("command.status.ready" if self._connected else "command.status.disconnected", self._language),
                "neutral" if self._connected else "warning",
            )
        self._update_arm_display()

    def set_available(self, available: bool) -> None:
        available = bool(
            available and not self._pending_commands and self._requested_armed is None
        )
        self.mode_combo.setEnabled(available)
        self.apply_mode_button.setEnabled(available and self.mode_combo.count() > 0)
        self.arm_button.setEnabled(available)
        for button in self.findChildren(QPushButton):
            if button is not self.apply_mode_button and button is not self.arm_button:
                button.setEnabled(available)
        for field in self.profile_inputs.values():
            field.setEnabled(available)

    def command_started(self, code: str, parameters: dict | None = None) -> None:
        normalized = str(code).strip().upper()
        self._pending_commands.append(normalized)
        parameters = parameters or {}
        if normalized in {"ARM", "FORCE_ARM"}:
            self._requested_armed = True
        elif normalized == "DISARM":
            self._requested_armed = False
        elif normalized in {"SET_MODE", "APPLY_MODE"}:
            requested = str(parameters.get("mode", "")).strip().upper()
            self._requested_mode = requested or None
            self._mode_selection_dirty = self._requested_mode is not None
        self.set_available(self._connected)
        self._update_arm_display()
        self._set_command_status(
            t("command.status.sending", self._language).format(command=normalized),
            "warning",
        )

    def command_completed(self, result) -> None:
        code = str(getattr(getattr(result, "request", None), "code", "COMMAND")).upper()
        try:
            self._pending_commands.remove(code)
        except ValueError:
            pass
        accepted = bool(getattr(result, "accepted", False))
        if not accepted:
            if code in {"ARM", "FORCE_ARM", "DISARM"}:
                self._requested_armed = None
            if code in {"SET_MODE", "APPLY_MODE"}:
                self._requested_mode = None
                self._mode_selection_dirty = False
        self.set_available(self._connected)
        self._update_arm_display()
        self._set_command_status(
            t("command.result.success" if accepted else "command.result.failed", self._language).format(command=code),
            "success" if accepted else "danger",
        )

    def _set_command_status(self, message: str, state: str) -> None:
        self.command_status.setText(message)
        self.command_status.setProperty("state", state)
        self.command_status.style().unpolish(self.command_status)
        self.command_status.style().polish(self.command_status)

    def _apply_mode(self) -> None:
        mode = str(self.mode_combo.currentData() or "").strip().upper()
        if mode:
            self._requested_mode = mode
            self._mode_selection_dirty = True
            self._request("SET_MODE", {"mode": mode})

    def _on_mode_selected(self, _index: int) -> None:
        selected = str(self.mode_combo.currentData() or "").strip().upper()
        self._mode_selection_dirty = bool(
            self._telemetry_mode and selected and selected != self._telemetry_mode
        )

    def mode_selection_matches(self, mode: str) -> bool:
        selected = str(self.mode_combo.currentData() or "").strip().upper()
        return bool(selected and selected == str(mode).strip().upper())

    def _toggle_arm(self) -> None:
        if self._requested_armed is None:
            self._request("ARM" if not self._armed else "DISARM", {})

    def _update_arm_display(self) -> None:
        if not hasattr(self, "arm_button"):
            return
        if not self._connected:
            label_key = "command.arm_state.disconnected"
            button_key = "command.arm_toggle.unavailable"
            role = None
        elif self._requested_armed is True:
            label_key = "command.arm_state.arming"
            button_key = "command.arm_button.pending"
            role = "success"
        elif self._requested_armed is False:
            label_key = "command.arm_state.disarming"
            button_key = "command.disarm_button.pending"
            role = "danger"
        elif self._armed:
            label_key = "command.arm_state.armed"
            button_key = "command.disarm_button.ready"
            role = "danger"
        else:
            label_key = "command.arm_state.disarmed"
            button_key = "command.arm_button.ready"
            role = "success"
        self.arm_label.setText(t(label_key, self._language).upper())
        self.arm_label.setVisible(self._connected)
        self.arm_button.setText(t(button_key, self._language))
        self.arm_button.setProperty("role", role)
        self.arm_button.style().unpolish(self.arm_button)
        self.arm_button.style().polish(self.arm_button)

    def _apply_profile(self, field: str) -> None:
        self._request(
            "SET_TARGET_PROFILE",
            {field: self.profile_inputs[field].value()},
        )

    def _request(self, command: str, parameters: dict) -> None:
        if self._connected:
            self.command_requested.emit(command, parameters)
