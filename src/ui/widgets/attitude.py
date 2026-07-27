from __future__ import annotations

import math
from datetime import datetime

from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QSizePolicy, QWidget

from src.models.telemetry import TelemetrySnapshot
from src.ui.themes.style import ThemePalette


class AttitudeIndicator(QWidget):
    _ANIMATION_INTERVAL_MS = 16
    _VELOCITY_SMOOTHING_TIME_S = 0.16
    _MIN_SMOOTHING_TIME_S = 0.025
    _MAX_SMOOTHING_TIME_S = 0.14
    _SMOOTHING_PERIOD_FACTOR = 0.65
    _MIN_SAMPLE_PERIOD_S = 0.005
    _MAX_SAMPLE_PERIOD_S = 2.0
    _SAMPLE_PERIOD_BLEND = 0.2
    _SAMPLE_JITTER_BLEND = 0.25
    _MIN_PREDICTION_HORIZON_S = 0.04
    _MAX_PREDICTION_HORIZON_S = 1.6
    _MIN_PREDICTION_BUFFER_S = 0.025
    _PREDICTION_JITTER_FACTOR = 2.0
    _MIN_PREDICTION_BRAKE_TIME_S = 0.08
    _MAX_PREDICTION_BRAKE_TIME_S = 0.45
    _TELEMETRY_HOLD_MS = 2_500
    _ANIMATED_FIELDS = (
        "roll",
        "pitch",
        "heading",
        "air_speed",
        "ground_speed",
        "altitude_agl",
    )
    _ANGLE_FIELDS = {"roll", "heading"}
    _WRAPPED_FIELDS = {"heading"}
    _FIELD_SOURCE_TIMESTAMPS = {
        "roll": ("attitude_received_at",),
        "pitch": ("attitude_received_at",),
        "heading": ("global_position_received_at", "hud_received_at"),
        "air_speed": ("hud_received_at",),
        "ground_speed": ("global_position_received_at", "hud_received_at"),
        "altitude_agl": ("global_position_received_at",),
    }
    _MAX_FIELD_RATES = {
        "roll": 240.0,
        "pitch": 180.0,
        "heading": 240.0,
        "air_speed": 45.0,
        "ground_speed": 45.0,
        "altitude_agl": 120.0,
    }

    def __init__(self, theme: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._snapshot = TelemetrySnapshot.zero()
        self._target_values = self._snapshot_values(self._snapshot)
        self._display_values = dict(self._target_values)
        self._field_velocities = {field: 0.0 for field in self._ANIMATED_FIELDS}
        self._field_sample_periods = {
            field: 1.0 for field in self._ANIMATED_FIELDS
        }
        self._field_period_initialized = {
            field: False for field in self._ANIMATED_FIELDS
        }
        self._field_sample_jitter_s = {
            field: 0.0 for field in self._ANIMATED_FIELDS
        }
        self._field_sample_ages = {
            field: 0.0 for field in self._ANIMATED_FIELDS
        }
        self._field_last_sample_ms: dict[str, int | None] = {
            field: None for field in self._ANIMATED_FIELDS
        }
        self._field_last_source_stamp: dict[str, datetime | None] = {
            field: None for field in self._ANIMATED_FIELDS
        }
        self._has_live_sample = False
        self._last_live_sample_ms: int | None = None
        self._render_data_available = False
        self._shutting_down = False
        self._frame_clock = QElapsedTimer()
        self._frame_clock.start()
        self._last_frame_ms = self._frame_clock.elapsed()
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(self._ANIMATION_INTERVAL_MS)
        self._animation_timer.timeout.connect(self._advance_animation)
        self.setMinimumHeight(205)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.update()

    def shutdown(self) -> None:
        self._shutting_down = True
        self._animation_timer.stop()
        for field in self._field_velocities:
            self._field_velocities[field] = 0.0

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        if self._shutting_down:
            return
        self._snapshot = snapshot
        if snapshot.telemetry_link and snapshot.autopilot_link:
            values = self._snapshot_values(snapshot)
            sample_ms = self._frame_clock.elapsed()
            starts_new_stream = (
                not self._has_live_sample
                or self._last_live_sample_ms is None
                or sample_ms - self._last_live_sample_ms
                > self._TELEMETRY_HOLD_MS
            )
            if starts_new_stream:
                self._display_values.update(values)
                for field, value in values.items():
                    self._target_values[field] = value
                    self._field_velocities[field] = 0.0
                    self._field_sample_periods[field] = 1.0
                    self._field_period_initialized[field] = False
                    self._field_sample_jitter_s[field] = 0.0
                    self._field_sample_ages[field] = 0.0
                    self._field_last_sample_ms[field] = sample_ms
                    self._field_last_source_stamp[field] = (
                        self._field_source_stamp(snapshot, field)
                    )
            else:
                for field, value in values.items():
                    previous = self._target_values.get(field, value)
                    delta = (
                        self._shortest_angle_delta(previous, value)
                        if field in self._ANGLE_FIELDS
                        else value - previous
                    )
                    source_stamp = self._field_source_stamp(snapshot, field)
                    previous_source_stamp = self._field_last_source_stamp[field]
                    if source_stamp is not None:
                        if source_stamp == previous_source_stamp:
                            continue
                        sample_period_s = (
                            (source_stamp - previous_source_stamp).total_seconds()
                            if previous_source_stamp is not None
                            else 0.0
                        )
                    else:
                        # When external or fake telemetry sources omit source
                        # time, count only an actual value change as a sample.
                        if math.isclose(delta, 0.0, abs_tol=1e-6):
                            continue
                        previous_ms = self._field_last_sample_ms[field]
                        sample_period_s = (
                            (sample_ms - previous_ms) / 1000.0
                            if previous_ms is not None
                            else 0.0
                        )
                    # Other MAVLink messages may publish the same telemetry
                    # snapshot. An unchanged source timestamp is not treated
                    # as a new flight-dynamics sample.
                    if (
                        self._MIN_SAMPLE_PERIOD_S
                        <= sample_period_s
                        <= self._MAX_SAMPLE_PERIOD_S
                    ):
                        self._update_field_motion_estimate(
                            field,
                            value,
                            sample_period_s,
                        )
                        if self._field_period_initialized[field]:
                            previous_period = self._field_sample_periods[field]
                            deviation = abs(sample_period_s - previous_period)
                            self._field_sample_jitter_s[field] += (
                                deviation - self._field_sample_jitter_s[field]
                            ) * self._SAMPLE_JITTER_BLEND
                            self._field_sample_periods[field] = (
                                previous_period
                                + (sample_period_s - previous_period)
                                * self._SAMPLE_PERIOD_BLEND
                            )
                        else:
                            self._field_sample_periods[field] = sample_period_s
                            self._field_sample_jitter_s[field] = 0.0
                            self._field_period_initialized[field] = True
                    else:
                        self._field_velocities[field] = 0.0
                        self._field_period_initialized[field] = False
                        self._field_sample_jitter_s[field] = 0.0
                    self._target_values[field] = value
                    self._field_sample_ages[field] = 0.0
                    self._field_last_sample_ms[field] = sample_ms
                    self._field_last_source_stamp[field] = source_stamp
            self._has_live_sample = True
            self._last_live_sample_ms = sample_ms
            self._render_data_available = True
            if not self._animation_timer.isActive():
                self._last_frame_ms = sample_ms
                self._animation_timer.start()
        # Painting is driven by the precise animation timer. Scheduling another
        # repaint for every MAVLink message can double the frame workload when
        # attitude packets arrive faster than the display refresh.

    def _update_field_motion_estimate(
        self,
        field: str,
        value: float,
        sample_period_s: float,
    ) -> None:
        blend = 1.0 - math.exp(
            -sample_period_s / self._VELOCITY_SMOOTHING_TIME_S
        )
        previous = self._target_values.get(field, value)
        delta = (
            self._shortest_angle_delta(previous, value)
            if field in self._ANGLE_FIELDS
            else value - previous
        )
        rate_limit = self._MAX_FIELD_RATES[field]
        measured_rate = max(
            -rate_limit,
            min(rate_limit, delta / sample_period_s),
        )
        current_rate = self._field_velocities.get(field, 0.0)
        self._field_velocities[field] = (
            current_rate + (measured_rate - current_rate) * blend
        )

    @classmethod
    def _snapshot_values(cls, snapshot: TelemetrySnapshot) -> dict[str, float]:
        values: dict[str, float] = {}
        for field in cls._ANIMATED_FIELDS:
            value = float(getattr(snapshot, field))
            if math.isfinite(value):
                values[field] = value
        return values

    @classmethod
    def _field_source_stamp(
        cls,
        snapshot: TelemetrySnapshot,
        field: str,
    ) -> datetime | None:
        stamps = [
            value
            for name in cls._FIELD_SOURCE_TIMESTAMPS[field]
            if isinstance((value := getattr(snapshot, name, None)), datetime)
        ]
        return max(stamps) if stamps else None

    @staticmethod
    def _shortest_angle_delta(current: float, target: float) -> float:
        return (target - current + 180.0) % 360.0 - 180.0

    @classmethod
    def _interpolate_angle(
        cls,
        current: float,
        target: float,
        factor: float,
    ) -> float:
        return (current + cls._shortest_angle_delta(current, target) * factor) % 360.0

    def _advance_animation(self) -> None:
        current_ms = self._frame_clock.elapsed()
        elapsed_s = max(0.001, min(0.1, (current_ms - self._last_frame_ms) / 1000.0))
        self._last_frame_ms = current_ms
        changed = self._animation_step(elapsed_s)
        available = self.has_recent_telemetry
        availability_changed = available != self._render_data_available
        self._render_data_available = available
        if changed or availability_changed:
            self.update()
        if not available:
            for field in self._field_velocities:
                self._field_velocities[field] = 0.0
            self._animation_timer.stop()

    def _animation_step(self, elapsed_s: float) -> bool:
        elapsed_s = max(0.0, elapsed_s)
        for field in self._field_sample_ages:
            self._field_sample_ages[field] += elapsed_s
        changed = False
        for field, target in self._target_values.items():
            target = self._projected_target(field, target)
            current = self._display_values[field]
            smoothing_time_s = max(
                self._MIN_SMOOTHING_TIME_S,
                min(
                    self._MAX_SMOOTHING_TIME_S,
                    self._field_sample_periods[field]
                    * self._SMOOTHING_PERIOD_FACTOR,
                ),
            )
            factor = 1.0 - math.exp(-elapsed_s / smoothing_time_s)
            if field in self._ANGLE_FIELDS:
                delta = self._shortest_angle_delta(current, target)
                if abs(delta) < 0.01:
                    next_value = (
                        target % 360.0
                        if field in self._WRAPPED_FIELDS
                        else current + delta
                    )
                else:
                    next_value = current + delta * factor
                    if field in self._WRAPPED_FIELDS:
                        next_value %= 360.0
            else:
                delta = target - current
                next_value = target if abs(delta) < 0.005 else current + delta * factor
            if not math.isclose(current, next_value, abs_tol=1e-6):
                self._display_values[field] = next_value
                changed = True
        return changed

    def _projected_target(self, field: str, target: float) -> float:
        velocity = self._field_velocities.get(field, 0.0)
        if math.isclose(velocity, 0.0, abs_tol=1e-6):
            return target

        horizon_s = max(
            self._MIN_PREDICTION_HORIZON_S,
            min(
                self._MAX_PREDICTION_HORIZON_S,
                self._field_sample_periods[field]
                + max(
                    self._MIN_PREDICTION_BUFFER_S,
                    self._field_sample_jitter_s[field]
                    * self._PREDICTION_JITTER_FACTOR,
                ),
            ),
        )
        sample_age_s = self._field_sample_ages[field]
        if sample_age_s <= horizon_s:
            projected_time_s = sample_age_s
        else:
            braking_age_s = sample_age_s - horizon_s
            brake_time_s = max(
                self._MIN_PREDICTION_BRAKE_TIME_S,
                min(
                    self._MAX_PREDICTION_BRAKE_TIME_S,
                    self._field_sample_periods[field] * 0.75,
                ),
            )
            projected_time_s = horizon_s + brake_time_s * (
                1.0
                - math.exp(
                    -braking_age_s / brake_time_s
                )
            )
        projected = target + velocity * projected_time_s
        return projected % 360.0 if field in self._WRAPPED_FIELDS else projected

    @property
    def has_recent_telemetry(self) -> bool:
        if not self._has_live_sample or self._last_live_sample_ms is None:
            return False
        return (
            self._frame_clock.elapsed() - self._last_live_sample_ms
            <= self._TELEMETRY_HOLD_MS
        )

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(self._theme.surface))

        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        heading_rect = QRectF(outer.left() + 60, outer.top() + 6, outer.width() - 120, 26)
        hud_rect = QRectF(
            outer.left() + 62,
            outer.top() + 40,
            outer.width() - 124,
            outer.height() - 50,
        )
        speed_rect = QRectF(outer.left() + 8, hud_rect.top(), 52, hud_rect.height())
        altitude_rect = QRectF(outer.right() - 60, hud_rect.top(), 52, hud_rect.height())

        self._draw_horizon(painter, hud_rect)
        self._draw_bank_scale(painter, hud_rect)
        self._draw_fixed_aircraft(painter, hud_rect)
        self._draw_heading_tape(painter, heading_rect)
        self._draw_vertical_tape(
            painter,
            speed_rect,
            value=self._display_values["air_speed"]
            if self._render_data_available
            else None,
            label="IAS",
            unit="m/s",
            major_step=5.0,
            color=QColor(self._theme.info),
        )
        self._draw_vertical_tape(
            painter,
            altitude_rect,
            value=self._display_values["altitude_agl"]
            if self._render_data_available
            else None,
            label="ALT",
            unit="m",
            major_step=20.0,
            color=QColor(self._theme.warning),
        )
        self._draw_flight_readouts(painter, hud_rect)
        painter.end()

    def _draw_horizon(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        painter.setClipRect(rect)
        center = rect.center()
        pitch_scale = 4.2
        painter.translate(center)
        painter.rotate(-self._display_values["roll"])
        painter.translate(0, self._display_values["pitch"] * pitch_scale)

        painter.fillRect(
            QRectF(-rect.width(), -rect.height() * 2, rect.width() * 2, rect.height() * 2),
            QColor("#274057"),
        )
        painter.fillRect(
            QRectF(-rect.width(), 0, rect.width() * 2, rect.height() * 2),
            QColor("#5a3d24"),
        )

        painter.setPen(QPen(QColor("#d2c6a0"), 2))
        painter.drawLine(QPointF(-rect.width(), 0), QPointF(rect.width(), 0))

        painter.setPen(QPen(QColor("#f2b84b"), 1.4))
        for degrees in range(-30, 35, 5):
            if degrees == 0:
                continue
            y = -(degrees * pitch_scale)
            width = 64 if degrees % 10 == 0 else 34
            painter.drawLine(QPointF(-width, y), QPointF(width, y))
            if degrees % 10 == 0:
                painter.drawText(
                    QRectF(-width - 26, y - 8, 22, 16),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(abs(degrees)),
                )
                painter.drawText(
                    QRectF(width + 4, y - 8, 22, 16),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    str(abs(degrees)),
                )
        painter.restore()

        painter.setPen(QPen(QColor(self._theme.border_strong), 1))
        painter.drawRect(rect)

    def _draw_bank_scale(self, painter: QPainter, rect: QRectF) -> None:
        center = rect.center()
        radius = min(rect.width(), rect.height()) * 0.42
        arc_rect = QRectF(center.x() - radius, rect.top() - 6, radius * 2, radius * 2)
        painter.setPen(QPen(QColor(self._theme.border_strong), 1.2))
        painter.drawArc(arc_rect, 30 * 16, 120 * 16)

        painter.save()
        painter.translate(center.x(), rect.top() + radius - 6)
        for degrees in (-60, -45, -30, -20, -10, 10, 20, 30, 45, 60):
            painter.save()
            painter.rotate(degrees)
            length = 12 if degrees % 30 == 0 else 8
            painter.setPen(QPen(QColor(self._theme.text), 1.2))
            painter.drawLine(QPointF(0, -radius), QPointF(0, -radius + length))
            painter.restore()
        painter.restore()

        painter.save()
        painter.translate(center.x(), rect.top() + radius - 6)
        painter.rotate(-self._display_values["roll"])
        pointer = QPolygonF(
            [
                QPointF(0, -radius - 3),
                QPointF(-6, -radius + 8),
                QPointF(6, -radius + 8),
            ]
        )
        painter.setBrush(QColor(self._theme.warning))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(pointer)
        painter.restore()

    def _draw_fixed_aircraft(self, painter: QPainter, rect: QRectF) -> None:
        center = rect.center()
        painter.setPen(QPen(QColor(self._theme.text), 2))
        painter.drawLine(QPointF(center.x() - 44, center.y()), QPointF(center.x() - 10, center.y()))
        painter.drawLine(QPointF(center.x() + 44, center.y()), QPointF(center.x() + 10, center.y()))
        painter.drawLine(QPointF(center.x() - 10, center.y()), QPointF(center.x(), center.y() + 10))
        painter.drawLine(QPointF(center.x() + 10, center.y()), QPointF(center.x(), center.y() + 10))
        painter.drawLine(QPointF(center.x(), center.y() - 16), QPointF(center.x(), center.y() + 16))

    def _draw_heading_tape(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        painter.fillRect(rect, QColor(self._theme.surface_alt))
        painter.setPen(QPen(QColor(self._theme.border_strong), 1))
        painter.drawRect(rect)
        center_x = rect.center().x()
        heading = self._display_values["heading"] % 360
        pixels_per_degree = rect.width() / 70.0

        if not self._render_data_available:
            painter.setPen(QColor(self._theme.muted_text))
            painter.setFont(QFont("Noto Sans", 8, QFont.Weight.Bold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "---")
            painter.restore()
            return

        painter.setPen(QPen(QColor(self._theme.text), 1))
        first_tick = math.ceil((heading - 40.0) / 5.0) * 5
        last_tick = math.floor((heading + 40.0) / 5.0) * 5
        for tick in range(first_tick, last_tick + 1, 5):
            degrees = tick - heading
            value = tick % 360
            x = center_x + degrees * pixels_per_degree
            if rect.left() <= x <= rect.right():
                cardinal = value % 45 == 0
                numeric = value % 10 == 0
                distance_to_cardinal = min(value % 45, 45 - (value % 45))
                show_label = cardinal or (numeric and distance_to_cardinal >= 10)
                major = numeric or cardinal
                line_height = 10 if major else 5
                painter.drawLine(QPointF(x, rect.bottom() - line_height), QPointF(x, rect.bottom()))
                if show_label:
                    label = (
                        self._cardinal_direction(value)
                        if cardinal
                        else f"{int(value):03d}"
                    )
                    painter.drawText(
                        QRectF(x - 14, rect.top() + 2, 28, 11),
                        Qt.AlignmentFlag.AlignCenter,
                        label,
                    )

        caret = QPolygonF(
            [
                QPointF(center_x, rect.bottom() - 2),
                QPointF(center_x - 6, rect.bottom() - 10),
                QPointF(center_x + 6, rect.bottom() - 10),
            ]
        )
        painter.setBrush(QColor(self._theme.info))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(caret)

        painter.restore()

    def _draw_flight_readouts(self, painter: QPainter, rect: QRectF) -> None:
        connected = self._render_data_available
        box_width = min(94.0, max(76.0, (rect.width() - 18.0) / 2.0))
        box_height = 36.0
        speed_box = QRectF(
            rect.left() + 6.0,
            rect.bottom() - box_height - 6.0,
            box_width,
            box_height,
        )
        heading_box = QRectF(
            rect.right() - box_width - 6.0,
            rect.bottom() - box_height - 6.0,
            box_width,
            box_height,
        )

        background = QColor(self._theme.background)
        background.setAlpha(218)
        for box in (speed_box, heading_box):
            painter.fillRect(box, background)
            painter.setPen(QPen(QColor(self._theme.border_strong), 1))
            painter.drawRect(box)

        painter.setFont(QFont("Noto Sans", 8, QFont.Weight.Bold))
        if connected:
            painter.setPen(QColor(self._theme.info))
            painter.drawText(
                speed_box.adjusted(6, 2, -4, -17),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"AS {self._display_values['air_speed']:0.1f} m/s",
            )
            painter.setPen(QColor(self._theme.success))
            painter.drawText(
                speed_box.adjusted(6, 17, -4, -2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"GS {self._display_values['ground_speed']:0.1f} m/s",
            )
            painter.setPen(QColor(self._theme.warning))
            painter.drawText(
                heading_box.adjusted(4, 2, -4, -17),
                Qt.AlignmentFlag.AlignCenter,
                f"HDG {self._display_values['heading'] % 360:03.0f}°",
            )
            painter.setPen(QColor(self._theme.text))
            painter.drawText(
                heading_box.adjusted(4, 17, -4, -2),
                Qt.AlignmentFlag.AlignCenter,
                self._cardinal_direction(self._display_values["heading"]),
            )
        else:
            painter.setPen(QColor(self._theme.muted_text))
            painter.drawText(speed_box, Qt.AlignmentFlag.AlignCenter, "AS --  ·  GS --")
            painter.drawText(heading_box, Qt.AlignmentFlag.AlignCenter, "HDG --")

    @staticmethod
    def _cardinal_direction(heading: float) -> str:
        directions = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
        index = int(((heading % 360.0) + 22.5) // 45.0) % len(directions)
        return directions[index]

    def _draw_vertical_tape(
        self,
        painter: QPainter,
        rect: QRectF,
        *,
        value: float | None,
        label: str,
        unit: str,
        major_step: float,
        color: QColor,
    ) -> None:
        painter.save()
        painter.fillRect(rect, QColor(self._theme.surface_alt))
        painter.setPen(QPen(QColor(self._theme.border_strong), 1))
        painter.drawRect(rect)

        center_y = rect.center().y()
        px_per_step = 18.0
        painter.setPen(QPen(QColor(self._theme.text), 1))
        if value is not None:
            start_value = math.floor(value / major_step) * major_step
            for offset in range(-5, 6):
                tick_value = start_value + offset * major_step
                y = center_y - ((tick_value - value) / major_step) * px_per_step
                if rect.top() + 8 <= y <= rect.bottom() - 8:
                    painter.drawLine(
                        QPointF(rect.right() - 12, y),
                        QPointF(rect.right() - 4, y),
                    )
                    painter.drawText(
                        QRectF(rect.left() + 4, y - 8, rect.width() - 18, 16),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        f"{tick_value:0.0f}",
                    )

        readout = QRectF(rect.left() + 4, center_y - 12, rect.width() - 8, 24)
        painter.fillRect(readout, QColor(self._theme.background))
        painter.setPen(QPen(color, 1.2))
        painter.drawRect(readout)
        painter.setPen(color)
        painter.setFont(QFont("Noto Sans", 10, QFont.Weight.Bold))
        painter.drawText(
            readout,
            Qt.AlignmentFlag.AlignCenter,
            f"{value:0.1f}" if value is not None else "--",
        )
        painter.setPen(QColor(self._theme.muted_text))
        painter.setFont(QFont("Noto Sans", 8, QFont.Weight.Bold))
        painter.drawText(
            rect.adjusted(4, 4, -4, -4),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            label,
        )
        painter.drawText(
            rect.adjusted(4, 4, -4, -4),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            unit,
        )
        painter.restore()
