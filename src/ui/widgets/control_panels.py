from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui.config.i18n import t
from src.ui.config.commands import (
    AUTONOMOUS_COMMANDS,
    FLIGHT_MODES,
    PROFILE_FIELDS,
    TASK_LABEL_KEYS,
)
from src.models.commands import CommandResult
from src.models.telemetry import TelemetrySnapshot
from src.ui.themes.style import ThemePalette
from src.ui.widgets.common import PanelFrame, compact_button


class TelemetryPanel(PanelFrame):
    SECTION_SPECS = (
        (
            "flight",
            (
                ("flight_mode", "telemetry.flight_mode"),
                ("vehicle_state", "telemetry.vehicle_state"),
                ("altitude_agl", "telemetry.altitude_agl"),
                ("altitude_msl", "telemetry.altitude_msl"),
                ("climb_rate", "telemetry.vertical_speed"),
                ("throttle", "telemetry.throttle"),
                ("load_factor", "telemetry.load_factor"),
                ("ekf", "telemetry.ekf"),
            ),
        ),
        (
            "navigation",
            (
                ("waypoint", "telemetry.waypoint"),
                ("home", "telemetry.home"),
                ("mission", "telemetry.mission"),
                ("wind", "telemetry.wind"),
            ),
        ),
        (
            "motion",
            (
                ("roll", "telemetry.roll"),
                ("pitch", "telemetry.pitch"),
                ("yaw", "telemetry.yaw"),
                ("acceleration_x", "telemetry.axis_x"),
                ("acceleration_y", "telemetry.axis_y"),
                ("acceleration_z", "telemetry.axis_z"),
            ),
        ),
    )

    def __init__(self, title: str, subtitle: str, theme: ThemePalette, parent=None) -> None:
        super().__init__(title, subtitle, parent)
        self.setMinimumWidth(300)
        self._theme = theme
        self._language = "tr"

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("TelemetryScrollArea")
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setMinimumHeight(0)
        self.scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.scroll_content = QWidget(self.scroll_area)
        self.scroll_content.setObjectName("TelemetryScrollContent")
        self.scroll_content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.content_layout = QVBoxLayout(self.scroll_content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(3)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll_area.setWidget(self.scroll_content)
        self.body_layout.addWidget(self.scroll_area)

        self.readouts: dict[str, QLabel] = {}
        self.readout_labels: dict[str, QLabel] = {}
        self.section_titles: dict[str, QLabel] = {}
        for section_key, items in self.SECTION_SPECS:
            self._add_readout_section(section_key, items)
        self.content_layout.addStretch(1)
        self.set_language(self._language)

    def _add_readout_section(
        self,
        section_key: str,
        items: tuple[tuple[str, str], ...],
    ) -> None:
        title = QLabel("", self.scroll_content)
        title.setProperty("role", "section-label")
        title.setObjectName("TelemetrySectionTitle")
        self.section_titles[section_key] = title
        self.content_layout.addWidget(title)

        section = QFrame(self.scroll_content)
        section.setObjectName("TelemetrySection")
        layout = QGridLayout(section)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(3)
        for index, (key, _label_key) in enumerate(items):
            row = index // 2
            column = (index % 2) * 2
            label = QLabel("", section)
            label.setProperty("role", "readout-label")
            value = QLabel("--", section)
            value.setProperty("role", "readout-value")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setMinimumWidth(0)
            value.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            layout.addWidget(label, row, column)
            layout.addWidget(value, row, column + 1)
            layout.setColumnStretch(column + 1, 1)
            self.readout_labels[key] = label
            self.readouts[key] = value
        self.content_layout.addWidget(section)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme

    def set_status_labels(self, labels: dict[str, str]) -> None:
        del labels

    def set_language(self, language: str) -> None:
        self._language = language
        for section_key, items in self.SECTION_SPECS:
            self.section_titles[section_key].setText(
                t(f"telemetry.section.{section_key}", language).upper()
            )
            for key, label_key in items:
                self.readout_labels[key].setText(t(label_key, language).upper())

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        connected = bool(snapshot.telemetry_link and snapshot.autopilot_link)
        if not connected:
            for readout in self.readouts.values():
                readout.setText("--")
                readout.setToolTip("")
            self.readouts["flight_mode"].setText(snapshot.flight_mode)
            return

        arm_state = "ARMED" if snapshot.armed else "DISARMED"
        self.readouts["flight_mode"].setText(snapshot.flight_mode)
        self.readouts["vehicle_state"].setText(
            f"{arm_state} · {snapshot.system_status}"
        )
        self.readouts["altitude_agl"].setText(f"{snapshot.altitude_agl:0.1f} m")
        self.readouts["altitude_msl"].setText(f"{snapshot.altitude_msl:0.1f} m")
        self.readouts["climb_rate"].setText(f"{snapshot.climb_rate:+0.1f} m/s")
        self.readouts["throttle"].setText(f"{snapshot.throttle_percent:0.0f}%")
        self.readouts["load_factor"].setText(
            self._format_optional(snapshot.load_factor_g, "g", 2)
        )
        self.readouts["waypoint"].setText(
            f"{snapshot.wp_distance:0.0f} m · {snapshot.nav_bearing:03.0f}°"
        )
        self.readouts["home"].setText(
            f"{snapshot.home_distance:0.0f} m · {snapshot.home_heading:03.0f}°"
        )
        self.readouts["mission"].setText(
            f"{snapshot.mission_index}/{snapshot.mission_total}"
        )
        self.readouts["roll"].setText(f"{snapshot.roll:+0.1f}°")
        self.readouts["pitch"].setText(f"{snapshot.pitch:+0.1f}°")
        self.readouts["yaw"].setText(f"{snapshot.yaw % 360:03.0f}°")
        self.readouts["acceleration_x"].setText(
            self._format_optional(snapshot.acceleration_x_mps2, "m/s²", 2)
        )
        self.readouts["acceleration_y"].setText(
            self._format_optional(snapshot.acceleration_y_mps2, "m/s²", 2)
        )
        self.readouts["acceleration_z"].setText(
            self._format_optional(snapshot.acceleration_z_mps2, "m/s²", 2)
        )
        self.readouts["wind"].setText(
            f"{snapshot.wind_speed:0.1f} m/s · {snapshot.wind_heading:03.0f}°"
        )
        self.readouts["ekf"].setText("OK" if snapshot.ekf_ok else "WAIT")

    @staticmethod
    def _format_optional(value: float | None, unit: str, decimals: int) -> str:
        if value is None:
            return "--"
        return f"{value:.{decimals}f} {unit}"

class CommandPanel(PanelFrame):
    command_requested = pyqtSignal(str, dict)

    def __init__(
        self,
        title: str,
        subtitle: str,
        parent=None,
        *,
        task_codes: Iterable[str] | None = None,
    ) -> None:
        super().__init__(title, subtitle, parent)
        self._language = "tr"
        self._armed = False
        self._vehicle_connected = False
        self._pending_commands: list[str] = []
        self._telemetry_mode = "DISCONNECTED"
        self._requested_mode: str | None = None
        self._requested_armed: bool | None = None
        self._mode_selection_dirty = False
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
        for mode in FLIGHT_MODES:
            self.mode_combo.addItem("LAND" if mode == "AUTOLAND" else mode, mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_selected)
        self.mode_apply_button = compact_button("APPLY", "primary")
        self.mode_apply_button.clicked.connect(self._emit_mode_update)
        mode_layout.addWidget(self.mode_combo, 0, 0)
        mode_layout.addWidget(self.mode_apply_button, 0, 1)
        self.body_layout.addWidget(mode_row)

        self.arm_label = QLabel(self)
        self.arm_label.setProperty("role", "section-label")
        self.body_layout.addWidget(self.arm_label)

        self.arm_toggle_button = compact_button("ARM", "success")
        self.arm_toggle_button.clicked.connect(self._toggle_arm_state)
        self.body_layout.addWidget(self.arm_toggle_button)

        self.command_status = QLabel(self)
        self.command_status.setProperty("role", "readout-label")
        self.command_status.setWordWrap(True)
        self.body_layout.addWidget(self.command_status)

        self.profile_label = QLabel(self)
        self.profile_label.setProperty("role", "section-label")
        self.profile_label.setVisible(False)
        self.body_layout.addWidget(self.profile_label)

        target_row = QWidget(self)
        target_layout = QGridLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setHorizontalSpacing(4)
        target_layout.setVerticalSpacing(3)

        self.profile_inputs: dict[str, QDoubleSpinBox] = {}
        for column, spec in enumerate(PROFILE_FIELDS):
            widget = QDoubleSpinBox(self)
            widget.setRange(spec.minimum, spec.maximum)
            widget.setDecimals(spec.decimals)
            widget.setValue(spec.default)
            widget.setSuffix(spec.suffix)
            self.profile_inputs[spec.key] = widget
            setattr(self, spec.key, widget)
            field_label = QLabel(spec.short_label, self)
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
        self.set_task_codes(
            (command.code for command in AUTONOMOUS_COMMANDS)
            if task_codes is None
            else task_codes
        )
        self.body_layout.addWidget(self.task_list, 1)

        self.task_apply_button = compact_button("TASK APPLY", "primary")
        self.task_apply_button.clicked.connect(self._emit_task_command)
        self.body_layout.addWidget(self.task_apply_button)

        self.set_language(self._language)

    def set_language(self, language: str) -> None:
        self._language = language
        self.mode_label.setText(t("command.quick", language).upper())
        self.profile_label.setText("")
        self.tasks_label.setText(t("command.tasks", language).upper())
        self.mode_apply_button.setText(t("command.apply", language).upper())
        self.profile_apply_button.setText(t("command.apply", language).upper())
        self.task_apply_button.setText(t("command.task_apply", language).upper())
        self._sync_arm_controls()
        self.mode_apply_button.setToolTip(t("command.mode_apply.tooltip", language))
        self.profile_apply_button.setToolTip(t("command.profile_apply.tooltip", language))
        if not self._pending_commands:
            self._set_command_status(
                t(
                    "command.status.ready"
                    if self._vehicle_connected
                    else "command.status.disconnected",
                    language,
                ),
                "neutral" if self._vehicle_connected else "warning",
            )
        self._sync_command_availability()

        for index in range(self.task_list.count()):
            item = self.task_list.item(index)
            code = str(item.data(Qt.ItemDataRole.UserRole) or "")
            label_key = TASK_LABEL_KEYS.get(code)
            item.setText(t(label_key, language) if label_key else code.replace("_", " ").title())

    def set_task_codes(self, task_codes: Iterable[str]) -> None:
        selected = ""
        current = self.task_list.currentItem()
        if current is not None:
            selected = str(current.data(Qt.ItemDataRole.UserRole) or "")
        codes = tuple(
            dict.fromkeys(
                str(code).strip().upper()
                for code in task_codes
                if str(code).strip()
            )
        )
        self.task_list.clear()
        selected_row = 0
        for row, code in enumerate(codes):
            item = QListWidgetItem(code)
            item.setData(Qt.ItemDataRole.UserRole, code)
            self.task_list.addItem(item)
            if code == selected:
                selected_row = row
        if self.task_list.count():
            self.task_list.setCurrentRow(selected_row)

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        was_connected = self._vehicle_connected
        self._vehicle_connected = bool(snapshot.autopilot_link)
        self._telemetry_mode = snapshot.flight_mode.strip().upper()
        self._armed = snapshot.armed
        if not self._vehicle_connected:
            self._requested_armed = None
        elif self._requested_armed is not None and snapshot.armed is self._requested_armed:
            self._requested_armed = None
        self._sync_arm_controls()

        if was_connected != self._vehicle_connected and not self._pending_commands:
            self._set_command_status(
                t(
                    "command.status.ready"
                    if self._vehicle_connected
                    else "command.status.disconnected",
                    self._language,
                ),
                "neutral" if self._vehicle_connected else "warning",
            )
        self._sync_command_availability()

        if not self._vehicle_connected:
            self._requested_mode = None
            self._mode_selection_dirty = False
            self._sync_mode_combo(self._telemetry_mode)
        elif self._requested_mode is not None:
            # SET_MODE arka planda yeni HEARTBEAT'i bekler. Eski telemetri
            # karelerinin kullanıcı seçimini veya doğrulanmış sonucu ezmesine
            # izin verme; yeni mod telemetride göründüğünde tekrar senkronize ol.
            if self._telemetry_mode == self._requested_mode:
                self._requested_mode = None
                self._mode_selection_dirty = False
                self._sync_mode_combo(self._telemetry_mode)
        elif not self._mode_selection_dirty:
            self._sync_mode_combo(self._telemetry_mode)

    def _on_mode_selected(self, _index: int) -> None:
        selected = str(self.mode_combo.currentData() or "").strip().upper()
        self._mode_selection_dirty = bool(selected and selected != self._telemetry_mode)

    def _sync_mode_combo(self, mode: str) -> None:
        index = self.mode_combo.findData(mode)
        if index < 0 or self.mode_combo.currentIndex() == index:
            return
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(index)
        self.mode_combo.blockSignals(False)

    def _profile_params(self) -> dict:
        return {
            spec.key: round(self.profile_inputs[spec.key].value(), spec.decimals)
            for spec in PROFILE_FIELDS
        }

    def _sync_arm_controls(self) -> None:
        if not self._vehicle_connected:
            state_key = "command.arm_state.disconnected"
            button_text_key = "command.arm_toggle.unavailable"
            button_role = None
            tooltip_key = "command.arm.tooltip"
        elif self._requested_armed is True:
            state_key = "command.arm_state.arming"
            button_text_key = "command.arm_button.pending"
            button_role = "success"
            tooltip_key = "command.arm.tooltip"
        elif self._requested_armed is False:
            state_key = "command.arm_state.disarming"
            button_text_key = "command.disarm_button.pending"
            button_role = "danger"
            tooltip_key = "command.disarm.tooltip"
        elif self._armed:
            state_key = "command.arm_state.armed"
            button_text_key = "command.disarm_button.ready"
            button_role = "danger"
            tooltip_key = "command.disarm.tooltip"
        else:
            state_key = "command.arm_state.disarmed"
            button_text_key = "command.arm_button.ready"
            button_role = "success"
            tooltip_key = "command.arm.tooltip"
        self.arm_label.setText(t(state_key, self._language).upper())
        self.arm_toggle_button.setText(t(button_text_key, self._language))
        self.arm_toggle_button.setToolTip(t(tooltip_key, self._language))
        self.arm_toggle_button.setProperty(
            "currentState",
            self._vehicle_connected
            and self._requested_armed is None
            and self._armed,
        )
        self.arm_toggle_button.setProperty(
            "pendingState",
            self._requested_armed is not None,
        )
        self.arm_toggle_button.setProperty("role", button_role)
        self.arm_toggle_button.style().unpolish(self.arm_toggle_button)
        self.arm_toggle_button.style().polish(self.arm_toggle_button)

        available = (
            self._vehicle_connected
            and not self._pending_commands
            and self._requested_armed is None
        )
        self.arm_toggle_button.setEnabled(available)

    def _toggle_arm_state(self) -> None:
        self._request_arm(not self._armed)

    def _request_arm(self, armed: bool) -> None:
        if not self._vehicle_connected or self._pending_commands:
            return
        if self._requested_armed is not None or self._armed is armed:
            return
        self._requested_armed = armed
        self._sync_arm_controls()
        self._emit_command("ARM" if armed else "DISARM")

    def command_started(self, code: str) -> None:
        normalized = code.strip().upper()
        if normalized == "FORCE_ARM":
            self._requested_armed = True
        self._pending_commands.append(normalized)
        self._set_command_status(
            t("command.status.sending", self._language).format(command=normalized),
            "warning",
        )
        self._sync_command_availability()

    def command_completed(self, result: CommandResult) -> None:
        code = result.request.code
        try:
            self._pending_commands.remove(code)
        except ValueError:
            pass

        if result.accepted:
            if code in {"SET_MODE", "APPLY_MODE"}:
                requested = result.request.parameters.get("mode", self._requested_mode)
                self._requested_mode = str(requested).strip().upper() if requested else None
                self._mode_selection_dirty = self._requested_mode is not None
        else:
            if code in {"ARM", "FORCE_ARM", "DISARM"}:
                self._requested_armed = None
            if code in {"SET_MODE", "APPLY_MODE"}:
                self._requested_mode = None
                self._mode_selection_dirty = False
                self._sync_mode_combo(self._telemetry_mode)
        self._sync_arm_controls()

        if self._pending_commands:
            pending = self._pending_commands[-1]
            self._set_command_status(
                t("command.status.sending", self._language).format(command=pending),
                "warning",
            )
        else:
            self._set_command_status(
                result.message,
                "success" if result.accepted else "danger",
            )
        self._sync_command_availability()

    def _sync_command_availability(self) -> None:
        available = self._vehicle_connected and not self._pending_commands
        for button in (
            self.mode_apply_button,
            self.profile_apply_button,
            self.task_apply_button,
        ):
            button.setEnabled(available)
        self.mode_combo.setEnabled(available)
        self._sync_arm_controls()

    def _set_command_status(self, message: str, state: str) -> None:
        self.command_status.setText(message)
        self.command_status.setProperty("state", state)
        self.command_status.style().unpolish(self.command_status)
        self.command_status.style().polish(self.command_status)

    def _emit_mode_update(self) -> None:
        params = self._profile_params()
        params["mode"] = self.mode_combo.currentData()
        self._requested_mode = str(params["mode"] or "").strip().upper() or None
        self._mode_selection_dirty = self._requested_mode is not None
        self.command_requested.emit("APPLY_MODE", params)

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
