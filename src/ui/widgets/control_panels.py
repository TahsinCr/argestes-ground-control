from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, pyqtSignal
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
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.ui.i18n import t
from src.ui.config.commands import (
    AUTONOMOUS_COMMANDS,
    NumericCommandField,
    PROFILE_FIELDS,
    TASK_LABEL_KEYS,
)
from src.models.commands import CommandResult
from src.models.onboard import (
    ACTUATOR_TEST_TASK,
    KAMIKAZE_CHECK_TASK,
    OnboardMissionState,
)
from src.models.telemetry import TelemetrySnapshot
from src.ui.themes.style import ThemePalette
from src.ui.widgets.common import PanelFrame, compact_button


class TelemetryPanel(PanelFrame):
    CLOCK_PLACEHOLDER = "--:--:--.---"
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

        # Telemetry is a concise live instrument panel, not a scrollable
        # document.  The bottom command panel owns the overflow scroll area
        # when the sidebar becomes shorter.
        self.scroll_content = QWidget(self.body)
        self.scroll_content.setObjectName("TelemetryScrollContent")
        self.scroll_content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.content_layout = QVBoxLayout(self.scroll_content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(3)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.body_layout.addWidget(self.scroll_content)

        self.readouts: dict[str, QLabel] = {}
        self.readout_labels: dict[str, QLabel] = {}
        self.section_titles: dict[str, QLabel] = {}
        self._readout_cache: dict[str, str] = {}
        self._build_clock_strip()
        for section_key, items in self.SECTION_SPECS:
            self._add_readout_section(section_key, items)
        self.content_layout.addStretch(1)
        self.set_language(self._language)

    def _build_clock_strip(self) -> None:
        self.clock_strip = QFrame(self.scroll_content)
        self.clock_strip.setObjectName("TelemetrySection")
        layout = QGridLayout(self.clock_strip)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(2)

        self.server_clock_title = QLabel("", self.clock_strip)
        self.orin_clock_title = QLabel("", self.clock_strip)
        self.server_clock_value = QLabel(self.CLOCK_PLACEHOLDER, self.clock_strip)
        self.orin_clock_value = QLabel(self.CLOCK_PLACEHOLDER, self.clock_strip)
        for title in (self.server_clock_title, self.orin_clock_title):
            title.setProperty("role", "readout-label")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for value in (self.server_clock_value, self.orin_clock_value):
            value.setProperty("role", "readout-value")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setMinimumWidth(0)
            value.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
        layout.addWidget(self.server_clock_title, 0, 0)
        layout.addWidget(self.orin_clock_title, 0, 1)
        layout.addWidget(self.server_clock_value, 1, 0)
        layout.addWidget(self.orin_clock_value, 1, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        self.content_layout.addWidget(self.clock_strip)

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
        self.server_clock_title.setText(
            t("telemetry.clock.teknofest", language).upper()
        )
        self.orin_clock_title.setText(t("telemetry.clock.orin", language).upper())
        for section_key, items in self.SECTION_SPECS:
            self.section_titles[section_key].setText(
                t(f"telemetry.section.{section_key}", language).upper()
            )
            for key, label_key in items:
                self.readout_labels[key].setText(t(label_key, language).upper())

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self.update_server_time(
            snapshot.server_time if snapshot.server_link else None
        )
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

    def update_server_time(self, value: datetime | str | None) -> None:
        self._set_clock_value(self.server_clock_value, value)

    def update_orin_time(self, value: datetime | str | None) -> None:
        self._set_clock_value(self.orin_clock_value, value)

    def _set_clock_value(
        self,
        label: QLabel,
        value: datetime | str | None,
    ) -> None:
        timestamp = self._clock_datetime(value)
        if timestamp is None:
            text = self.CLOCK_PLACEHOLDER
            tooltip = ""
        else:
            timestamp = timestamp.astimezone(UTC)
            text = (
                timestamp.strftime("%H:%M:%S.")
                + f"{timestamp.microsecond // 1000:03d}"
            )
            tooltip = timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"
        if label.text() != text:
            label.setText(text)
        if label.toolTip() != tooltip:
            label.setToolTip(tooltip)

    @staticmethod
    def _clock_datetime(value: datetime | str | None) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            resolved = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return resolved if resolved.tzinfo is not None else resolved.replace(tzinfo=UTC)

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
    task_selected = pyqtSignal(str)

    def __init__(
        self,
        title: str,
        subtitle: str,
        parent=None,
        *,
        task_codes: Iterable[str] | None = None,
        flight_modes: Iterable[str] = (),
        command_codes: Iterable[str] = (),
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
        self._available_command_codes: frozenset[str] = frozenset()
        self._onboard_state = OnboardMissionState()
        self._ika_operation_pending = False
        self.setMinimumWidth(300)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("CommandTabs")
        self.tabs.setDocumentMode(False)
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.quick_tab = QWidget(self.tabs)
        self.task_tab = QWidget(self.tabs)
        self.quick_tab.setObjectName("CommandTabPage")
        self.task_tab.setObjectName("CommandTabPage")
        self.tabs.addTab(self.quick_tab, "")
        self.tabs.addTab(self.task_tab, "")
        self.body_layout.addWidget(self.tabs, 1)

        quick_page_layout = QVBoxLayout(self.quick_tab)
        quick_page_layout.setContentsMargins(0, 4, 0, 0)
        quick_page_layout.setSpacing(4)
        quick_scroll = QScrollArea(self.quick_tab)
        quick_scroll.setObjectName("CommandTabScroll")
        quick_scroll.setFrameShape(QFrame.Shape.NoFrame)
        quick_scroll.setWidgetResizable(True)
        quick_scroll.setMinimumHeight(0)
        quick_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        quick_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        quick_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        quick_content = QWidget(quick_scroll)
        quick_content.setObjectName("CommandTabContent")
        quick_layout = QVBoxLayout(quick_content)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        # Keep the two quick-control groups visually distinct without adding
        # another container or changing their command semantics.
        quick_layout.setSpacing(8)
        quick_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        quick_scroll.setWidget(quick_content)
        quick_page_layout.addWidget(quick_scroll, 1)

        task_page_layout = QVBoxLayout(self.task_tab)
        task_page_layout.setContentsMargins(0, 4, 0, 0)
        task_page_layout.setSpacing(4)
        task_scroll = QScrollArea(self.task_tab)
        task_scroll.setObjectName("CommandTabScroll")
        task_scroll.setFrameShape(QFrame.Shape.NoFrame)
        task_scroll.setWidgetResizable(True)
        task_scroll.setMinimumHeight(0)
        task_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        task_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        task_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        task_content = QWidget(task_scroll)
        task_content.setObjectName("CommandTabContent")
        task_layout = QVBoxLayout(task_content)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(4)
        task_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        task_scroll.setWidget(task_content)
        task_page_layout.addWidget(task_scroll, 1)

        self.quick_control_card = QFrame(quick_content)
        self.quick_control_card.setObjectName("QuickCommandCard")
        quick_control_layout = QVBoxLayout(self.quick_control_card)
        quick_control_layout.setContentsMargins(8, 7, 8, 8)
        quick_control_layout.setSpacing(4)
        quick_layout.addWidget(self.quick_control_card)

        self.mode_label = QLabel(self)
        self.mode_label.setProperty("role", "section-label")
        quick_control_layout.addWidget(self.mode_label)

        mode_row = QWidget(self)
        mode_layout = QGridLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setHorizontalSpacing(4)
        mode_layout.setVerticalSpacing(4)
        self.mode_combo = QComboBox(self)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_selected)
        self.mode_apply_button = compact_button("APPLY", "primary")
        self.mode_apply_button.clicked.connect(self._emit_mode_update)
        mode_layout.addWidget(self.mode_combo, 0, 0)
        mode_layout.addWidget(self.mode_apply_button, 0, 1)
        quick_control_layout.addWidget(mode_row)
        self.set_flight_modes(flight_modes)
        self.set_available_command_codes(command_codes)

        self.arm_label = QLabel(self)
        self.arm_label.setProperty("role", "section-label")
        quick_control_layout.addWidget(self.arm_label)

        self.arm_toggle_button = compact_button("ARM", "success")
        self.arm_toggle_button.clicked.connect(self._toggle_arm_state)
        quick_control_layout.addWidget(self.arm_toggle_button)

        self.command_status = QLabel(self)
        self.command_status.setObjectName("CommandStatus")
        self.command_status.setProperty("role", "readout-label")
        self.command_status.setWordWrap(True)

        self.profile_label = QLabel(self)
        self.profile_label.setProperty("role", "section-label")
        self.profile_label.setVisible(True)

        self.quick_profile_card = QFrame(quick_content)
        self.quick_profile_card.setObjectName("QuickCommandCard")
        quick_profile_layout = QVBoxLayout(self.quick_profile_card)
        quick_profile_layout.setContentsMargins(8, 7, 8, 8)
        quick_profile_layout.setSpacing(4)
        quick_profile_layout.addWidget(self.profile_label)
        quick_layout.addWidget(self.quick_profile_card)

        target_row = QWidget(self)
        target_layout = QGridLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setHorizontalSpacing(4)
        target_layout.setVerticalSpacing(3)

        self.profile_inputs: dict[str, QDoubleSpinBox] = {}
        self.profile_apply_buttons: dict[str, QPushButton] = {}
        for column, spec in enumerate(PROFILE_FIELDS):
            widget = QDoubleSpinBox(self)
            widget.setRange(spec.minimum, spec.maximum)
            widget.setDecimals(spec.decimals)
            widget.setValue(spec.default)
            widget.setSuffix(spec.suffix)
            widget.setMinimumWidth(0)
            widget.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
            self.profile_inputs[spec.key] = widget
            setattr(self, spec.key, widget)
            field_label = QLabel(spec.short_label, self)
            field_label.setProperty("role", "readout-label")
            target_layout.addWidget(field_label, 0, column)
            target_layout.addWidget(widget, 1, column)
            apply_button = compact_button("APPLY", "primary")
            apply_button.clicked.connect(
                lambda _checked=False, field=spec: self._emit_profile_update(field)
            )
            self.profile_apply_buttons[spec.key] = apply_button
            target_layout.addWidget(apply_button, 2, column)
        quick_profile_layout.addWidget(target_row)
        quick_layout.addStretch(1)
        quick_page_layout.addWidget(self.command_status)

        self.tasks_label = QLabel(self)
        self.tasks_label.setProperty("role", "section-label")
        task_layout.addWidget(self.tasks_label)

        self.task_list = QListWidget(self)
        self.task_list.setUniformItemSizes(True)
        self.task_list.setAlternatingRowColors(False)
        self.task_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.task_list.setMinimumHeight(76)
        self.task_list.currentRowChanged.connect(self._on_task_selection_changed)
        self.set_task_codes(
            (command.code for command in AUTONOMOUS_COMMANDS)
            if task_codes is None
            else task_codes
        )
        task_layout.addWidget(self.task_list, 1)

        self.actuator_card = QFrame(self)
        self.actuator_card.setObjectName("MetricCard")
        actuator_layout = QGridLayout(self.actuator_card)
        actuator_layout.setContentsMargins(5, 1, 5, 1)
        actuator_layout.setHorizontalSpacing(4)
        actuator_layout.setVerticalSpacing(3)
        self.actuator_title = QLabel(self.actuator_card)
        self.actuator_title.setObjectName("MetricTitle")
        self.actuator_status = QLabel(self.actuator_card)
        self.actuator_status.setWordWrap(False)
        self.actuator_status.setProperty("role", "readout-value")
        self.actuator_detail = QLabel(self.actuator_card)
        self.actuator_detail.setWordWrap(False)
        self.actuator_detail.setProperty("role", "readout-label")
        self.actuator_detail.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self.actuator_step_detail = QLabel(self.actuator_card)
        self.actuator_step_detail.setWordWrap(False)
        self.actuator_step_detail.setProperty("role", "readout-label")
        self.actuator_progress = QProgressBar(self.actuator_card)
        self.actuator_progress.setObjectName("ActuatorTestProgress")
        self.actuator_progress.setTextVisible(False)
        self.actuator_progress.setFixedHeight(7)
        self._actuator_progress_animation = QPropertyAnimation(
            self.actuator_progress,
            b"value",
            self,
        )
        self._actuator_progress_animation.setDuration(280)
        self._actuator_progress_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )
        actuator_layout.addWidget(self.actuator_title, 0, 0, 1, 2)
        actuator_layout.addWidget(self.actuator_status, 0, 2, 1, 4)
        actuator_layout.addWidget(self.actuator_step_detail, 1, 0, 1, 6)
        self.actuator_detail.hide()
        for column in range(6):
            actuator_layout.setColumnStretch(column, 1)
        for label in (
            self.actuator_title,
            self.actuator_status,
            self.actuator_detail,
            self.actuator_step_detail,
        ):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.actuator_title.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.actuator_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.actuator_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.actuator_step_detail.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.actuator_progress.hide()
        self.actuator_card.setFixedHeight(48)
        self.actuator_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.actuator_card.hide()

        self.kamikaze_card = QFrame(self)
        self.kamikaze_card.setObjectName("MetricCard")
        kamikaze_layout = QGridLayout(self.kamikaze_card)
        kamikaze_layout.setContentsMargins(5, 1, 5, 1)
        kamikaze_layout.setHorizontalSpacing(4)
        kamikaze_layout.setVerticalSpacing(3)
        self.kamikaze_title = QLabel(self.kamikaze_card)
        self.kamikaze_title.setObjectName("MetricTitle")
        self.kamikaze_status = QLabel(self.kamikaze_card)
        self.kamikaze_status.setWordWrap(False)
        self.kamikaze_status.setProperty("role", "readout-value")
        self.kamikaze_detail = QLabel(self.kamikaze_card)
        self.kamikaze_detail.setWordWrap(False)
        self.kamikaze_detail.setProperty("role", "readout-label")
        self.kamikaze_detail.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self.kamikaze_phase = QLabel(self.kamikaze_card)
        self.kamikaze_phase.setWordWrap(False)
        self.kamikaze_phase.setProperty("role", "readout-label")
        self.kamikaze_qr = QLabel(self.kamikaze_card)
        self.kamikaze_qr.setWordWrap(False)
        self.kamikaze_qr.setTextFormat(Qt.TextFormat.PlainText)
        self.kamikaze_qr.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.kamikaze_qr.setProperty("role", "readout-value")
        kamikaze_layout.addWidget(self.kamikaze_title, 0, 0, 1, 2)
        kamikaze_layout.addWidget(self.kamikaze_status, 0, 2, 1, 4)
        kamikaze_layout.addWidget(self.kamikaze_phase, 1, 0, 1, 3)
        kamikaze_layout.addWidget(self.kamikaze_qr, 1, 3, 1, 3)
        self.kamikaze_detail.hide()
        for column in range(6):
            kamikaze_layout.setColumnStretch(column, 1)
        for label in (
            self.kamikaze_title,
            self.kamikaze_status,
            self.kamikaze_detail,
            self.kamikaze_phase,
            self.kamikaze_qr,
        ):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.kamikaze_title.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.kamikaze_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.kamikaze_detail.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.kamikaze_phase.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.kamikaze_qr.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.kamikaze_card.setFixedHeight(48)
        self.kamikaze_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.kamikaze_card.hide()

        task_page_layout.addWidget(self.kamikaze_card)
        self.task_apply_button = compact_button("START", "primary")
        self.task_apply_button.clicked.connect(self._emit_task_command)

        self.onboard_status = QLabel(self)
        self.onboard_status.setObjectName("CommandStatus")
        self.onboard_status.setProperty("role", "readout-label")
        self.onboard_status.setWordWrap(True)

        task_control_row = QWidget(self)
        task_control_layout = QGridLayout(task_control_row)
        task_control_layout.setContentsMargins(0, 0, 0, 0)
        task_control_layout.setHorizontalSpacing(4)
        task_control_layout.setVerticalSpacing(4)
        self.task_cancel_button = compact_button("CANCEL", "danger")
        self.task_cancel_button.clicked.connect(
            lambda: self._emit_active_task_control("ONBOARD_TASK_CANCEL")
        )
        self.ika_calibration_button = compact_button("CALIBRATION", "warning")
        self.ika_calibration_button.clicked.connect(
            lambda: self.command_requested.emit("IKA_CALIBRATION_START", {})
        )
        self.ika_course_button = compact_button("COURSE", "primary")
        self.ika_course_button.clicked.connect(
            lambda: self.command_requested.emit("IKA_COURSE_START", {})
        )
        task_control_layout.addWidget(self.ika_calibration_button, 0, 0)
        task_control_layout.addWidget(self.ika_course_button, 0, 1, 1, 2)
        task_control_layout.addWidget(self.task_apply_button, 1, 0, 1, 2)
        task_control_layout.addWidget(self.task_cancel_button, 1, 2)
        task_control_layout.setColumnStretch(0, 1)
        task_control_layout.setColumnStretch(1, 1)
        task_page_layout.addWidget(task_control_row)
        self.task_progress = QProgressBar(self)
        self.task_progress.setObjectName("ActuatorTestProgress")
        self.task_progress.setTextVisible(False)
        self.task_progress.setFixedHeight(7)
        self.task_progress.hide()
        self._task_progress_animation = QPropertyAnimation(
            self.task_progress,
            b"value",
            self,
        )
        self._task_progress_animation.setDuration(280)
        self._task_progress_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )
        self._task_progress_visual_state = ""
        # Detailed mission feedback is hosted by Vehicle Management. The
        # sidebar retains only the compact last-operation text, matching the
        # Quick Commands tab.
        task_page_layout.addWidget(self.onboard_status)

        self.set_language(self._language)

    def set_language(self, language: str) -> None:
        self._language = language
        self._arm_render_signature = None
        self.tabs.setTabText(0, t("command.tab.quick", language))
        self.tabs.setTabText(1, t("command.tab.tasks", language))
        self.mode_label.setText(t("command.target_control", language).upper())
        self.profile_label.setText(t("command.target_profile", language).upper())
        self.tasks_label.setText(t("command.tasks", language).upper())
        self.mode_apply_button.setText(t("command.apply", language).upper())
        for spec in PROFILE_FIELDS:
            self.profile_apply_buttons[spec.key].setText(
                t(spec.apply_label_key, language).upper()
            )
        self.actuator_title.setText(t("onboard.actuator.title", language).upper())
        self.kamikaze_title.setText(t("onboard.kamikaze.title", language).upper())
        self.task_cancel_button.setText(t("onboard.task.cancel", language).upper())
        self.ika_calibration_button.setText(t("ika.calibration", language).upper())
        self.ika_course_button.setText(t("ika.course", language).upper())
        self.update_onboard_state(self._onboard_state)
        self._sync_arm_controls()
        self.mode_apply_button.setToolTip(t("command.mode_apply.tooltip", language))
        for spec in PROFILE_FIELDS:
            self.profile_apply_buttons[spec.key].setToolTip(
                t(spec.apply_tooltip_key, language)
            )
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
        supported_codes = {command.code for command in AUTONOMOUS_COMMANDS}
        codes = tuple(
            dict.fromkeys(
                str(code).strip().upper()
                for code in task_codes
                if str(code).strip().upper() in supported_codes
            )
        )
        self.task_list.clear()
        selected_row = -1
        for row, code in enumerate(codes):
            item = QListWidgetItem(code)
            item.setData(Qt.ItemDataRole.UserRole, code)
            self.task_list.addItem(item)
            if code == selected:
                selected_row = row
        if selected_row >= 0:
            self.task_list.setCurrentRow(selected_row)
        else:
            self.task_list.setCurrentRow(-1)
            self.task_list.clearSelection()

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        was_connected = self._vehicle_connected
        self._vehicle_connected = bool(snapshot.autopilot_link)
        self._telemetry_mode = snapshot.flight_mode.strip().upper()
        self._armed = snapshot.armed
        if self._requested_mode is not None and self._telemetry_mode == self._requested_mode:
            self._requested_mode = None
            self._mode_selection_dirty = False
        elif not self._mode_selection_dirty:
            self._sync_mode_combo(self._telemetry_mode)
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

    def set_flight_modes(self, flight_modes: Iterable[str]) -> None:
        """Populate modes from the connected autopilot, never from a vehicle family.

        The same panel is used by Plane, Copter, Rover and SITL.  Rebuilding
        only when the advertised set changes keeps telemetry updates cheap.
        """

        modes = tuple(
            dict.fromkeys(
                str(mode).strip().upper()
                for mode in flight_modes
                if str(mode).strip()
            )
        )
        current = tuple(
            str(self.mode_combo.itemData(index) or "").upper()
            for index in range(self.mode_combo.count())
        )
        if modes == current:
            return
        selected = str(self.mode_combo.currentData() or "").upper()
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        for mode in modes:
            self.mode_combo.addItem("LAND" if mode == "AUTOLAND" else mode, mode)
        index = self.mode_combo.findData(selected)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        self.mode_combo.blockSignals(False)
        self._mode_selection_dirty = False
        self._sync_command_availability()

    def set_available_command_codes(self, command_codes: Iterable[str]) -> None:
        """Retain advertised capabilities without imposing a vehicle-specific UI.

        The quick panel stays intentionally compact.  Command routing and
        feature availability continue to be supplied by the connected vehicle
        rather than a fixed aircraft-family button list.
        """
        self._available_command_codes = frozenset(
            str(code).strip().upper()
            for code in command_codes
            if str(code).strip()
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
            phase = self._onboard_phase_text(state)
            text = t(
                "onboard.status.active",
                self._language,
                task=state.active_task,
                phase=phase,
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
        task_active = bool(
            state.phase
            in {
                "requesting",
                "queued",
                "starting",
                "running",
                "pausing",
                "paused",
                "stopping",
                "reconciling",
            }
        )
        cancel_pending = state.pending_command == "cancel"
        self.task_cancel_button.setEnabled(
            state.connected and task_active and not cancel_pending
        )
        self._sync_command_availability()

    def _onboard_phase_text(self, state: OnboardMissionState) -> str:
        """Prefer the compact shared reason code without rejecting new codes."""
        if not state.reason_code:
            return state.phase
        key = f"onboard.reason.{state.reason_code}"
        translated = t(key, self._language)
        return state.reason_code if translated == f"[{key}]" else translated

    def _update_actuator_card(self) -> None:
        state = self._onboard_state
        selected = self._selected_task_code()
        visible = selected == "ACTUATOR_TEST"
        self.actuator_card.setVisible(visible)
        if not visible:
            return

        active = bool(
            state.active_task == ACTUATOR_TEST_TASK
            and not state.terminal
        )
        if state.phase in {"failed", "cancelled"}:
            status_text = state.last_error or t(
                "onboard.actuator.failed",
                self._language,
            )
            visual_state = "danger"
        elif state.actuator_notification_only:
            status_text = t("onboard.actuator.notification_only", self._language)
            visual_state = "success"
        elif state.phase == "succeeded":
            status_text = t("onboard.actuator.succeeded", self._language)
            visual_state = "success"
        elif state.phase == "reconciling":
            status_text = t("onboard.actuator.reconciling", self._language)
            visual_state = "warning"
        elif state.pending_command or state.phase in {"requesting", "starting"}:
            status_text = t("onboard.actuator.starting", self._language)
            visual_state = "warning"
        elif active:
            status_text = t("onboard.actuator.running", self._language)
            visual_state = "warning"
        else:
            status_text = t("onboard.actuator.ready", self._language)
            visual_state = "neutral"

        self.actuator_status.setText(
            t("onboard.feedback.status", self._language, value=status_text)
        )
        self.actuator_status.setToolTip(status_text)
        self.actuator_status.setProperty("state", visual_state)
        self.actuator_status.style().unpolish(self.actuator_status)
        self.actuator_status.style().polish(self.actuator_status)

        mission_id = state.mission_id if state.mission_id is not None else "—"
        step = state.actuator_step or "—"
        self.actuator_title.setText(
            f"{t('onboard.actuator.title', self._language).upper()} · "
            f"{t('onboard.feedback.id', self._language, value=mission_id)}"
        )
        id_text = t(
            "onboard.feedback.id",
            self._language,
            value=mission_id,
        )
        step_text = t(
            "onboard.feedback.step",
            self._language,
            value=step,
        )
        self.actuator_detail.setText(id_text)
        self.actuator_detail.setToolTip(id_text)
        self.actuator_step_detail.setText(step_text)
        self.actuator_step_detail.setToolTip(step_text)

        self.actuator_progress.setProperty("state", visual_state)
        self.actuator_progress.style().unpolish(self.actuator_progress)
        self.actuator_progress.style().polish(self.actuator_progress)
        if state.phase == "succeeded":
            self._set_actuator_progress(100)
        elif state.phase in {"failed", "cancelled", "idle"}:
            self._set_actuator_progress(0)
        elif active and state.actuator_step_count == 0:
            self._actuator_progress_animation.stop()
            self.actuator_progress.setRange(0, 0)
        else:
            self._set_actuator_progress(
                min(92, max(8, state.actuator_step_count * 16))
            )

    def _update_kamikaze_card(self) -> None:
        state = self._onboard_state
        selected = self._selected_task_code()
        visible = selected in {"KAMIKAZE", "KAMIKAZE_CHECK"}
        self.kamikaze_card.setVisible(visible)
        if not visible:
            return

        relevant_task = (
            KAMIKAZE_CHECK_TASK if selected == "KAMIKAZE_CHECK" else "kamikaze.execute"
        )
        relevant = state.active_task == relevant_task
        if relevant and state.critical_event:
            status_text = state.last_error or t(
                "onboard.kamikaze.critical",
                self._language,
                event=state.critical_event,
            )
            visual_state = "danger"
        elif relevant and state.phase == "failed":
            status_text = state.last_error or t(
                "onboard.kamikaze.failed", self._language
            )
            visual_state = "danger"
        elif relevant and state.phase == "cancelled":
            status_text = t("onboard.kamikaze.cancelled", self._language)
            visual_state = "warning"
        elif relevant and state.phase == "succeeded":
            status_text = t("onboard.kamikaze.succeeded", self._language)
            visual_state = "success"
        elif relevant and state.waiting_for_guided:
            status_text = t("onboard.kamikaze.waiting_guided", self._language)
            visual_state = "warning"
        elif relevant and state.phase == "reconciling":
            status_text = t("onboard.kamikaze.reconciling", self._language)
            visual_state = "warning"
        elif relevant and state.phase in {"requesting", "queued", "starting"}:
            status_text = t("onboard.kamikaze.starting", self._language)
            visual_state = "warning"
        elif relevant and state.phase == "running":
            status_text = t("onboard.kamikaze.running", self._language)
            visual_state = "success"
        elif state.target_ready:
            status_text = t("onboard.kamikaze.ready", self._language)
            visual_state = "neutral"
        else:
            status_text = t("onboard.kamikaze.target_pending", self._language)
            visual_state = "warning"

        self.kamikaze_status.setText(
            t("onboard.feedback.status", self._language, value=status_text)
        )
        self.kamikaze_status.setToolTip(status_text)
        self.kamikaze_status.setProperty("state", visual_state)
        self.kamikaze_status.style().unpolish(self.kamikaze_status)
        self.kamikaze_status.style().polish(self.kamikaze_status)

        mission_id = state.mission_id if relevant and state.mission_id is not None else "—"
        phase = state.phase if relevant and state.phase not in {"", "idle"} else "—"
        self.kamikaze_title.setText(
            f"{t('onboard.kamikaze_check.title' if selected == 'KAMIKAZE_CHECK' else 'onboard.kamikaze.title', self._language).upper()} · "
            f"{t('onboard.feedback.id', self._language, value=mission_id)}"
        )
        target = dict(state.target)
        try:
            target_text = f"{float(target['latitude_deg']):.7f}, {float(target['longitude_deg']):.7f}"
        except (KeyError, TypeError, ValueError):
            target_text = "—"
        detail_text = t(
            "onboard.feedback.id",
            self._language,
            value=mission_id,
        )
        self.kamikaze_detail.setText(detail_text)
        kamikaze_tooltip = t(
                "onboard.kamikaze.detail",
                self._language,
                mission_id=mission_id,
                phase=phase,
                target=target_text,
                revision=state.target_revision or "—",
        ).replace("\n", " · ")
        self.kamikaze_detail.setToolTip(kamikaze_tooltip)
        self.kamikaze_title.setToolTip(kamikaze_tooltip)
        phase_text = t(
            "onboard.feedback.phase",
            self._language,
            value=phase,
        )
        self.kamikaze_phase.setText(phase_text)
        self.kamikaze_phase.setToolTip(phase_text)

        qr_text = state.qr_text if relevant else None
        if qr_text:
            qr_label = t(
                "onboard.kamikaze.qr_result",
                self._language,
                qr=str(qr_text),
            )
            qr_state = "danger" if state.qr_status == "conflict" else "success"
        elif relevant and state.result_received:
            qr_label = t("onboard.kamikaze.qr_missing", self._language)
            qr_state = "warning"
        else:
            qr_label = t("onboard.kamikaze.qr_pending", self._language)
            qr_state = "neutral"
        self.kamikaze_qr.setText(qr_label)
        self.kamikaze_qr.setToolTip(qr_label)
        self.kamikaze_qr.setProperty("state", qr_state)
        self.kamikaze_qr.style().unpolish(self.kamikaze_qr)
        self.kamikaze_qr.style().polish(self.kamikaze_qr)

    def _on_task_selection_changed(self, _row: int) -> None:
        self._sync_command_availability()
        self.task_selected.emit(self._selected_task_code())

    @property
    def selected_task_code(self) -> str:
        return self._selected_task_code()

    def _selected_task_code(self) -> str:
        item = self.task_list.currentItem()
        return (
            str(item.data(Qt.ItemDataRole.UserRole) or "").strip().upper()
            if item is not None
            else ""
        )

    def _update_task_progress(self) -> None:
        selected = self._selected_task_code()
        state = self._onboard_state
        relevant_task = {
            "KAMIKAZE": "kamikaze.execute",
            "KAMIKAZE_CHECK": KAMIKAZE_CHECK_TASK,
            "ACTUATOR_TEST": ACTUATOR_TEST_TASK,
        }.get(selected, selected.lower())
        visible = bool(selected)
        self.task_progress.setVisible(visible)
        if not visible:
            return

        relevant = state.active_task == relevant_task
        phase = state.phase if relevant else "idle"
        visual_state = (
            "danger"
            if phase in {"failed", "cancelled"}
            else "success" if phase == "succeeded" else "warning"
        )
        if visual_state != self._task_progress_visual_state:
            self._task_progress_visual_state = visual_state
            self.task_progress.setProperty("state", visual_state)
            self.task_progress.style().unpolish(self.task_progress)
            self.task_progress.style().polish(self.task_progress)

        if phase in {"requesting", "queued", "starting", "running", "stopping"}:
            if selected == "ACTUATOR_TEST" and state.actuator_step_count > 0:
                value = min(92, max(8, state.actuator_step_count * 16))
            else:
                snapshot = dict(state.acknowledgement_snapshot)
                try:
                    value = int(float(snapshot.get("progress", -1)) * 100.0)
                except (TypeError, ValueError):
                    value = -1
                if value < 0 and phase == "running":
                    self._task_progress_animation.stop()
                    self.task_progress.setRange(0, 0)
                    return
                if value < 0:
                    value = {
                        "requesting": 5,
                        "queued": 10,
                        "starting": 18,
                        "stopping": 92,
                    }.get(phase, 0)
        elif phase in {"succeeded", "failed", "cancelled", "stopped"}:
            value = 100
        else:
            value = 0

        if self.task_progress.minimum() == 0 and self.task_progress.maximum() == 0:
            self.task_progress.setRange(0, 100)
        target = max(0, min(100, int(value)))
        if self.task_progress.value() == target:
            return
        self._task_progress_animation.stop()
        self._task_progress_animation.setStartValue(self.task_progress.value())
        self._task_progress_animation.setEndValue(target)
        self._task_progress_animation.start()


    def _set_actuator_progress(self, value: int) -> None:
        if self.actuator_progress.minimum() == 0 and self.actuator_progress.maximum() == 0:
            self.actuator_progress.setRange(0, 100)
        target = max(0, min(100, int(value)))
        if self.actuator_progress.value() == target:
            return
        self._actuator_progress_animation.stop()
        self._actuator_progress_animation.setStartValue(self.actuator_progress.value())
        self._actuator_progress_animation.setEndValue(target)
        self._actuator_progress_animation.start()

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

    def _profile_params(self, key: str | None = None) -> dict:
        fields = (
            (spec for spec in PROFILE_FIELDS if spec.key == key)
            if key is not None
            else PROFILE_FIELDS
        )
        return {
            spec.key: round(
                self.profile_inputs[spec.key].value(),
                spec.decimals,
            )
            for spec in fields
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
            self._vehicle_connected,
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
        # Connection state is already shown once in the persistent status
        # line below the panel.  Do not repeat it directly below the mode box.
        self.arm_label.setVisible(self._vehicle_connected)
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
        self._set_operation_status(
            normalized,
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
            self._set_operation_status(
                pending,
                t("command.status.sending", self._language).format(command=pending),
                "warning",
            )
        else:
            self._set_operation_status(
                code,
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
            *self.profile_apply_buttons.values(),
        ):
            if button.isEnabled() != available:
                button.setEnabled(available)
        current = self.task_list.currentItem()
        selected_task = str(current.data(Qt.ItemDataRole.UserRole) or "") if current else ""
        onboard_task = selected_task in {
            "KAMIKAZE",
            "KAMIKAZE_CHECK",
            "TARGET_LOCK",
            "ACTUATOR_TEST",
            "HSS_ESCAPE",
            "HSS_PREDICT",
            "HSS_TRANSIT",
            "WAYPOINT_TRACK",
            "SURVEY_AREA",
        }
        selected_canonical = self._canonical_ui_task(selected_task)
        same_task_active = bool(
            selected_canonical
            and self._onboard_state.active_task == selected_canonical
            and not self._onboard_state.terminal
        )
        task_available = bool(
            selected_task
            and onboard_task
            and self._onboard_state.connected
            and not self._pending_commands
            and self._onboard_state.pending_command != "cancel"
        )
        self.task_apply_button.setEnabled(task_available)
        ika_available = self._onboard_state.connected and not self._ika_operation_pending
        self.ika_calibration_button.setEnabled(ika_available)
        self.ika_course_button.setEnabled(ika_available)
        button_key = "command.task_restart" if same_task_active else "command.task_start"
        button_role = "warning" if same_task_active else "primary"
        button_text = t(button_key, self._language)
        if self._language.lower().startswith("tr"):
            button_text = button_text.replace("i", "İ").replace("ı", "I")
        self.task_apply_button.setText(button_text.upper())
        if self.task_apply_button.property("role") != button_role:
            self.task_apply_button.setProperty("role", button_role)
            self.task_apply_button.style().unpolish(self.task_apply_button)
            self.task_apply_button.style().polish(self.task_apply_button)
        mode_available = available and self.mode_combo.count() > 0
        if self.mode_combo.isEnabled() != mode_available:
            self.mode_combo.setEnabled(mode_available)
        if self.mode_apply_button.isEnabled() != mode_available:
            self.mode_apply_button.setEnabled(mode_available)
        task_control_active = bool(
            self._onboard_state.phase
            in {
                "requesting",
                "queued",
                "starting",
                "running",
                "pausing",
                "paused",
                "stopping",
                "reconciling",
            }
        )
        cancel_pending = bool(
            self._onboard_state.pending_command == "cancel"
            or "ONBOARD_TASK_CANCEL" in self._pending_commands
        )
        self.task_cancel_button.setEnabled(
            self._onboard_state.connected
            and task_control_active
            and not cancel_pending
        )
        self._sync_arm_controls()
        self._update_actuator_card()
        self._update_kamikaze_card()
        self._update_task_progress()

    def set_ika_operation(self, pending: bool, message: str, accepted: bool | None = None) -> None:
        self._ika_operation_pending = bool(pending)
        state = "warning" if pending else ("success" if accepted else "danger")
        self._set_task_status(message, state)
        self._sync_command_availability()

    def _set_command_status(self, message: str, state: str) -> None:
        self.command_status.setText(message)
        self.command_status.setProperty("state", state)
        self.command_status.style().unpolish(self.command_status)
        self.command_status.style().polish(self.command_status)

    def _set_task_status(self, message: str, state: str) -> None:
        self.onboard_status.setText(message)
        self.onboard_status.setProperty("state", state)
        self.onboard_status.style().unpolish(self.onboard_status)
        self.onboard_status.style().polish(self.onboard_status)
        self.task_progress.setToolTip(message)

    def _set_operation_status(self, code: str, message: str, state: str) -> None:
        if code.strip().upper() in {
            "SET_MISSION_TASK",
            "RESTART_MISSION_TASK",
            "ONBOARD_TASK_CANCEL",
        }:
            self._set_task_status(message, state)
            return
        self._set_command_status(message, state)

    def _emit_mode_update(self) -> None:
        params = self._profile_params()
        params["mode"] = self.mode_combo.currentData()
        self._requested_mode = str(params["mode"] or "").strip().upper() or None
        self._mode_selection_dirty = self._requested_mode is not None
        self.command_requested.emit("APPLY_MODE", params)

    def _emit_profile_update(self, field: NumericCommandField) -> None:
        self.command_requested.emit(
            field.command_code,
            self._profile_params(field.key),
        )

    def _emit_task_command(self) -> None:
        if not self._onboard_state.connected:
            self._set_task_status(
                t("onboard.status.offline", self._language),
                "danger",
            )
            return
        item = self.task_list.currentItem()
        if not item:
            return
        task = str(item.data(Qt.ItemDataRole.UserRole) or "").strip().upper()
        same_task_active = bool(
            self._canonical_ui_task(task) == self._onboard_state.active_task
            and not self._onboard_state.terminal
        )
        command = "RESTART_MISSION_TASK" if same_task_active else "SET_MISSION_TASK"
        if task == "ACTUATOR_TEST":
            self.command_requested.emit(
                command,
                {"task": task},
            )
            return
        params = self._profile_params()
        params["mode"] = self.mode_combo.currentData()
        params["task"] = task
        self.command_requested.emit(command, params)

    @staticmethod
    def _canonical_ui_task(task: str) -> str:
        normalized = str(task).strip().upper()
        return {
            "KAMIKAZE": "kamikaze.execute",
            "KAMIKAZE_CHECK": KAMIKAZE_CHECK_TASK,
            "TARGET_LOCK": "target.lock",
            "ACTUATOR_TEST": ACTUATOR_TEST_TASK,
        }.get(normalized, normalized.lower())

    def _emit_active_task_control(self, command: str) -> None:
        if not self._onboard_state.connected:
            self._set_task_status(
                t("onboard.status.offline", self._language),
                "danger",
            )
            return
        if self._onboard_state.phase not in {
            "requesting",
            "queued",
            "starting",
            "running",
            "pausing",
            "paused",
            "stopping",
            "reconciling",
        }:
            return
        task = self._onboard_state.active_task
        if not task:
            item = self.task_list.currentItem()
            selected = (
                str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
            )
            task = self._canonical_ui_task(selected) if selected else ""
        if not task:
            return
        self.command_requested.emit(command, {"task": task})

    def _emit_command(self, command: str) -> None:
        params = self._profile_params()
        params["mode"] = self.mode_combo.currentData()
        self.command_requested.emit(command, params)
