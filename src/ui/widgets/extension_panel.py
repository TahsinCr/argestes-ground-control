"""Compact extension browser in the existing left sidebar."""

from __future__ import annotations

from collections.abc import Callable
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from src.extension.host import ExtensionError, ExtensionHost


class ExtensionPanel(QWidget):
    def __init__(self, host: ExtensionHost, language: str, parent: QWidget | None = None,
                 open_application_settings: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.language = language
        self.open_application_settings = open_application_settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.heading = QLabel("EXTENSIONS" if language == "en" else "EKLENTİLER")
        layout.addWidget(self.heading)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search extensions" if language == "en" else "Eklenti ara")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.filter = QComboBox(self)
        self.filter.addItems((["All", "Enabled", "Disabled"] if language == "en"
                              else ["Tümü", "Etkin", "Devre dışı"]))
        self.filter.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.filter)
        self.category = QComboBox(self)
        self.category.addItem("All categories" if language == "en" else "Tüm kategoriler", "")
        for name in sorted({entry.manifest.category for entry in host.list_extensions()}):
            self.category.addItem(name, name)
        self.category.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.category)
        self.restart_label = QLabel("", self)
        self.restart_label.setWordWrap(True)
        layout.addWidget(self.restart_label)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.cards = QWidget(scroll)
        self.cards_layout = QVBoxLayout(self.cards)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.cards)
        layout.addWidget(scroll, 1)
        self.refresh()

    def refresh(self, *_args) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        query = self.search.text().casefold().strip()
        entries = self.host.list_extensions()
        self.restart_label.setText(
            ("Restart required to apply changes" if self.language == "en"
             else "Değişiklikler için yeniden başlatma gerekli")
            if any(item.pending_restart for item in entries) else ""
        )
        for item in entries:
            manifest = item.manifest
            if query and query not in f"{manifest.name} {manifest.description}".casefold():
                continue
            if self.filter.currentIndex() == 1 and not item.enabled:
                continue
            if self.filter.currentIndex() == 2 and item.enabled:
                continue
            if self.category.currentData() and self.category.currentData() != manifest.category:
                continue
            card = QWidget(self.cards)
            card.setObjectName("ExtensionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(6, 8, 6, 8)
            title = QLabel(f"{manifest.name}  ·  {manifest.version}", card)
            card_layout.addWidget(title)
            description = QLabel(manifest.description, card)
            description.setWordWrap(True)
            card_layout.addWidget(description)
            row = QHBoxLayout()
            toggle = QPushButton(("Disable" if item.enabled else "Enable") if self.language == "en"
                                 else ("Devre dışı bırak" if item.enabled else "Etkinleştir"), card)
            toggle.clicked.connect(lambda _checked=False, key=manifest.identifier, value=not item.enabled:
                                   self._toggle(key, value))
            row.addWidget(toggle)
            settings = QPushButton("Settings" if self.language == "en" else "Ayarlar", card)
            settings.clicked.connect(lambda _checked=False, key=manifest.identifier, anchor=settings:
                                     self._settings(key, anchor))
            row.addWidget(settings)
            card_layout.addLayout(row)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)

    def set_language(self, language: str) -> None:
        self.language = language
        self.heading.setText("EXTENSIONS" if language == "en" else "EKLENTİLER")
        self.search.setPlaceholderText("Search extensions" if language == "en" else "Eklenti ara")
        labels = ["All", "Enabled", "Disabled"] if language == "en" else ["Tümü", "Etkin", "Devre dışı"]
        for index, label in enumerate(labels):
            self.filter.setItemText(index, label)
        self.category.setItemText(0, "All categories" if language == "en" else "Tüm kategoriler")
        self.refresh()

    def _toggle(self, identifier: str, enabled: bool) -> None:
        try:
            self.host.set_enabled(identifier, enabled)
        except ExtensionError as exc:
            QMessageBox.warning(self, "Extension" if self.language == "en" else "Eklenti", str(exc))
        self.refresh()

    def _settings(self, identifier: str, anchor: QWidget | None = None) -> None:
        contribution = self.host.settings_action(identifier)
        if contribution is not None:
            contribution.action(anchor or self)
            return
        manifest = next(item.manifest for item in self.host.list_extensions()
                        if item.manifest.identifier == identifier)
        if not manifest.settings and self.open_application_settings is not None:
            self.open_application_settings()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(manifest.name)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        values = self.host.settings(identifier)
        controls: dict[str, QWidget] = {}
        for key, spec in manifest.settings.items():
            if spec.get("type") == "boolean":
                widget = QCheckBox(dialog)
                widget.setChecked(bool(values.get(key)))
            else:
                widget = QLineEdit(str(values.get(key, "")), dialog)
            controls[key] = widget
            form.addRow(spec.get("label", key), widget)
        if not controls:
            layout.addWidget(QLabel("No additional settings" if self.language == "en"
                                    else "Ek ayar bulunmuyor", dialog))
        else:
            layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not controls:
            return
        try:
            updated = {}
            for key, widget in controls.items():
                kind = manifest.settings[key].get("type")
                updated[key] = (widget.isChecked() if kind == "boolean" else
                                int(widget.text()) if kind == "integer" else widget.text())
            self.host.save_settings(identifier, updated)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, manifest.name, str(exc))
        self.refresh()
