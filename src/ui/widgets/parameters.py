from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.ui.i18n import t
from src.models.parameters import Parameter
from src.models.target_selection import TargetSelectionResult
from src.ui.config.commands import TASK_LABEL_KEYS
from src.ui.widgets.common import PanelFrame, compact_button, horizontal_row


_TARGET_SELECTOR_COLUMNS = (
    "team", "score", "lock_probability", "pre_score", "tail_access",
    "distance", "altitude_delta", "data_age", "merge_eta", "lock_start",
    "lock_duration", "turn", "quality", "reason",
)


class _NumericTableItem(QTableWidgetItem):
    """Table item that sorts by its numeric value instead of formatted text."""

    def __init__(self, text: str, value: float) -> None:
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole, float(value))

    def __lt__(self, other: QTableWidgetItem) -> bool:
        return float(self.data(Qt.ItemDataRole.UserRole)) < float(
            other.data(Qt.ItemDataRole.UserRole)
        )


class ParametersPanel(PanelFrame):
    parameter_changed = pyqtSignal(str, object)
    apply_requested = pyqtSignal(dict)
    reset_requested = pyqtSignal()
    target_selector_visibility_changed = pyqtSignal(bool)

    def __init__(self, title: str, subtitle: str, search_placeholder: str, parent=None) -> None:
        super().__init__(title, subtitle, parent)
        self._parameters: list[Parameter] = []
        self._original_values: dict[str, object] = {}
        self._dirty_values: dict[str, object] = {}
        self._language = "tr"
        self._selected_task = ""
        self._task_pages: dict[str, QWidget] = {}
        self.setMinimumWidth(0)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("CommandTabs")
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.parameters_tab = QWidget(self.tabs)
        self.parameters_tab.setObjectName("CommandTabPage")
        self.mission_commands_tab = QWidget(self.tabs)
        self.target_selector_tab = QWidget(self.tabs)
        self.mission_commands_tab.setObjectName("CommandTabPage")
        self.target_selector_tab.setObjectName("CommandTabPage")
        self.tabs.addTab(self.parameters_tab, "")
        self.tabs.addTab(self.mission_commands_tab, "")
        self.tabs.addTab(self.target_selector_tab, "")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.body_layout.addWidget(self.tabs, 1)

        parameters_layout = QVBoxLayout(self.parameters_tab)
        parameters_layout.setContentsMargins(6, 6, 6, 6)
        parameters_layout.setSpacing(6)

        self.search_input = QLineEdit(self.parameters_tab)
        self.search_input.setPlaceholderText(search_placeholder)
        self.search_input.textChanged.connect(self._rebuild_table)

        self.category_combo = QComboBox(self.parameters_tab)
        self.category_combo.addItem("", "all")
        self.category_combo.currentIndexChanged.connect(self._rebuild_table)

        parameters_layout.addWidget(horizontal_row(self.search_input, self.category_combo))

        self.table = QTableWidget(0, 5, self.parameters_tab)
        self.table.setHorizontalHeaderLabels(["", "", "", "", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._on_item_changed)
        parameters_layout.addWidget(self.table, 1)

        footer = QWidget(self.parameters_tab)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(6)
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

        self.mission_layout = QVBoxLayout(self.mission_commands_tab)
        # The task-specific page may use the full tab surface. Its persistent
        # feedback strip intentionally reaches both panel edges.
        self.mission_layout.setContentsMargins(0, 8, 0, 0)
        self.mission_layout.setSpacing(6)
        self.mission_command_stack = QStackedWidget(self.mission_commands_tab)
        self.mission_command_stack.setObjectName("MissionCommandStack")
        self.mission_layout.addWidget(self.mission_command_stack, 1)

        self.mission_empty_page = QWidget(self.mission_command_stack)
        empty_layout = QVBoxLayout(self.mission_empty_page)
        empty_layout.setContentsMargins(12, 12, 12, 12)
        empty_layout.addStretch(1)
        self.mission_empty_card = QFrame(self.mission_empty_page)
        self.mission_empty_card.setObjectName("MetricCard")
        card_layout = QVBoxLayout(self.mission_empty_card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(8)
        self.mission_empty_marker = QLabel("◇", self.mission_empty_card)
        self.mission_empty_marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mission_empty_marker.setProperty("role", "section-label")
        self.mission_empty_title = QLabel(self.mission_empty_card)
        self.mission_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mission_empty_title.setObjectName("MetricTitle")
        self.mission_empty_description = QLabel(self.mission_empty_card)
        self.mission_empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mission_empty_description.setWordWrap(True)
        self.mission_empty_description.setProperty("role", "readout-label")
        card_layout.addWidget(self.mission_empty_marker)
        card_layout.addWidget(self.mission_empty_title)
        card_layout.addWidget(self.mission_empty_description)
        empty_layout.addWidget(self.mission_empty_card)
        empty_layout.addStretch(1)
        self.mission_command_stack.addWidget(self.mission_empty_page)

        target_selector_layout = QVBoxLayout(self.target_selector_tab)
        target_selector_layout.setContentsMargins(6, 6, 6, 6)
        target_selector_layout.setSpacing(6)
        self.target_selector_summary = QLabel(self.target_selector_tab)
        self.target_selector_summary.setProperty("role", "readout-label")
        self.target_selector_summary.setWordWrap(True)
        target_selector_layout.addWidget(self.target_selector_summary)
        self.target_selector_table = QTableWidget(self.target_selector_tab)
        self.target_selector_table.setObjectName("TargetSelectorTable")
        self.target_selector_table.setColumnCount(len(_TARGET_SELECTOR_COLUMNS))
        self.target_selector_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.target_selector_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.target_selector_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.target_selector_table.setAlternatingRowColors(True)
        self.target_selector_table.setShowGrid(False)
        self.target_selector_table.setWordWrap(False)
        self.target_selector_table.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.target_selector_table.verticalHeader().setVisible(False)
        selector_header = self.target_selector_table.horizontalHeader()
        selector_header.setSectionsClickable(True)
        selector_header.setSortIndicatorShown(True)
        selector_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        selector_header.setStretchLastSection(True)
        selector_header.setSortIndicator(1, Qt.SortOrder.DescendingOrder)
        target_selector_layout.addWidget(self.target_selector_table, 1)
        self._target_selection_result = TargetSelectionResult()
        self._update_footer()
        self.set_language(self._language)

    def attach_task_feedback(
        self,
        progress: QWidget,
        status: QWidget,
        task_cards: Iterable[QWidget],
    ) -> None:
        """Host compact task-specific feedback at the bottom of this tab."""

        del status  # The compact operation message remains in Autonomous Control.
        self.task_feedback_strip = QWidget(self.mission_commands_tab)
        self.task_feedback_strip.setObjectName("TaskFeedbackStrip")
        self.task_feedback_strip.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.task_feedback_strip.setMaximumHeight(57)
        feedback_layout = QVBoxLayout(self.task_feedback_strip)
        feedback_layout.setContentsMargins(0, 0, 0, 0)
        feedback_layout.setSpacing(3)
        for card in task_cards:
            card.setParent(self.task_feedback_strip)
            feedback_layout.addWidget(card)
        progress.setParent(self.task_feedback_strip)
        progress.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        progress.setFixedHeight(6)
        feedback_layout.addWidget(progress)
        self.mission_layout.addWidget(self.task_feedback_strip)

    def set_language(self, language: str) -> None:
        self._language = language
        self.tabs.setTabText(0, t("tab.parameters", language))
        self.tabs.setTabText(1, t("tab.mission_commands", language))
        self.tabs.setTabText(2, t("vehicle_management.tab.target_selector", language))
        self.target_selector_table.setHorizontalHeaderLabels(
            [
                t(f"target_selector.column.{column}", language)
                for column in _TARGET_SELECTOR_COLUMNS
            ]
        )
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
            self.category_combo.setItemText(
                0, t("table.parameters.all", language)
            )
        self._update_mission_command_placeholder()
        self._update_footer()
        self.update_target_selection(self._target_selection_result)

    def update_target_selection(self, result: TargetSelectionResult) -> None:
        """Render the live target ranking while preserving the selected sort."""

        self._target_selection_result = result
        if not self.target_selector_active:
            return
        table = self.target_selector_table
        header = table.horizontalHeader()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        if not 0 <= sort_column < len(_TARGET_SELECTOR_COLUMNS):
            sort_column, sort_order = 1, Qt.SortOrder.DescendingOrder

        table.setUpdatesEnabled(False)
        table.setSortingEnabled(False)
        try:
            table.setRowCount(len(result.candidates))
            for row, candidate in enumerate(result.candidates):
                values = (
                _NumericTableItem(
                    t("target_selector.team", self._language, team=candidate.team_number),
                    candidate.team_number,
                ),
                _NumericTableItem(f"{candidate.final_score:.1f}", candidate.final_score),
                _NumericTableItem(f"{candidate.lock_probability:.1f}%", candidate.lock_probability),
                _NumericTableItem(f"{candidate.pre_score:.1f}", candidate.pre_score),
                _NumericTableItem(f"{candidate.tail_access_score:.1f}", candidate.tail_access_score),
                _NumericTableItem(f"{candidate.distance_m:.1f} m", candidate.distance_m),
                _NumericTableItem(f"{candidate.altitude_delta_m:+.1f} m", candidate.altitude_delta_m),
                _NumericTableItem(f"{candidate.data_age_ms} ms", candidate.data_age_ms),
                _NumericTableItem(f"{candidate.merge_eta_s:.2f} s", candidate.merge_eta_s),
                _NumericTableItem("--" if candidate.best_lock_start_s is None else f"{candidate.best_lock_start_s:.2f} s", -1.0 if candidate.best_lock_start_s is None else candidate.best_lock_start_s),
                _NumericTableItem(f"{candidate.best_lock_duration_s:.2f} s", candidate.best_lock_duration_s),
                _NumericTableItem(f"{candidate.turn_required_deg:.1f}°", candidate.turn_required_deg),
                _NumericTableItem(f"{candidate.simulation_quality:.1f}%", candidate.simulation_quality),
                QTableWidgetItem(candidate.reason),
                )
                for column, item in enumerate(values):
                    item.setTextAlignment(
                        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                    )
                    table.setItem(row, column, item)
        finally:
            table.setSortingEnabled(True)
            table.setUpdatesEnabled(True)
        table.sortItems(sort_column, sort_order)
        header.setSortIndicator(sort_column, sort_order)
        self.target_selector_summary.setText(t(
            "target_selector.summary", self._language,
            finalists=len(result.candidates), valid=result.valid_count,
            considered=result.considered_count, elapsed=result.elapsed_ms,
        ))

    @property
    def target_selector_active(self) -> bool:
        return self.tabs.currentWidget() is self.target_selector_tab

    def _on_tab_changed(self, _index: int) -> None:
        active = self.target_selector_active
        if active:
            self.update_target_selection(self._target_selection_result)
        self.target_selector_visibility_changed.emit(active)

    def set_selected_task(self, task_code: str | None) -> None:
        self._selected_task = str(task_code or "").strip().upper()
        page = self._task_pages.get(self._selected_task)
        self.mission_command_stack.setCurrentWidget(page or self.mission_empty_page)
        self._update_mission_command_placeholder()

    def register_task_page(self, task_code: str, page: QWidget) -> None:
        """Register a task-specific command page without coupling it to MAVLink."""
        code = str(task_code).strip().upper()
        if not code:
            raise ValueError("Task code cannot be empty")
        previous = self._task_pages.get(code)
        if previous is not None:
            self.mission_command_stack.removeWidget(previous)
        self._task_pages[code] = page
        self.mission_command_stack.addWidget(page)
        if code == self._selected_task:
            self.mission_command_stack.setCurrentWidget(page)

    def _update_mission_command_placeholder(self) -> None:
        code = self._selected_task
        if not code:
            title = t("mission_commands.empty.title", self._language)
            description = t("mission_commands.empty.description", self._language)
            marker = "◇"
        else:
            label_key = TASK_LABEL_KEYS.get(code)
            task = t(label_key, self._language) if label_key else code.replace("_", " ").title()
            title = task
            description = t(
                "mission_commands.pending.description",
                self._language,
                task=task,
            )
            marker = "◈"
        self.mission_empty_marker.setText(marker)
        self.mission_empty_title.setText(title)
        self.mission_empty_description.setText(description)

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
        self._original_values = {item.key: item.value for item in self._parameters}
        self._dirty_values.clear()
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
        new_parameters = [
            item for item in incoming if item.key not in previous_by_key
        ]
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
                if (
                    row is not None
                    and (previous is None or previous.default_value != parameter.default_value)
                ):
                    default_item = self.table.item(row, 2)
                    if default_item is not None:
                        default_item.setText(self._display_default(parameter.default_value))
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
            QTableWidgetItem(str(parameter.value)),
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
