"""Ordered field selection and list/grid configuration without protocol imports."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.extension.api import ClockQuality
from src.services.settings import SettingsService
from src.ui.i18n import t

from ..fields import clock_field_specs, clock_reference_id
from .panel import TelemetryPanel


class TelemetrySettingsPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.settings = context.services.resolve(SettingsService)
        self.language = self.settings.current.ui.language
        self.clock_references = getattr(context, "clock_references", None)
        self._clock_fields = {}
        self._clock_field_titles = {}
        self._base_catalog = [
            (key, label)
            for _, items in TelemetryPanel.SECTION_SPECS
            for key, label in items
        ]
        self._refresh_clock_catalog()
        config = self.settings.extension_config("telemetry_display")
        root = QVBoxLayout(self)
        title = QLabel(self.tr("hint"), self)
        title.setWordWrap(True)
        root.addWidget(title)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(self.tr("search"))
        root.addWidget(self.search)
        lists = QHBoxLayout()
        self.available, self.selected = QListWidget(self), QListWidget(self)
        self.available.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.selected.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.selected.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.selected.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.selected.setMinimumHeight(220)
        for widget, heading in (
            (self.available, "available"),
            (self.selected, "selected"),
        ):
            column = QVBoxLayout()
            column.addWidget(QLabel(self.tr(heading), self))
            column.addWidget(widget, 1)
            lists.addLayout(column, 1)
        root.addLayout(lists, 1)
        fields = config.get("fields", TelemetryPanel.DEFAULT_FIELDS)
        if not isinstance(fields, (list, tuple)):
            fields = TelemetryPanel.DEFAULT_FIELDS
        for key in fields:
            if not isinstance(key, str):
                continue
            self.add_key(key, preserve_unavailable=True)
        actions = QHBoxLayout()
        for label, callback in (
            ("add", self.add),
            ("remove", self.remove),
            ("up", lambda: self.move(-1)),
            ("down", lambda: self.move(1)),
        ):
            button = QPushButton(self.tr(label), self)
            button.clicked.connect(callback)
            actions.addWidget(button)
        root.addLayout(actions)
        form = QFormLayout()
        self.mode = QComboBox(self)
        self.mode.addItem(self.tr("grid"), "grid")
        self.mode.addItem(self.tr("list"), "list")
        self.mode.setCurrentIndex(
            max(0, self.mode.findData(config.get("layout", "grid")))
        )
        self.columns = QSpinBox(self)
        self.columns.setRange(1, 4)
        try:
            columns = int(config.get("columns", 2))
        except (TypeError, ValueError):
            columns = 2
        self.columns.setValue(columns)
        self.grouped = QCheckBox(self.tr("grouped"), self)
        self.grouped.setChecked(config.get("grouped", True))
        form.addRow(self.tr("layout"), self.mode)
        form.addRow(self.tr("columns"), self.columns)
        form.addRow(self.grouped)
        root.addLayout(form)
        self.result = QLabel(self)
        root.addWidget(self.result)
        save = QPushButton(self.tr("apply"), self)
        save.setProperty("role", "primary")
        save.clicked.connect(self.apply)
        root.addWidget(save)
        self.search.textChanged.connect(self.refresh_available)
        self.available.itemDoubleClicked.connect(lambda _: self.add())
        self.mode.currentIndexChanged.connect(
            lambda: self.columns.setEnabled(self.mode.currentData() == "grid")
        )
        self.columns.setEnabled(self.mode.currentData() == "grid")
        self.refresh_available()

    def tr(self, key):
        return t("telemetry.settings." + key, self.language)

    def _refresh_clock_catalog(self):
        previous_titles = self._clock_field_titles
        references = (
            self.clock_references.references() if self.clock_references else ()
        )
        clock_specs = clock_field_specs(references)
        self._clock_fields = {key: identifier for key, _, identifier in clock_specs}
        self._clock_field_titles = {key: label for key, label, _ in clock_specs}
        self.catalog = self._base_catalog + [
            (key, label) for key, label, _ in clock_specs
        ]
        if hasattr(self, "selected"):
            for index in range(self.selected.count()):
                item = self.selected.item(index)
                key = item.data(Qt.ItemDataRole.UserRole)
                identifier = clock_reference_id(key)
                if identifier is None:
                    continue
                title = self._clock_field_titles.get(key)
                if title is None:
                    unavailable = self.tr("clock_unavailable")
                    title = previous_titles.get(key, item.text() or identifier)
                    suffix = f" · {unavailable}"
                    item.setText(
                        title if title.endswith(suffix) else f"{title}{suffix}"
                    )
                    item.setToolTip(self.tr("clock_unavailable"))
                else:
                    item.setText(title)
                    item.setToolTip(
                        ""
                        if self._clock_is_available(identifier)
                        else self.tr("clock_unavailable")
                    )

    def add_key(self, key, *, preserve_unavailable=False):
        labels = dict(self.catalog)
        if key in self.fields():
            return
        identifier = clock_reference_id(key)
        if key in labels:
            label = self._field_label(key, labels[key])
        elif identifier is not None:
            label = self._clock_field_titles.get(key, identifier)
        else:
            return
        available = identifier is None or self._clock_is_available(identifier)
        if not available and not preserve_unavailable:
            self.result.setText(self.tr("clock_unavailable"))
            return
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        if not available:
            item.setToolTip(self.tr("clock_unavailable"))
        self.selected.addItem(item)

    def fields(self):
        return [
            self.selected.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.selected.count())
        ]

    def refresh_available(self):
        self._refresh_clock_catalog()
        self.available.clear()
        query = self.search.text().casefold()
        chosen = set(self.fields())
        for key, label in self.catalog:
            text = self._field_label(key, label)
            if key not in chosen and query in (text + " " + key).casefold():
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, key)
                identifier = self._clock_fields.get(key)
                if identifier is not None and not self._clock_is_available(identifier):
                    item.setFlags(
                        item.flags() & ~Qt.ItemFlag.ItemIsEnabled
                    )
                    item.setToolTip(self.tr("clock_unavailable"))
                self.available.addItem(item)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_available()

    def _field_label(self, key, label):
        if key in self._clock_field_titles:
            return self._clock_field_titles[key]
        return t(label, self.language)

    def _clock_is_available(self, identifier):
        if self.clock_references is None:
            return False
        status = self.clock_references.status(identifier)
        return status.value is not None and status.quality not in {
            ClockQuality.UNAVAILABLE,
            ClockQuality.STALE,
        }

    def add(self):
        for item in self.available.selectedItems():
            self.add_key(item.data(Qt.ItemDataRole.UserRole))
        self.refresh_available()

    def remove(self):
        for item in self.selected.selectedItems():
            self.selected.takeItem(self.selected.row(item))
        self.refresh_available()

    def move(self, offset):
        row = self.selected.currentRow()
        target = row + offset
        if row >= 0 and 0 <= target < self.selected.count():
            item = self.selected.takeItem(row)
            self.selected.insertItem(target, item)
            self.selected.setCurrentRow(target)

    def apply(self):
        config = dict(self.settings.extension_config("telemetry_display"))
        config.update(
            fields=self.fields(),
            layout=self.mode.currentData(),
            columns=self.columns.value(),
            grouped=self.grouped.isChecked(),
        )
        self.settings.update_extension("telemetry_display", config=config)
        self.result.setText(self.tr("saved"))
