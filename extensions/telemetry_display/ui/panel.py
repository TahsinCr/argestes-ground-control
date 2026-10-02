from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QSizePolicy,
    QVBoxLayout,
    QLayout,
    QLabel,
    QFrame,
    QGridLayout,
)
from src.ui.widgets.common import PanelFrame
from src.ui.themes.style import ThemePalette
from src.ui.i18n import t
from src.models.telemetry import TelemetrySnapshot


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

    def set_visible_fields(self, fields=None):
        selected = set(fields) if fields is not None else set(self.readouts)
        for key, readout in self.readouts.items():
            readout.setVisible(key in selected)
            self.readout_labels[key].setVisible(key in selected)
        for section, items in self.SECTION_SPECS:
            active = any(key in selected for key, _ in items)
            title = self.section_titles[section]
            title.setVisible(active)
            index = self.content_layout.indexOf(title)
            self.content_layout.itemAt(index + 1).widget().setVisible(active)

    def __init__(
        self,
        title: str,
        subtitle: str,
        theme: ThemePalette,
        parent: QWidget | None = None,
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
        self.set_language(self._language)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme

    def set_language(self, language: str) -> None:
        self._language = language
        for section, items in self.SECTION_SPECS:
            self.section_titles[section].setText(
                t(f"telemetry.section.{section}", language).upper()
            )
            for key, label_key in items:
                self.readout_labels[key].setText(t(label_key, language).upper())

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        if not (snapshot.telemetry_link and snapshot.autopilot_link):
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

    def _set_readout_values(self, values: dict[str, str]) -> None:
        for key, text in values.items():
            if self._readout_cache.get(key) == text:
                continue
            self._readout_cache[key] = text
            self.readouts[key].setText(text)

    @staticmethod
    def _format_optional(value: float | None, unit: str, decimals: int) -> str:
        return "--" if value is None else f"{value:.{decimals}f} {unit}"
