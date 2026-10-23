from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.models.telemetry import TelemetrySnapshot
from src.ui.i18n import t
from src.extension.api import ClockQuality
from src.ui.themes.style import ThemePalette
from src.ui.widgets.common import PanelFrame

from ..fields import (
    EXTRA_FIELDS,
    clock_field_specs,
    clock_reference_id,
    format_field,
)


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
    DEFAULT_FIELDS = tuple(key for _, items in SECTION_SPECS for key, _ in items)
    SECTION_SPECS = SECTION_SPECS + tuple(
        (section, tuple((key, f"telemetry.field.{key}") for key in keys))
        for section, keys in EXTRA_FIELDS.items()
    )

    def set_visible_fields(self, fields=None):
        self.configure_layout(
            {"fields": fields if fields is not None else list(self.DEFAULT_FIELDS)}
        )

    def configure_layout(self, config):
        fields = config.get("fields", self.DEFAULT_FIELDS)
        if not isinstance(fields, (list, tuple)):
            fields = self.DEFAULT_FIELDS
        for key in fields:
            if isinstance(key, str):
                self._ensure_clock_field(key)
        selected = list(
            dict.fromkeys(
                key for key in fields if isinstance(key, str) and key in self.readouts
            )
        )
        self._visible_clock_fields = {
            key for key in selected if key in self._clock_field_ids
        }
        try:
            columns = max(1, min(4, int(config.get("columns", 2))))
        except (TypeError, ValueError):
            columns = 2
        if config.get("layout") == "list":
            columns = 1
        groups = bool(config.get("grouped", True))
        for grid in self.section_grids.values():
            while grid.count():
                grid.takeAt(0)
            for column in range(8):
                grid.setColumnStretch(column, 0)
        for key, readout in self.readouts.items():
            readout.hide()
            self.readout_labels[key].hide()
        first = next(iter(self.section_frames))
        positions = {}
        order = []
        owners = {
            key: section for section, items in self._section_specs for key, _ in items
        }
        for key in selected:
            section = owners[key] if groups else first
            if section not in order:
                order.append(section)
            index = positions.get(section, 0)
            positions[section] = index + 1
            grid = self.section_grids[section]
            label, value = self.readout_labels[key], self.readouts[key]
            label.setParent(self.section_frames[section])
            value.setParent(self.section_frames[section])
            grid.addWidget(label, index // columns, (index % columns) * 2)
            grid.addWidget(value, index // columns, (index % columns) * 2 + 1)
            grid.setColumnStretch((index % columns) * 2 + 1, 1)
            label.show()
            value.show()
        for section, frame in self.section_frames.items():
            self.content_layout.removeWidget(frame)
            self.content_layout.removeWidget(self.section_titles[section])
            frame.setVisible(section in order)
            self.section_titles[section].setVisible(groups and section in order)
        for index, section in enumerate(order):
            self.content_layout.insertWidget(index * 2, self.section_titles[section])
            self.content_layout.insertWidget(
                index * 2 + 1, self.section_frames[section]
            )
        if self._visible_clock_fields:
            self._clock_timer.start()
            self._update_clock_values()
        else:
            self._clock_timer.stop()

    def __init__(
        self,
        title: str,
        subtitle: str,
        theme: ThemePalette,
        parent: QWidget | None = None,
        clock_references=None,
    ) -> None:
        super().__init__(title, subtitle, parent)
        self.setMinimumWidth(300)
        self._theme = theme
        self._language = "tr"
        content = QWidget(self.body)
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(3)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.body_layout.addWidget(content)
        self.readouts: dict[str, QLabel] = {}
        self.readout_labels: dict[str, QLabel] = {}
        self.section_titles: dict[str, QLabel] = {}
        self.section_frames = {}
        self.section_grids = {}
        self._readout_cache: dict[str, str] = {}
        self._clock_references = clock_references
        self._clock_field_ids: dict[str, str] = {}
        self._clock_field_titles: dict[str, str] = {}
        self._visible_clock_fields: set[str] = set()
        clock_items = []
        references = clock_references.references() if clock_references else ()
        for key, label, identifier in clock_field_specs(references):
            self._clock_field_ids[key] = identifier
            self._clock_field_titles[key] = label
            clock_items.append((key, label))
        self._section_specs = tuple(
            (
                section,
                items + tuple(clock_items) if section == "sensors" else items,
            )
            for section, items in self.SECTION_SPECS
        )
        for section_key, items in self._section_specs:
            title_label = QLabel("", content)
            title_label.setProperty("role", "section-label")
            self.section_titles[section_key] = title_label
            self.content_layout.addWidget(title_label)
            section = QFrame(content)
            section.setObjectName("TelemetrySection")
            grid = QGridLayout(section)
            self.section_frames[section_key] = section
            self.section_grids[section_key] = grid
            grid.setContentsMargins(7, 4, 7, 4)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(3)
            for index, (key, _) in enumerate(items):
                row, column = index // 2, (index % 2) * 2
                label = QLabel("", section)
                label.setProperty("role", "readout-label")
                value = QLabel("--", section)
                value.setProperty("role", "readout-value")
                value.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                value.setMinimumWidth(0)
                value.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
                )
                grid.addWidget(label, row, column)
                grid.addWidget(value, row, column + 1)
                grid.setColumnStretch(column + 1, 1)
                self.readout_labels[key] = label
                self.readouts[key] = value
            self.content_layout.addWidget(section)
        self.content_layout.addStretch(1)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock_values)
        self.set_language(self._language)
        self.set_visible_fields()

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme

    def set_language(self, language: str) -> None:
        self._language = language
        for section, items in self._section_specs:
            self.section_titles[section].setText(
                t(f"telemetry.section.{section}", language).upper()
            )
            for key, label_key in items:
                label = self._clock_field_titles.get(key)
                self.readout_labels[key].setText(
                    (label if label is not None else t(label_key, language)).upper()
                )

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        if not (snapshot.telemetry_link and snapshot.autopilot_link):
            values = {
                key: "--" for key in self.readouts if key not in self._clock_field_ids
            }
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
                "load_factor": self._format_optional(snapshot.load_factor_g, "g", 2),
                "waypoint": f"{snapshot.wp_distance:0.0f} m · {snapshot.nav_bearing:03.0f}°",
                "home": f"{snapshot.home_distance:0.0f} m · {snapshot.home_heading:03.0f}°",
                "mission": f"{snapshot.mission_index}/{snapshot.mission_total}",
                "roll": f"{snapshot.roll:+0.1f}°",
                "pitch": f"{snapshot.pitch:+0.1f}°",
                "yaw": f"{snapshot.yaw % 360:03.0f}°",
                "acceleration_x": self._format_optional(
                    snapshot.acceleration_x_mps2, "m/s²", 2
                ),
                "acceleration_y": self._format_optional(
                    snapshot.acceleration_y_mps2, "m/s²", 2
                ),
                "acceleration_z": self._format_optional(
                    snapshot.acceleration_z_mps2, "m/s²", 2
                ),
                "wind": f"{snapshot.wind_speed:0.1f} m/s · {snapshot.wind_heading:03.0f}°",
                "ekf": "OK" if snapshot.ekf_ok else "WAIT",
            }
        )
        self._set_readout_values(
            {
                key: format_field(snapshot, key)
                for keys in EXTRA_FIELDS.values()
                for key in keys
            }
        )

    def _ensure_clock_field(self, field_key: str) -> None:
        identifier = clock_reference_id(field_key)
        if identifier is None or field_key in self.readouts:
            return
        label_text = identifier
        self._clock_field_ids[field_key] = identifier
        self._clock_field_titles[field_key] = label_text
        self._section_specs = tuple(
            (
                section,
                items + ((field_key, label_text),) if section == "sensors" else items,
            )
            for section, items in self._section_specs
        )
        section = self.section_frames["sensors"]
        label = QLabel(label_text.upper(), section)
        label.setProperty("role", "readout-label")
        value = QLabel("--", section)
        value.setProperty("role", "readout-value")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setMinimumWidth(0)
        value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.readout_labels[field_key] = label
        self.readouts[field_key] = value

    def _update_clock_values(self) -> None:
        if self._clock_references is None:
            return
        values = {}
        for field_key in self._visible_clock_fields:
            identifier = self._clock_field_ids[field_key]
            status = self._clock_references.status(identifier)
            usable = status.quality not in {
                ClockQuality.UNAVAILABLE,
                ClockQuality.STALE,
            }
            value = status.value if usable else None
            prefix = "~" if status.quality is ClockQuality.ESTIMATED else ""
            values[field_key] = (
                f"{prefix}{value.strftime('%H:%M:%S')}" if value else "--"
            )
            tooltip = t(
                f"telemetry.clock.quality.{status.quality.value}", self._language
            )
            if status.updated_at is not None and status.age_seconds is not None:
                updated = status.updated_at.astimezone().strftime(
                    "%Y-%m-%d %H:%M:%S %Z"
                )
                age = f"{status.age_seconds:.1f} {t('telemetry.clock.seconds', self._language)}"
                tooltip = (
                    f"{tooltip}\n{t('telemetry.clock.last_updated', self._language)}: "
                    f"{updated}\n{t('telemetry.clock.sample_age', self._language)}: {age}"
                )
            self.readouts[field_key].setToolTip(tooltip)
        self._set_readout_values(values)

    def _set_readout_values(self, values: dict[str, str]) -> None:
        for key, text in values.items():
            if self._readout_cache.get(key) == text:
                continue
            self._readout_cache[key] = text
            self.readouts[key].setText(text)

    @staticmethod
    def _format_optional(value: float | None, unit: str, decimals: int) -> str:
        return "--" if value is None else f"{value:.{decimals}f} {unit}"
