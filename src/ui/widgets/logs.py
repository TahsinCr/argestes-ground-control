from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
)

from src.models.logs import LogEntry
from src.ui.i18n import localized_log_message, t
from src.ui.widgets.common import PanelFrame


class LevelBadgeDelegate(QStyledItemDelegate):
    COLOR_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        color = index.data(self.COLOR_ROLE)
        if not isinstance(color, QColor):
            super().paint(painter, option, index)
            return
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        badge_rect = option.rect.adjusted(7, 4, -7, -4)
        background = QColor(color)
        background.setAlpha(38)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(badge_rect, 5, 5)
        font = QFont(option.font)
        font.setPointSize(8)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            badge_rect,
            Qt.AlignmentFlag.AlignCenter,
            str(index.data() or "").upper(),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        del option, index
        return QSize(88, 27)


class EventLogPanel(PanelFrame):
    MAX_ENTRIES = 1000
    MAX_VISIBLE_ROWS = 500
    LEVELS = ("all", "debug", "info", "success", "warning", "danger")
    LEVEL_RANKS = {
        "debug": 0,
        "info": 1,
        "success": 2,
        "warning": 3,
        "danger": 4,
    }

    def __init__(self, title: str, subtitle: str, clear_text: str, parent=None) -> None:
        super().__init__(title, subtitle, parent)
        self._language = "tr"
        self._entries: list[LogEntry] = []
        self._follow_latest = True
        self._scroll_change_internal = False

        self.search_input = QLineEdit(self)
        self.search_input.setObjectName("LogSearchInput")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(170)
        self.search_input.setMaximumWidth(300)
        self.search_input.textChanged.connect(self._rebuild_table)
        self.add_header_widget(self.search_input)

        self.level_filter = QComboBox(self)
        self.level_filter.setObjectName("LogLevelFilter")
        self.level_filter.setMinimumWidth(142)
        self.level_filter.setFixedHeight(30)
        self.level_filter.setIconSize(QSize(11, 11))
        self.level_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.level_filter.setMaxVisibleItems(len(self.LEVELS))
        self.level_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.level_filter.setMinimumContentsLength(13)
        self.level_filter.view().setMinimumWidth(168)
        self.level_filter.currentIndexChanged.connect(self._rebuild_table)
        self.add_header_widget(self.level_filter)

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
        self._level_delegate = LevelBadgeDelegate(self.table)
        self.table.setItemDelegateForColumn(1, self._level_delegate)
        self.body_layout.addWidget(self.table, 1)
        self.table.verticalScrollBar().valueChanged.connect(
            self._on_scroll_value_changed
        )
        self.set_language("tr")


    def set_language(self, language: str) -> None:
        self._language = language
        self.table.setHorizontalHeaderLabels(
            [
                t("table.logs.time", language),
                t("table.logs.level", language),
                t("table.logs.source", language),
                t("table.logs.message", language),
            ]
        )
        self.search_input.setPlaceholderText(t("log.search.placeholder", language))
        selected_level = self.level_filter.currentData() or "all"
        self.level_filter.blockSignals(True)
        self.level_filter.clear()
        for level in self.LEVELS:
            self.level_filter.addItem(
                self._level_icon(level),
                t(f"log.level.{level}", language),
                level,
            )
        selected_index = self.level_filter.findData(selected_level)
        self.level_filter.setCurrentIndex(max(0, selected_index))
        self.level_filter.blockSignals(False)
        self.clear_button.setText(t("log.clear", language))
        self._rebuild_table()

    def _level_icon(self, level: str) -> QIcon:
        pixmap = QPixmap(11, 11)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = (
            QColor("#8CA1B5")
            if level == "all"
            else self._severity_color(level)
        )
        glow = QColor(color)
        glow.setAlpha(48)
        painter.setPen(QPen(glow, 1))
        painter.setBrush(color)
        painter.drawEllipse(2, 2, 7, 7)
        painter.end()
        return QIcon(pixmap)

    def add_entry(self, entry: LogEntry) -> None:
        follow_latest = self._follow_latest
        self._entries.append(entry)
        entries_trimmed = False
        if len(self._entries) > self.MAX_ENTRIES:
            del self._entries[: len(self._entries) - self.MAX_ENTRIES]
            entries_trimmed = True
        if entries_trimmed:
            self._rebuild_table(scroll_to_bottom=follow_latest)
            return
        if not self._entry_matches(entry):
            return
        scrollbar = self.table.verticalScrollBar()
        previous_scroll = scrollbar.value()
        self._scroll_change_internal = True
        try:
            if self.table.rowCount() >= self.MAX_VISIBLE_ROWS:
                self.table.removeRow(0)
            self._append_row(entry)
            if follow_latest:
                self.table.scrollToBottom()
            else:
                scrollbar.setValue(min(previous_scroll, scrollbar.maximum()))
        finally:
            self._scroll_change_internal = False
        self._follow_latest = follow_latest

    def _append_row(self, entry: LogEntry) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 27)
        severity_color = self._severity_color(entry.severity)
        severity_text = t(
            f"log.level.{self._normalized_severity(entry.severity)}",
            self._language,
        )
        values = (
            entry.timestamp.strftime("%H:%M:%S"),
            severity_text,
            entry.source,
            localized_log_message(entry, self._language),
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in (0, 1, 2):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if column == 1:
                item.setData(LevelBadgeDelegate.COLOR_ROLE, severity_color)
            self.table.setItem(row, column, item)

    def _rebuild_table(
        self,
        *_args: object,
        scroll_to_bottom: bool | None = None,
    ) -> None:
        scrollbar = self.table.verticalScrollBar()
        previous_scroll = scrollbar.value()
        if scroll_to_bottom is None:
            scroll_to_bottom = self._follow_latest
        matching = [entry for entry in self._entries if self._entry_matches(entry)]
        self._scroll_change_internal = True
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            for entry in matching[-self.MAX_VISIBLE_ROWS :]:
                self._append_row(entry)
            if matching and scroll_to_bottom:
                self.table.scrollToBottom()
            elif matching:
                scrollbar.setValue(min(previous_scroll, scrollbar.maximum()))
        finally:
            self.table.setUpdatesEnabled(True)
            self._scroll_change_internal = False
        self._follow_latest = bool(scroll_to_bottom or not matching)

    def _is_scrolled_to_bottom(self) -> bool:
        scrollbar = self.table.verticalScrollBar()
        return scrollbar.value() == scrollbar.maximum()

    def _on_scroll_value_changed(self, value: int) -> None:
        if self._scroll_change_internal:
            return
        scrollbar = self.table.verticalScrollBar()
        self._follow_latest = value == scrollbar.maximum()

    def _entry_matches(self, entry: LogEntry) -> bool:
        selected_level = str(self.level_filter.currentData() or "all")
        severity = self._normalized_severity(entry.severity)
        if selected_level != "all":
            minimum_rank = self.LEVEL_RANKS.get(selected_level, 0)
            entry_rank = self.LEVEL_RANKS.get(severity, self.LEVEL_RANKS["info"])
            if entry_rank < minimum_rank:
                return False
        query = self.search_input.text().strip().casefold()
        if not query:
            return True
        localized = localized_log_message(entry, self._language)
        searchable = " ".join(
            (
                entry.source,
                entry.message,
                localized,
                entry.action,
                entry.severity,
                " ".join(str(value) for value in entry.details.values()),
            )
        ).casefold()
        return query in searchable

    @staticmethod
    def _normalized_severity(severity: str) -> str:
        normalized = str(severity).strip().lower()
        if normalized in {"error", "critical", "fatal"}:
            return "danger"
        if normalized == "warn":
            return "warning"
        return normalized

    def _severity_color(self, severity: str) -> QColor:
        return {
            "debug": QColor("#8CA1B5"),
            "info": QColor("#3BA7FF"),
            "success": QColor("#37D67A"),
            "warning": QColor("#F4B942"),
            "danger": QColor("#FF6363"),
            "error": QColor("#FF6363"),
        }.get(str(severity).lower(), QColor("#8CA1B5"))

    def clear(self) -> None:
        self._entries.clear()
        self.table.setRowCount(0)
