"""Common embedding support; extension factories retain their configuration logic."""

import json
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QLineEdit,
    QPlainTextEdit,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QLabel,
    QMessageBox,
)
from src.ui.widgets.title_bar import AppTitleBar


class SettingsPageDialog(QDialog):
    def done(self, result):
        if self.property("embeddedSettings"):
            if result == QDialog.DialogCode.Accepted:
                self.accepted.emit()
        else:
            super().done(result)


def embed_dialog(dialog, *, commit=None):
    dialog.setProperty("embeddedSettings", True)
    dialog.setWindowFlags(Qt.WindowType.Widget)
    dialog.setModal(False)
    dialog.setMinimumSize(0, 0)
    dialog.setMaximumSize(16777215, 16777215)
    cancel = getattr(dialog, "cancel_button", None)
    if cancel:
        cancel.hide()
    for title in dialog.findChildren(AppTitleBar):
        title.hide()
    for box in dialog.findChildren(QDialogButtonBox):
        cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel:
            cancel.hide()
    if commit:
        dialog.accepted.connect(commit)
    return dialog


class ConfigSettingsPage(QWidget):
    """Typed fallback includes unknown config keys and never enables an extension."""

    def __init__(self, host, manifest, language, parent=None):
        super().__init__(parent)
        self.host, self.manifest = host, manifest
        self.controls = {}
        self.language = language
        root = QVBoxLayout(self)
        form = QFormLayout()
        config = host.settings(manifest.identifier)
        for key in dict.fromkeys([*manifest.settings, *config]):
            spec = manifest.settings.get(key, {})
            value = config.get(key, spec.get("default"))
            if isinstance(value, bool):
                widget = QCheckBox(self)
                widget.setChecked(value)
            elif isinstance(value, int):
                widget = QSpinBox(self)
                widget.setRange(-2147483647, 2147483647)
                widget.setValue(value)
            elif isinstance(value, float):
                widget = QDoubleSpinBox(self)
                widget.setRange(-1e9, 1e9)
                widget.setDecimals(6)
                widget.setValue(value)
            elif isinstance(value, (list, dict)) or value is None:
                widget = QPlainTextEdit(
                    json.dumps(value, ensure_ascii=False, indent=2), self
                )
                widget.setMaximumHeight(180)
            else:
                widget = QLineEdit(str(value), self)
                if "password" in key.lower():
                    widget.setEchoMode(QLineEdit.EchoMode.Password)
            self.controls[key] = widget
            form.addRow(spec.get("label", key.replace("_", " ")), widget)
        root.addLayout(form)
        if not self.controls:
            root.addWidget(
                QLabel(
                    "No configurable settings."
                    if language == "en"
                    else "Bu eklentinin değiştirilebilir ayarı yok."
                )
            )
        button = QPushButton("Apply" if language == "en" else "Uygula", self)
        button.setEnabled(bool(self.controls))
        button.clicked.connect(self.apply)
        root.addWidget(button)
        root.addStretch(1)

    def apply(self):
        try:
            values = {}
            for key, widget in self.controls.items():
                if isinstance(widget, QCheckBox):
                    values[key] = widget.isChecked()
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    values[key] = widget.value()
                elif isinstance(widget, QPlainTextEdit):
                    values[key] = json.loads(widget.toPlainText())
                else:
                    values[key] = widget.text()
            self.host.save_settings(self.manifest.identifier, values)
        except (ValueError, TypeError, OSError, RuntimeError) as error:
            QMessageBox.warning(
                self, "Settings" if self.language == "en" else "Ayarlar", str(error)
            )
