from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.ui.i18n import t
from src.ui.config.settings_schema import FieldSpec, SETTINGS_SECTIONS, SectionSpec, iter_fields
from src.models.settings import SettingsModel
from src.ui.themes.style import ThemePalette
from src.ui.widgets.title_bar import AppTitleBar


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsModel, language: str, theme: ThemePalette, parent=None) -> None:
        super().__init__(parent)
        self._ui_language = language
        self._theme = theme
        self._settings = settings
        self._widgets: dict[str, QWidget] = {}
        self.setObjectName("FramelessDialog")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowTitle(t("dialog.settings.title", language))
        self.resize(620, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title_bar = AppTitleBar(self, theme, self)
        self.title_bar.set_title(t("dialog.settings.title", language))
        layout.addWidget(self.title_bar)

        shell = QWidget(self)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(10, 10, 10, 10)
        shell_layout.setSpacing(8)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        for section in SETTINGS_SECTIONS:
            content_layout.addWidget(self._build_section(section, settings))
        content_layout.addStretch(1)
        scroll.setWidget(content)
        shell_layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self.clear_cache_button = QPushButton(t("dialog.settings.clear_cache", language), self)
        footer.addWidget(self.clear_cache_button)
        footer.addStretch(1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("dialog.settings.save", language))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("dialog.settings.cancel", language))
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        shell_layout.addLayout(footer)
        layout.addWidget(shell, 1)

    def _build_section(self, section: SectionSpec, settings: SettingsModel) -> QWidget:
        box = QGroupBox(t(section.title_key, self._ui_language), self)
        form_layout = QFormLayout(box)
        form_layout.setContentsMargins(10, 10, 10, 10)
        form_layout.setSpacing(10)

        for field in section.fields:
            widget = self._create_widget(field, settings)
            self._widgets[field.path] = widget
            form_layout.addRow(t(field.label_key, self._ui_language), widget)
        return box

    def _create_widget(self, field: FieldSpec, settings: SettingsModel) -> QWidget:
        current_value = settings.value_at(field.path)
        if field.kind == "combo":
            combo = QComboBox(self)
            for option in field.choices:
                combo.addItem(t(option.label_key, self._ui_language), option.value)
            self._set_combo_data(combo, str(current_value))
            return combo

        if field.kind == "check":
            checkbox = QCheckBox(self)
            checkbox.setChecked(bool(current_value))
            return checkbox

        if field.kind == "spin":
            spin = QSpinBox(self)
            if field.minimum is not None:
                spin.setMinimum(int(field.minimum))
            if field.maximum is not None:
                spin.setMaximum(int(field.maximum))
            if field.suffix:
                spin.setSuffix(field.suffix)
            spin.setValue(int(current_value))
            return spin

        if field.kind == "double":
            spin = QDoubleSpinBox(self)
            if field.minimum is not None:
                spin.setMinimum(float(field.minimum))
            if field.maximum is not None:
                spin.setMaximum(float(field.maximum))
            spin.setDecimals(field.decimals)
            spin.setSingleStep(field.single_step)
            if field.suffix:
                spin.setSuffix(field.suffix)
            spin.setValue(float(current_value))
            return spin

        line_edit = QLineEdit(str(current_value), self)
        if field.path.endswith(".password"):
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)
        return line_edit

    def _set_combo_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _value_for(self, path: str) -> Any:
        widget = self._widgets[path]
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QSpinBox):
            return int(widget.value())
        if isinstance(widget, QDoubleSpinBox):
            return float(widget.value())
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        raise TypeError(f"Unsupported settings widget for path: {path}")

    def to_settings(self) -> SettingsModel:
        payload = self._settings.to_dict()
        for field in iter_fields():
            target = payload
            path_parts = field.path.split(".")
            for part in path_parts[:-1]:
                target = target[part]
            target[path_parts[-1]] = self._value_for(field.path)
        # Validate the complete form atomically. Interdependent fields such as
        # battery minimum/maximum voltage must not fail against stale values
        # merely because one happens to be processed first.
        return SettingsModel.from_dict(payload)

    def _accept_if_valid(self) -> None:
        try:
            self.to_settings()
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                t("dialog.settings.validation_title", self._ui_language),
                t(
                    "dialog.settings.validation_message",
                    self._ui_language,
                    error=str(exc),
                ),
            )
            return
        self.accept()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.sync_window_state()
