from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QSizePolicy, QWidget

from src.models.telemetry import TelemetrySnapshot
from src.themes.style import ThemePalette


class AttitudeIndicator(QWidget):
    def __init__(self, theme: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._snapshot = TelemetrySnapshot.zero()
        self.setMinimumHeight(205)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.update()

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._snapshot = snapshot
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(self._theme.surface))

        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        heading_rect = QRectF(outer.left() + 60, outer.top() + 6, outer.width() - 120, 26)
        hud_rect = QRectF(outer.left() + 62, outer.top() + 40, outer.width() - 124, outer.height() - 100)
        speed_rect = QRectF(outer.left() + 8, hud_rect.top(), 52, hud_rect.height())
        altitude_rect = QRectF(outer.right() - 60, hud_rect.top(), 52, hud_rect.height())
        footer_rect = QRectF(outer.left() + 8, outer.bottom() - 42, outer.width() - 16, 34)

        self._draw_horizon(painter, hud_rect)
        self._draw_bank_scale(painter, hud_rect)
        self._draw_fixed_aircraft(painter, hud_rect)
        self._draw_heading_tape(painter, heading_rect)
        self._draw_vertical_tape(
            painter,
            speed_rect,
            value=self._snapshot.air_speed,
            label="IAS",
            unit="m/s",
            major_step=5.0,
            color=QColor(self._theme.info),
        )
        self._draw_vertical_tape(
            painter,
            altitude_rect,
            value=self._snapshot.altitude_agl,
            label="ALT",
            unit="m",
            major_step=20.0,
            color=QColor(self._theme.warning),
        )
        self._draw_footer(painter, footer_rect)
        painter.end()

    def _draw_horizon(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        painter.setClipRect(rect)
        center = rect.center()
        pitch_scale = 4.2
        painter.translate(center)
        painter.rotate(-self._snapshot.roll)
        painter.translate(0, self._snapshot.pitch * pitch_scale)

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
        painter.rotate(-self._snapshot.roll)
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
        heading = self._snapshot.heading % 360
        pixels_per_degree = rect.width() / 70.0

        painter.setPen(QPen(QColor(self._theme.text), 1))
        for degrees in range(-40, 45, 5):
            value = (heading + degrees) % 360
            x = center_x + degrees * pixels_per_degree
            if rect.left() <= x <= rect.right():
                line_height = 10 if degrees % 10 == 0 else 5
                painter.drawLine(QPointF(x, rect.bottom() - line_height), QPointF(x, rect.bottom()))
                if degrees % 10 == 0:
                    label = f"{int(value):03d}"
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

    def _draw_vertical_tape(
        self,
        painter: QPainter,
        rect: QRectF,
        *,
        value: float,
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
        start_value = math.floor(value / major_step) * major_step
        painter.setPen(QPen(QColor(self._theme.text), 1))
        for offset in range(-5, 6):
            tick_value = start_value + offset * major_step
            y = center_y - ((tick_value - value) / major_step) * px_per_step
            if rect.top() + 8 <= y <= rect.bottom() - 8:
                painter.drawLine(QPointF(rect.right() - 12, y), QPointF(rect.right() - 4, y))
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
        painter.drawText(readout, Qt.AlignmentFlag.AlignCenter, f"{value:0.1f}")
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

    def _draw_footer(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        painter.fillRect(rect, QColor(self._theme.surface_alt))
        painter.setPen(QPen(QColor(self._theme.border_strong), 1))
        painter.drawRect(rect)
        painter.setPen(QColor(self._theme.text))
        painter.setFont(QFont("Noto Sans", 7, QFont.Weight.Bold))
        sections = [
            f"ROLL {self._snapshot.roll:+0.1f}",
            f"PITCH {self._snapshot.pitch:+0.1f}",
            f"HDG {self._snapshot.heading % 360:03.0f}",
            f"ALT {self._snapshot.altitude_agl:0.1f} m",
        ]
        columns = 2
        rows = 2
        segment_width = rect.width() / columns
        segment_height = rect.height() / rows
        for index, text in enumerate(sections):
            row = index // columns
            column = index % columns
            cell = QRectF(
                rect.left() + column * segment_width + 4,
                rect.top() + row * segment_height,
                segment_width - 8,
                segment_height,
            )
            painter.drawText(
                cell,
                Qt.AlignmentFlag.AlignCenter,
                text,
            )
            if column < columns - 1:
                divider_x = rect.left() + (column + 1) * segment_width
                painter.setPen(QPen(QColor(self._theme.border), 1))
                painter.drawLine(
                    QPointF(divider_x, cell.top() + 4),
                    QPointF(divider_x, cell.bottom() - 4),
                )
                painter.setPen(QColor(self._theme.text))
            if row == 0:
                divider_y = rect.top() + segment_height
                painter.setPen(QPen(QColor(self._theme.border), 1))
                painter.drawLine(
                    QPointF(rect.left() + 4, divider_y),
                    QPointF(rect.right() - 4, divider_y),
                )
                painter.setPen(QColor(self._theme.text))
        painter.restore()
