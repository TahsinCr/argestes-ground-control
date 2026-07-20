from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

from src.ui.widgets.common import PanelFrame


class VideoViewport(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._status = "NO SIGNAL"
        self.setMinimumHeight(320)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self._status = "LIVE"
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._pixmap.isNull():
            painter.end()
            return

        target_width = self.width()
        target_height = int(target_width * 9 / 16)
        if target_height > self.height():
            target_height = self.height()
            target_width = int(target_height * 16 / 9)
        x = (self.width() - target_width) // 2
        y = (self.height() - target_height) // 2
        painter.drawPixmap(x, y, target_width, target_height, self._pixmap)
        painter.end()


class VideoPanel(PanelFrame):
    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(title, subtitle, parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.viewport = VideoViewport(self)
        self.body_layout.addWidget(self.viewport, 1)

    def set_frame(self, image: QImage) -> None:
        self.viewport.set_frame(image)
