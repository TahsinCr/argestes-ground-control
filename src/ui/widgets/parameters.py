from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from src.ui.config.i18n import t
from src.models.parameters import Parameter
from src.ui.widgets.common import PanelFrame, compact_button, horizontal_row


class ParametersPanel(PanelFrame):
    parameter_changed = pyqtSignal(str, object)
    apply_requested = pyqtSignal(dict)
    reset_requested = pyqtSignal()

    def __init__(self, title: str, subtitle: str, search_placeholder: str, parent=None) -> None:
        super().__init__(title, subtitle, parent)
        self._parameters: list[Parameter] = []
        self._original_values: dict[str, object] = {}
        self._dirty_values: dict[str, object] = {}
        self._language = "tr"
        self.setMinimumWidth(0)

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText(search_placeholder)
        self.search_input.textChanged.connect(self._rebuild_table)

        self.category_combo = QComboBox(self)
        self.category_combo.addItem("ALL", "all")
        self.category_combo.currentIndexChanged.connect(self._rebuild_table)

        self.body_layout.addWidget(horizontal_row(self.search_input, self.category_combo))

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["KEY", "VALUE", "UNIT", "CATEGORY", "DESCRIPTION"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._on_item_changed)
        self.body_layout.addWidget(self.table, 1)

        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(6)
        self.pending_label = QLabel(self)
        self.pending_label.setProperty("role", "note")
        footer_layout.addWidget(self.pending_label, 1)
        self.reset_button = compact_button("RESET")
        self.reset_button.clicked.connect(self.reset_to_originals)
        footer_layout.addWidget(self.reset_button)
        self.apply_button = compact_button("APPLY", "primary")
        self.apply_button.clicked.connect(self.apply_changes)
        footer_layout.addWidget(self.apply_button)
        self.body_layout.addWidget(footer)
        self._update_footer()

    def set_language(self, language: str) -> None:
        self._language = language
        self.apply_button.setText(t("parameters.apply", language).upper())
        self.reset_button.setText(t("parameters.reset", language).upper())
        self._update_footer()

    def set_parameters(self, parameters: Iterable[Parameter]) -> None:
        self._parameters = list(parameters)
        self._original_values = {item.key: item.value for item in self._parameters}
        self._dirty_values.clear()
        categories = sorted({item.category for item in self._parameters})
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("ALL", "all")
        for category in categories:
            self.category_combo.addItem(category.upper(), category)
        self.category_combo.blockSignals(False)
        self._rebuild_table()
        self._update_footer()

    def _parameter_for_key(self, key: str) -> Parameter | None:
        for parameter in self._parameters:
            if parameter.key == key:
                return parameter
        return None

    def _rebuild_table(self) -> None:
        query = self.search_input.text()
        category = self.category_combo.currentData()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for parameter in self._parameters:
            if not parameter.matches(query, category):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            items = [
                QTableWidgetItem(str(parameter.key)),
                QTableWidgetItem(str(parameter.value)),
                QTableWidgetItem(parameter.unit),
                QTableWidgetItem(parameter.category.upper()),
                QTableWidgetItem(parameter.description),
            ]
            items[0].setFlags(items[0].flags() & ~Qt.ItemFlag.ItemIsEditable)
            items[2].setFlags(items[2].flags() & ~Qt.ItemFlag.ItemIsEditable)
            items[3].setFlags(items[3].flags() & ~Qt.ItemFlag.ItemIsEditable)
            items[4].setFlags(items[4].flags() & ~Qt.ItemFlag.ItemIsEditable)
            for column, item in enumerate(items):
                self.table.setItem(row, column, item)
            self._apply_row_style(row, parameter.key)
        self.table.blockSignals(False)

    def _coerce_value(self, parameter: Parameter, raw_value: str) -> object:
        current_value = self._original_values.get(parameter.key, parameter.value)
        if isinstance(current_value, bool):
            return raw_value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(current_value, int) and not isinstance(current_value, bool):
            try:
                return int(float(raw_value))
            except ValueError:
                return current_value
        if isinstance(current_value, float):
            try:
                return float(raw_value)
            except ValueError:
                return current_value
        return raw_value

    def _apply_row_style(self, row: int, key: str) -> None:
        dirty = key in self._dirty_values
        item = self.table.item(row, 1)
        if not item:
            return
        if dirty:
            item.setBackground(QColor("#0e639c"))
            item.setForeground(QColor("#ffffff"))
        else:
            background = (
                self.table.palette().alternateBase().color()
                if row % 2
                else self.table.palette().base().color()
            )
            item.setBackground(background)
            item.setForeground(self.table.palette().text().color())

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        key_item = self.table.item(item.row(), 0)
        if not key_item:
            return
        key = key_item.text()
        parameter = self._parameter_for_key(key)
        if not parameter:
            return
        value = self._coerce_value(parameter, item.text())
        parameter.value = value
        if value == self._original_values.get(key):
            self._dirty_values.pop(key, None)
        else:
            self._dirty_values[key] = value
        self._apply_row_style(item.row(), key)
        self._update_footer()
        self.parameter_changed.emit(key, value)

    def _update_footer(self) -> None:
        count = len(self._dirty_values)
        self.pending_label.setText(t("parameters.pending", self._language).format(count=count))
        enabled = count > 0
        self.apply_button.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)

    def apply_changes(self) -> None:
        if not self._dirty_values:
            return
        self.apply_requested.emit(dict(self._dirty_values))
        self._original_values.update(self._dirty_values)
        self._dirty_values.clear()
        self._rebuild_table()
        self._update_footer()

    def reset_to_originals(self) -> None:
        if not self._dirty_values:
            return
        for parameter in self._parameters:
            if parameter.key in self._dirty_values:
                parameter.value = self._original_values.get(parameter.key, parameter.value)
        self._dirty_values.clear()
        self._rebuild_table()
        self._update_footer()
        self.reset_requested.emit()
