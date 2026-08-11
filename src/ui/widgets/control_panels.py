from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui.i18n import t
from src.ui.config.commands import (
    AUTONOMOUS_COMMANDS,
    FLIGHT_MODES,
    PROFILE_FIELDS,
    TASK_LABEL_KEYS,
)
from src.models.commands import CommandResult
from src.models.onboard import OnboardMissionState
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
        self._readout_cache: dict[str, str] = {}
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
            values = {key: "--" for key in self.readouts}
            values["flight_mode"] = snapshot.flight_mode
            self._set_readout_values(values)
            return

        arm_state = "ARMED" if snapshot.armed else "DISARMED"
        self._set_readout_values(
            {
                "flight_mode": snapshot.flight_mode,
                "vehicle_state": f"{arm_state} · {snapshot.system_status}",
                "altitude_agl": f"{snapshot.altitude_agl:0.1f} m",
                "altitude_msl": f"{snapshot.altitude_msl:0.1f} m",
                "climb_rate": f"{snapshot.climb_rate:+0.1f} m/s",
                "throttle": f"{snapshot.throttle_percent:0.0f}%",
                "load_factor": self._format_optional(
                    snapshot.load_factor_g,
                    "g",
                    2,
                ),
                "waypoint": (
                    f"{snapshot.wp_distance:0.0f} m · "
                    f"{snapshot.nav_bearing:03.0f}°"
                ),
                "home": (
                    f"{snapshot.home_distance:0.0f} m · "
                    f"{snapshot.home_heading:03.0f}°"
                ),
                "mission": f"{snapshot.mission_index}/{snapshot.mission_total}",
                "roll": f"{snapshot.roll:+0.1f}°",
                "pitch": f"{snapshot.pitch:+0.1f}°",
                "yaw": f"{snapshot.yaw % 360:03.0f}°",
                "acceleration_x": self._format_optional(
                    snapshot.acceleration_x_mps2,
                    "m/s²",
                    2,
                ),
                "acceleration_y": self._format_optional(
                    snapshot.acceleration_y_mps2,
                    "m/s²",
                    2,
                ),
                "acceleration_z": self._format_optional(
                    snapshot.acceleration_z_mps2,
                    "m/s²",
                    2,
                ),
                "wind": (
                    f"{snapshot.wind_speed:0.1f} m/s · "
                    f"{snapshot.wind_heading:03.0f}°"
                ),
                "ekf": "OK" if snapshot.ekf_ok else "WAIT",
            }
        )

    def _set_readout_values(self, values: dict[str, str]) -> None:
        for key, text in values.items():
            if self._readout_cache.get(key) == text:
                continue
            self._readout_cache[key] = text
            readout = self.readouts[key]
            readout.setText(text)
            if readout.toolTip():
                readout.setToolTip("")

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
        self._arm_render_signature: tuple[object, ...] | None = None
        self._onboard_state = OnboardMissionState()
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
        self.task_list.currentRowChanged.connect(
            lambda _row: self._sync_command_availability()
        )
        self.set_task_codes(
            (command.code for command in AUTONOMOUS_COMMANDS)
            if task_codes is None
            else task_codes
        )
        self.body_layout.addWidget(self.task_list, 1)

        self.task_apply_button = compact_button("TASK APPLY", "primary")
        self.task_apply_button.clicked.connect(self._emit_task_command)
        self.body_layout.addWidget(self.task_apply_button)

        self.onboard_status = QLabel(self)
        self.onboard_status.setProperty("role", "readout-label")
        self.onboard_status.setWordWrap(True)
        self.body_layout.addWidget(self.onboard_status)

        qr_row = QWidget(self)
        qr_layout = QHBoxLayout(qr_row)
        qr_layout.setContentsMargins(0, 0, 0, 0)
        qr_layout.setSpacing(4)
        self.qr_result = QLineEdit(self)
        self.qr_result.setReadOnly(True)
        self.qr_copy_button = compact_button("COPY", "success")
        self.qr_copy_button.clicked.connect(self._copy_qr)
        qr_layout.addWidget(self.qr_result, 1)
        qr_layout.addWidget(self.qr_copy_button)
        self.body_layout.addWidget(qr_row)

        task_control_row = QWidget(self)
        task_control_layout = QHBoxLayout(task_control_row)
        task_control_layout.setContentsMargins(0, 0, 0, 0)
        task_control_layout.setSpacing(4)
        self.task_stop_button = compact_button("STOP", "warning")
        self.task_cancel_button = compact_button("CANCEL", "danger")
        self.task_stop_button.clicked.connect(
            lambda: self.command_requested.emit(
                "ONBOARD_TASK_STOP", {"task": "kamikaze.execute"}
            )
        )
        self.task_cancel_button.clicked.connect(
            lambda: self.command_requested.emit(
                "ONBOARD_TASK_CANCEL", {"task": "kamikaze.execute"}
            )
        )
        task_control_layout.addWidget(self.task_stop_button)
        task_control_layout.addWidget(self.task_cancel_button)
        self.body_layout.addWidget(task_control_row)

        self.set_language(self._language)

    def set_language(self, language: str) -> None:
        self._language = language
        self._arm_render_signature = None
        self.mode_label.setText(t("command.quick", language).upper())
        self.profile_label.setText("")
        self.tasks_label.setText(t("command.tasks", language).upper())
        self.mode_apply_button.setText(t("command.apply", language).upper())
        self.profile_apply_button.setText(t("command.apply", language).upper())
        self.task_apply_button.setText(t("command.task_apply", language).upper())
        self.qr_copy_button.setText(t("onboard.qr.copy", language).upper())
        self.task_stop_button.setText(t("onboard.task.stop", language).upper())
        self.task_cancel_button.setText(t("onboard.task.cancel", language).upper())
        self.update_onboard_state(self._onboard_state)
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
            # SET_MODE waits for a new HEARTBEAT in the background. Do not let
            # stale telemetry overwrite the operator selection or confirmed
            # result; synchronize again when telemetry reports the new mode.
            if self._telemetry_mode == self._requested_mode:
                self._requested_mode = None
                self._mode_selection_dirty = False
                self._sync_mode_combo(self._telemetry_mode)
        elif not self._mode_selection_dirty:
            self._sync_mode_combo(self._telemetry_mode)

    def update_onboard_state(self, state: OnboardMissionState) -> None:
        self._onboard_state = state
        if not state.connected:
            text = t("onboard.status.offline", self._language)
            status = "danger"
        elif state.critical_event:
            text = t(
                "onboard.status.critical",
                self._language,
                event=state.critical_event,
            )
            status = "danger"
        elif state.waiting_for_guided:
            text = t("onboard.status.waiting_guided", self._language)
            status = "warning"
        elif state.active_task:
            text = t(
                "onboard.status.active",
                self._language,
                task=state.active_task,
                phase=state.phase,
            )
            status = "success" if state.phase == "succeeded" else "neutral"
        else:
            text = t(
                "onboard.status.ready",
                self._language,
                version=state.onboard_version or "-",
            )
            status = "success"
        self.onboard_status.setText(text)
        self.onboard_status.setProperty("state", status)
        self.onboard_status.style().unpolish(self.onboard_status)
        self.onboard_status.style().polish(self.onboard_status)
        self.qr_result.setText(state.qr_text or "")
        self.qr_result.setPlaceholderText(
            t(
                "onboard.qr.cancelled"
                if state.qr_status == "cancelled"
                else "onboard.qr.pending",
                self._language,
            )
        )
        self.qr_copy_button.setEnabled(bool(state.qr_text))
        task_active = bool(
            state.connected
            and state.active_task == "kamikaze.execute"
            and not state.terminal
        )
        self.task_stop_button.setEnabled(task_active and not state.pending_command)
        self.task_cancel_button.setEnabled(task_active and not state.pending_command)
        self._sync_command_availability()

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
        arm_label = t(state_key, self._language).upper()
        button_text = t(button_text_key, self._language)
        tooltip = t(tooltip_key, self._language)
        current_state = (
            self._vehicle_connected
            and self._requested_armed is None
            and self._armed
        )
        pending_state = self._requested_armed is not None
        available = (
            self._vehicle_connected
            and not self._pending_commands
            and self._requested_armed is None
        )
        render_signature = (
            arm_label,
            button_text,
            tooltip,
            button_role,
            current_state,
            pending_state,
            available,
        )
        if render_signature == self._arm_render_signature:
            return
        self._arm_render_signature = render_signature
        self.arm_label.setText(arm_label)
        self.arm_toggle_button.setText(button_text)
        self.arm_toggle_button.setToolTip(tooltip)
        self.arm_toggle_button.setProperty(
            "currentState",
            current_state,
        )
        self.arm_toggle_button.setProperty(
            "pendingState",
            pending_state,
        )
        self.arm_toggle_button.setProperty("role", button_role)
        self.arm_toggle_button.style().unpolish(self.arm_toggle_button)
        self.arm_toggle_button.style().polish(self.arm_toggle_button)

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
                t(
                    "command.result.success"
                    if result.accepted
                    else "command.result.failed",
                    self._language,
                    command=code,
                ),
                "success" if result.accepted else "danger",
            )
        self._sync_command_availability()

    def _sync_command_availability(self) -> None:
        if not hasattr(self, "task_apply_button"):
            return
        available = self._vehicle_connected and not self._pending_commands
        for button in (
            self.mode_apply_button,
            self.profile_apply_button,
        ):
            if button.isEnabled() != available:
                button.setEnabled(available)
        current = self.task_list.currentItem()
        selected_task = str(current.data(Qt.ItemDataRole.UserRole) or "") if current else ""
        task_available = available and (
            selected_task not in {"KAMIKAZE", "TARGET_LOCK", "WAYPOINT_TRACK", "SURVEY_AREA"}
            or (self._onboard_state.connected and not self._onboard_state.pending_command)
        )
        self.task_apply_button.setEnabled(task_available)
        if self.mode_combo.isEnabled() != available:
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

    def _copy_qr(self) -> None:
        text = self.qr_result.text()
        if text:
            from PyQt6.QtWidgets import QApplication

            QApplication.clipboard().setText(text)
