"""Theme-aware workspace glyphs."""

from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPalette,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QStyle


ICON_NAMES = {
    "layout": ("Layout", "Yerleşim"),
    "grid": ("Overview", "Genel görünüm"),
    "map": ("Map", "Harita"),
    "compass": ("Navigation", "Seyrüsefer"),
    "camera": ("Camera", "Kamera"),
    "chart": ("Telemetry", "Telemetri"),
    "flight": ("Vehicle", "Araç"),
    "logs": ("Logs", "Loglar"),
    "sliders": ("Parameters", "Parametreler"),
    "terminal": ("Commands", "Komutlar"),
    "folder": ("Workspace", "Çalışma alanı"),
    "layers": ("Group", "Grup"),
}

class _GlyphRenderer:
    def __init__(self, name):
        self.name = name

    def pixmap(self, size, mode, state):
        result = QPixmap(size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return result

    def scaledPixmap(self, size, mode, state, scale):
        result = self.pixmap(
            QSize(round(size.width() * scale), round(size.height() * scale)),
            mode,
            state,
        )
        result.setDevicePixelRatio(scale)
        return result

    def paint(self, painter, rect, mode, state):
        app = QApplication.instance()
        palette = app.palette()
        color = palette.color(QPalette.ColorRole.ButtonText)
        if mode == QIcon.Mode.Disabled:
            color.setAlphaF(0.3)
        elif mode == QIcon.Mode.Normal and state != QIcon.State.On:
            color.setAlphaF(0.72)
        name = self.name
        painter.save()
        if name not in ICON_NAMES and name != "plus":
            source = (
                app.style()
                .standardIcon(
                    getattr(
                        QStyle.StandardPixmap, name, QStyle.StandardPixmap.SP_FileIcon
                    )
                )
                .pixmap(rect.size())
            )
            if source.isNull():
                painter.restore()
                return
            tint = QPainter(source)
            tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            tint.fillRect(source.rect(), color)
            tint.end()
            painter.drawPixmap(rect, source)
            painter.restore()
            return
        side = min(rect.width(), rect.height())
        painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
        painter.scale(side / 24, side / 24)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                color,
                1.65,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()

        def line(x, y, a, b):
            path.moveTo(x, y)
            path.lineTo(a, b)

        if name in ("layout", "logs", "terminal", "chart", "sliders"):
            path.addRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
        if name == "layout":
            line(3, 9, 21, 9)
            line(9, 9, 9, 20)
        elif name == "logs":
            for y in (9, 13, 17):
                line(7, y, 17, y)
        elif name == "terminal":
            path.moveTo(7, 9)
            path.lineTo(10, 12)
            path.lineTo(7, 15)
            line(13, 15, 17, 15)
        elif name == "chart":
            path.moveTo(6, 16)
            path.lineTo(10, 12)
            path.lineTo(13, 14)
            path.lineTo(18, 8)
        elif name == "sliders":
            for x, y in ((8, 10), (16, 15)):
                line(x, 7, x, y - 2)
                line(x, y + 2, x, 17)
                path.addRoundedRect(QRectF(x - 2, y - 2, 4, 4), 1, 1)
        elif name == "grid":
            for x in (4, 14):
                for y in (4, 14):
                    path.addRoundedRect(QRectF(x, y, 6, 6), 1, 1)
        elif name == "camera":
            path.moveTo(3, 8)
            path.lineTo(7, 8)
            path.lineTo(9, 5)
            path.lineTo(15, 5)
            path.lineTo(17, 8)
            path.lineTo(21, 8)
            path.lineTo(21, 19)
            path.lineTo(3, 19)
            path.closeSubpath()
            path.addEllipse(QRectF(8, 9, 8, 8))
        elif name == "map":
            path.moveTo(3, 6)
            path.lineTo(9, 3)
            path.lineTo(15, 6)
            path.lineTo(21, 3)
            path.lineTo(21, 18)
            path.lineTo(15, 21)
            path.lineTo(9, 18)
            path.lineTo(3, 21)
            path.closeSubpath()
            line(9, 3, 9, 18)
            line(15, 6, 15, 21)
        elif name == "compass":
            path.addEllipse(QRectF(3, 3, 18, 18))
            path.moveTo(15, 8)
            path.lineTo(13, 14)
            path.lineTo(8, 16)
            path.lineTo(10, 10)
            path.closeSubpath()
        elif name == "flight":
            path.moveTo(12, 3)
            path.lineTo(14, 10)
            path.lineTo(21, 14)
            path.lineTo(21, 16)
            path.lineTo(14, 14)
            path.lineTo(14, 19)
            path.lineTo(17, 21)
            path.lineTo(7, 21)
            path.lineTo(10, 19)
            path.lineTo(10, 14)
            path.lineTo(3, 16)
            path.lineTo(3, 14)
            path.lineTo(10, 10)
            path.closeSubpath()
        elif name == "folder":
            path.moveTo(3, 6)
            path.lineTo(9, 6)
            path.lineTo(11, 9)
            path.lineTo(21, 9)
            path.lineTo(21, 20)
            path.lineTo(3, 20)
            path.closeSubpath()
        elif name == "layers":
            path.moveTo(3, 8)
            path.lineTo(12, 3)
            path.lineTo(21, 8)
            path.lineTo(12, 13)
            path.closeSubpath()
            for y in (12, 16):
                path.moveTo(3, y)
                path.lineTo(12, y + 5)
                path.lineTo(21, y)
        elif name == "plus":
            line(5, 12, 19, 12)
            line(12, 5, 12, 19)
        painter.drawPath(path)
        painter.restore()


def workspace_icon(name):
    # Native QIcon owns the rasters; no Python virtual callbacks survive
    # widgets or run during Qt's deferred window destruction.
    renderer = _GlyphRenderer(name)
    glyph = name
    if glyph not in ICON_NAMES and glyph != "plus":
        source = (
            QApplication.instance()
            .style()
            .standardIcon(
                getattr(QStyle.StandardPixmap, glyph, QStyle.StandardPixmap.SP_FileIcon)
            )
        )
        if source.pixmap(24, 24).isNull():
            return QIcon()
    result = QIcon()
    for scale in (1, 2):
        for mode, state in (
            (QIcon.Mode.Normal, QIcon.State.Off),
            (QIcon.Mode.Normal, QIcon.State.On),
            (QIcon.Mode.Active, QIcon.State.Off),
            (QIcon.Mode.Disabled, QIcon.State.Off),
        ):
            result.addPixmap(
                renderer.scaledPixmap(QSize(24, 24), mode, state, scale), mode, state
            )
    return result
