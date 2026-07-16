from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PyQt6.QtWidgets import QHeaderView, QPushButton, QTableWidget, QTableWidgetItem, QWidget

from src.models.logs import LogEntry
from src.widgets.common import PanelFrame


class LevelBadge(QWidget):
    def __init__(self, text: str, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text.upper()
        self._color = color
        self._background = QColor(color)
        self._background.setAlpha(38)
        self.setMinimumSize(78, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        self._font.setPointSize(8)
        self._font.setWeight(QFont.Weight.Bold)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(4, 4, -4, -4)
        painter.setPen(QPen(self._color, 1))
        painter.setBrush(self._background)
        painter.drawRoundedRect(rect, 5, 5)
        painter.setFont(self._font)
        painter.setPen(self._color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class EventLogPanel(PanelFrame):
    def __init__(self, title: str, subtitle: str, clear_text: str, parent=None) -> None:
        super().__init__(title, subtitle, parent)
        self.clear_button = QPushButton(clear_text, self)
        self.clear_button.setProperty("compact", "true")
        self.clear_button.clicked.connect(self.clear)
        self.add_header_widget(self.clear_button)

        self.table = QTableWidget(0, 4, self)
        self.table.setObjectName("LogTable")
        self.table.setHorizontalHeaderLabels(["TIME", "LEVEL", "SOURCE", "MESSAGE"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 88)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.body_layout.addWidget(self.table, 1)

    def add_entry(self, entry: LogEntry) -> None:
        if self.table.rowCount() >= 500:
            self.table.removeRow(0)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 27)
        for column, value in enumerate(entry.to_row()):
            item = QTableWidgetItem("" if column == 1 else value)
            if column in (0, 1, 2):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, column, item)
        severity_color = self._severity_color(entry.severity)
        self.table.setCellWidget(row, 1, LevelBadge(entry.severity, severity_color, self.table))
        self.table.scrollToBottom()

    def _severity_color(self, severity: str) -> QColor:
        return {
            "info": QColor("#3BA7FF"),
            "success": QColor("#37D67A"),
            "warning": QColor("#F4B942"),
            "danger": QColor("#FF6363"),
        }.get(severity, QColor("#8CA1B5"))

    def clear(self) -> None:
        self.table.setRowCount(0)
