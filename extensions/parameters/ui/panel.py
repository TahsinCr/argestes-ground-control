from __future__ import annotations

import math
from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ui.i18n import t
from src.extension.api.parameters import ParameterSyncState, ParameterSyncStatus
from src.models.parameters import Parameter
from src.ui.widgets.common import PanelFrame, compact_button, horizontal_row


class ParametersPanel(PanelFrame):
    apply_requested = pyqtSignal(dict)
    reset_requested = pyqtSignal()

    def __init__(
        self, title: str, subtitle: str, search_placeholder: str, parent=None
    ) -> None:
        super().__init__(title, subtitle, parent)
        self._parameters: list[Parameter] = []
        self._original_values: dict[str, object] = {}
        self._dirty_values: dict[str, object] = {}
        self._invalid_values: dict[str, str] = {}
        self._validation_errors: dict[str, str] = {}
        self._language = "tr"
        self._sync_status = ParameterSyncStatus()
        self._write_in_progress = False
        self._write_error: str | None = None
        self.setMinimumWidth(0)

        self.parameters_tab = QWidget(self)
        self.parameters_tab.setObjectName("CommandTabPage")
        self.parameters_tab.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.body_layout.addWidget(self.parameters_tab, 1)

        parameters_layout = QVBoxLayout(self.parameters_tab)
        parameters_layout.setContentsMargins(6, 6, 6, 6)
        parameters_layout.setSpacing(6)

        self.search_input = QLineEdit(self.parameters_tab)
        self.search_input.setPlaceholderText(search_placeholder)
        self.search_input.textChanged.connect(self._rebuild_table)

        self.category_combo = QComboBox(self.parameters_tab)
        self.category_combo.addItem("", "all")
        self.category_combo.currentIndexChanged.connect(self._rebuild_table)

        parameters_layout.addWidget(
            horizontal_row(self.search_input, self.category_combo)
        )

        self.table = QTableWidget(0, 5, self.parameters_tab)
        self.table.setHorizontalHeaderLabels(["", "", "", "", ""])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._on_item_changed)
        parameters_layout.addWidget(self.table, 1)

        footer = QWidget(self.parameters_tab)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(6)
        self.sync_status_label = QLabel(footer)
        self.sync_status_label.setProperty("role", "note")
        footer_layout.addWidget(self.sync_status_label)
        self.pending_label = QLabel(footer)
        self.pending_label.setProperty("role", "note")
        footer_layout.addWidget(self.pending_label, 1)
        self.reset_button = compact_button("RESET")
        self.reset_button.clicked.connect(self.reset_to_originals)
        footer_layout.addWidget(self.reset_button)
        self.apply_button = compact_button("APPLY", "primary")
        self.apply_button.clicked.connect(self.apply_changes)
        footer_layout.addWidget(self.apply_button)
        parameters_layout.addWidget(footer)

        self._update_footer()
        self.set_language(self._language)

    def set_language(self, language: str) -> None:
        self._language = language
        self.apply_button.setText(t("parameters.apply", language).upper())
        self.reset_button.setText(t("parameters.reset", language).upper())
        self.table.setHorizontalHeaderLabels(
            [
                t("table.parameters.key", language),
                t("table.parameters.value", language),
                t("table.parameters.default", language),
                t("table.parameters.unit", language),
                t("table.parameters.category", language),
            ]
        )
        if self.category_combo.count():
            self.category_combo.setItemText(0, t("table.parameters.all", language))
        self._update_footer()

    def set_sync_status(self, status: ParameterSyncStatus) -> None:
        self._sync_status = status
        self._update_footer()

    def set_parameters(self, parameters: Iterable[Parameter]) -> None:
        incoming = list(parameters)
        previous_by_key = {item.key: item for item in self._parameters}
        incoming_by_key = {item.key: item for item in incoming}
        previous_keys = set(previous_by_key)
        incoming_keys = set(incoming_by_key)
        previous_order = [item.key for item in self._parameters]
        retained_order = [item.key for item in incoming if item.key in previous_keys]
        metadata_compatible = all(
            (
                previous_by_key[key].unit,
                previous_by_key[key].category,
                previous_by_key[key].description,
                previous_by_key[key].value_type,
            )
            == (
                incoming_by_key[key].unit,
                incoming_by_key[key].category,
                incoming_by_key[key].description,
                incoming_by_key[key].value_type,
            )
            for key in previous_keys & incoming_keys
        )

        # Streaming parameter sync only adds rows and updates values. Handle
        # that common path incrementally so a growing ArduPilot catalogue does
        # not rebuild a thousand-row table for every published batch.
        if (
            previous_keys
            and previous_keys.issubset(incoming_keys)
            and retained_order == previous_order
            and metadata_compatible
        ):
            self._apply_incremental_parameters(
                incoming,
                previous_by_key=previous_by_key,
            )
            return

        self._parameters = incoming
        incoming_keys = {item.key for item in incoming}
        self._invalid_values = {
            key: value for key, value in self._invalid_values.items() if key in incoming_keys
        }
        self._validation_errors = {
            key: value
            for key, value in self._validation_errors.items()
            if key in incoming_keys
        }
        self._dirty_values = {
            key: value
            for key, value in self._dirty_values.items()
            if key in incoming_keys
        }
        self._original_values = {
            item.key: self._original_values.get(item.key, item.value)
            if item.key in self._dirty_values
            else item.value
            for item in incoming
        }
        self._replace_categories()
        self._rebuild_table()
        self._update_footer()

    def _replace_categories(self) -> None:
        categories = sorted({item.category for item in self._parameters})
        selected = self.category_combo.currentData() or "all"
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem(t("table.parameters.all", self._language), "all")
        for category in categories:
            self.category_combo.addItem(category.upper(), category)
        selected_index = self.category_combo.findData(selected)
        self.category_combo.setCurrentIndex(max(0, selected_index))
        self.category_combo.blockSignals(False)

    def _apply_incremental_parameters(
        self,
        incoming: list[Parameter],
        *,
        previous_by_key: dict[str, Parameter],
    ) -> None:
        incoming_by_key = {item.key: item for item in incoming}
        new_parameters = [item for item in incoming if item.key not in previous_by_key]
        self._parameters = incoming

        existing_categories = {
            str(self.category_combo.itemData(index))
            for index in range(self.category_combo.count())
        }
        for category in sorted(
            {item.category for item in new_parameters} - existing_categories
        ):
            self.category_combo.addItem(category.upper(), category)

        visible_rows = {
            self.table.item(row, 0).text(): row
            for row in range(self.table.rowCount())
            if self.table.item(row, 0) is not None
        }
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        try:
            for key, parameter in incoming_by_key.items():
                previous = previous_by_key.get(key)
                if key not in self._dirty_values:
                    self._original_values[key] = parameter.value
                row = visible_rows.get(key)
                if (
                    row is not None
                    and key not in self._dirty_values
                    and (previous is None or previous.value != parameter.value)
                ):
                    value_item = self.table.item(row, 1)
                    if value_item is not None:
                        value_item.setText(str(parameter.value))
                if row is not None and (
                    previous is None
                    or previous.default_value != parameter.default_value
                ):
                    default_item = self.table.item(row, 2)
                    if default_item is not None:
                        default_item.setText(
                            self._display_default(parameter.default_value)
                        )
            for parameter in new_parameters:
                if parameter.matches(
                    self.search_input.text(),
                    self.category_combo.currentData(),
                ):
                    self._append_parameter_row(parameter)
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.blockSignals(False)
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
            self._append_parameter_row(parameter)
        self.table.blockSignals(False)

    def _append_parameter_row(self, parameter: Parameter) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        items = [
            QTableWidgetItem(str(parameter.key)),
            QTableWidgetItem(
                self._invalid_values.get(
                    parameter.key,
                    str(self._dirty_values.get(parameter.key, parameter.value)),
                )
            ),
            QTableWidgetItem(self._display_default(parameter.default_value)),
            QTableWidgetItem(parameter.unit),
            QTableWidgetItem(parameter.category.upper()),
        ]
        items[0].setFlags(items[0].flags() & ~Qt.ItemFlag.ItemIsEditable)
        items[2].setFlags(items[2].flags() & ~Qt.ItemFlag.ItemIsEditable)
        items[3].setFlags(items[3].flags() & ~Qt.ItemFlag.ItemIsEditable)
        items[4].setFlags(items[4].flags() & ~Qt.ItemFlag.ItemIsEditable)
        for column, item in enumerate(items):
            self.table.setItem(row, column, item)
        self._apply_row_style(row, parameter.key)

    @staticmethod
    def _display_default(value: object | None) -> str:
        return "—" if value is None else str(value)

    def _coerce_value(self, parameter: Parameter, raw_value: str) -> object:
        current_value = self._original_values.get(parameter.key, parameter.value)
        if isinstance(current_value, bool):
            normalized = raw_value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ValueError("parameters.invalid_boolean")
        if isinstance(current_value, int) and not isinstance(current_value, bool):
            try:
                return int(raw_value.strip(), 10)
            except ValueError as exc:
                raise ValueError("parameters.invalid_integer") from exc
        if isinstance(current_value, float):
            try:
                value = float(raw_value)
            except ValueError as exc:
                raise ValueError("parameters.invalid_number") from exc
            if not math.isfinite(value):
                raise ValueError("parameters.invalid_number")
            return value
        return raw_value

    def _apply_row_style(self, row: int, key: str) -> None:
        dirty = key in self._dirty_values
        invalid = key in self._validation_errors
        item = self.table.item(row, 1)
        if not item:
            return
        if invalid:
            item.setBackground(QColor("#7a2525"))
            item.setForeground(QColor("#ffffff"))
            item.setToolTip(t(self._validation_errors[key], self._language))
        elif dirty:
            item.setBackground(QColor("#0e639c"))
            item.setForeground(QColor("#ffffff"))
            item.setToolTip("")
        else:
            background = (
                self.table.palette().alternateBase().color()
                if row % 2
                else self.table.palette().base().color()
            )
            item.setBackground(background)
            item.setForeground(self.table.palette().text().color())
            item.setToolTip("")

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
        raw_value = item.text()
        try:
            value = self._coerce_value(parameter, raw_value)
        except ValueError as exc:
            error_key = (
                str(exc)
                if str(exc).startswith("parameters.")
                else "parameters.invalid_values"
            )
            self._invalid_values[key] = raw_value
            self._validation_errors[key] = error_key
            self._apply_row_style(item.row(), key)
            self._update_footer()
            return
        self._invalid_values.pop(key, None)
        self._validation_errors.pop(key, None)
        parameter.value = value
        self._write_error = None
        if value == self._original_values.get(key):
            self._dirty_values.pop(key, None)
        else:
            self._dirty_values[key] = value
        self._apply_row_style(item.row(), key)
        self._update_footer()

    def _update_footer(self) -> None:
        self._update_sync_status_label()
        count = len(self._dirty_values)
        if self._write_in_progress:
            self.pending_label.setText(t("parameters.write_in_progress", self._language))
            self.pending_label.setToolTip("")
        elif self._validation_errors:
            self.pending_label.setText(
                t("parameters.invalid_values", self._language).format(
                    count=len(self._validation_errors)
                )
            )
            self.pending_label.setToolTip("")
        elif self._write_error:
            self.pending_label.setText(
                t("parameters.write_failed", self._language).format(count=count)
            )
            self.pending_label.setToolTip(self._write_error)
        else:
            self.pending_label.setText(
                t("parameters.pending", self._language).format(count=count)
            )
            self.pending_label.setToolTip(self._write_error or "")
        can_apply = (
            count > 0
            and not self._write_in_progress
            and not self._validation_errors
            and self._sync_status.state
            not in {
                ParameterSyncState.LOADING,
                ParameterSyncState.PARTIAL,
                ParameterSyncState.FAILED,
                ParameterSyncState.DISCONNECTED,
            }
        )
        can_reset = (
            (count > 0 or bool(self._validation_errors))
            and not self._write_in_progress
        )
        self.apply_button.setEnabled(can_apply)
        self.reset_button.setEnabled(can_reset)
        self.table.setEnabled(not self._write_in_progress)

    def _update_sync_status_label(self) -> None:
        status = self._sync_status
        expected = (
            str(status.expected_count)
            if status.expected_count is not None
            else "?"
        )
        if status.state == ParameterSyncState.LOADING:
            message = t("parameters.sync.loading", self._language).format(
                received=status.received_count,
                expected=expected,
            )
        elif status.state == ParameterSyncState.PARTIAL:
            message = t("parameters.sync.partial", self._language).format(
                received=status.received_count,
                expected=expected,
            )
        elif status.state == ParameterSyncState.COMPLETE:
            message = t("parameters.sync.complete", self._language).format(
                received=status.received_count,
            )
        elif status.state == ParameterSyncState.FAILED:
            message = t("parameters.sync.failed", self._language).format(
                received=status.received_count,
                expected=expected,
            )
        elif status.state == ParameterSyncState.DISCONNECTED:
            message = t("parameters.sync.disconnected", self._language)
        else:
            message = t("parameters.sync.idle", self._language)
        self.sync_status_label.setText(message)
        self.sync_status_label.setToolTip(message)

    def apply_changes(self) -> None:
        if not self._dirty_values or self._write_in_progress or self._validation_errors:
            return
        if self._sync_status.state in {
            ParameterSyncState.LOADING,
            ParameterSyncState.PARTIAL,
            ParameterSyncState.FAILED,
            ParameterSyncState.DISCONNECTED,
        }:
            return
        changes = dict(self._dirty_values)
        self._write_in_progress = True
        self._write_error = None
        self._rebuild_table()
        self._update_footer()
        self.apply_requested.emit(changes)

    def finish_apply(self, changes: dict[str, object], error: object | None) -> None:
        """Commit staged edits only after the backend confirms the write."""
        if not self._write_in_progress:
            return
        if error is None:
            parameters = {parameter.key: parameter for parameter in self._parameters}
            for key, value in changes.items():
                self._original_values[key] = value
                if key in parameters:
                    parameters[key].value = value
                if self._dirty_values.get(key) == value:
                    self._dirty_values.pop(key, None)
            self._write_error = None
        else:
            confirmed = getattr(error, "confirmed_values", {})
            parameters = {parameter.key: parameter for parameter in self._parameters}
            for key, value in confirmed.items():
                self._original_values[key] = value
                if key in parameters:
                    parameters[key].value = value
                if self._dirty_values.get(key) == changes.get(key):
                    self._dirty_values.pop(key, None)
            self._write_error = str(error) or error.__class__.__name__
        self._write_in_progress = False
        self._rebuild_table()
        self._update_footer()

    def reset_to_originals(self) -> None:
        if (
            (not self._dirty_values and not self._validation_errors)
            or self._write_in_progress
        ):
            return
        for parameter in self._parameters:
            if parameter.key in self._dirty_values:
                parameter.value = self._original_values.get(
                    parameter.key, parameter.value
                )
        self._dirty_values.clear()
        self._invalid_values.clear()
        self._validation_errors.clear()
        self._rebuild_table()
        self._update_footer()
        self.reset_requested.emit()
