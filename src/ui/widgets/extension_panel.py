"""Compact extension browser in the existing left sidebar."""

from __future__ import annotations

from collections.abc import Callable
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from src.extension.host import ExtensionError, ExtensionHost


class ExtensionPanel(QWidget):
    def __init__(self, host: ExtensionHost, language: str, parent: QWidget | None = None,
                 apply_change: Callable[[str, bool], None] | None = None,
                 reload_extension: Callable[[str], None] | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.language = language
        self.apply_change = apply_change
        self.reload_extension = reload_extension
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.heading = QLabel("EXTENSIONS" if language == "en" else "EKLENTİLER")
        heading_row = QHBoxLayout()
        heading_row.addWidget(self.heading, 1)
        self.discover_button = QPushButton("Refresh" if language == "en" else "Yenile", self)
        self.discover_button.clicked.connect(self._refresh_discovery)
        heading_row.addWidget(self.discover_button)
        layout.addLayout(heading_row)
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
            ("Settings need extension reload" if self.language == "en"
             else "Ayarlar için eklentiyi yeniden yükleyin")
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
            settings.setEnabled(item.enabled or bool(manifest.settings))
            settings.clicked.connect(lambda _checked=False, key=manifest.identifier, anchor=settings:
                                     self._settings(key, anchor))
            row.addWidget(settings)
            if item.enabled and self.host.can_reload(manifest.identifier):
                reload_button = QPushButton("↻", card)
                reload_button.setFixedWidth(30)
                reload_button.setToolTip("Reload extension" if self.language == "en"
                                         else "Eklentiyi yeniden yükle")
                reload_button.clicked.connect(
                    lambda _checked=False, key=manifest.identifier: self._reload(key)
                )
                row.addWidget(reload_button)
            card_layout.addLayout(row)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)

    def set_language(self, language: str) -> None:
        self.language = language
        self.heading.setText("EXTENSIONS" if language == "en" else "EKLENTİLER")
        self.discover_button.setText("Refresh" if language == "en" else "Yenile")
        self.search.setPlaceholderText("Search extensions" if language == "en" else "Eklenti ara")
        labels = ["All", "Enabled", "Disabled"] if language == "en" else ["Tümü", "Etkin", "Devre dışı"]
        for index, label in enumerate(labels):
            self.filter.setItemText(index, label)
        self.category.setItemText(0, "All categories" if language == "en" else "Tüm kategoriler")
        self.refresh()

    def _toggle(self, identifier: str, enabled: bool) -> None:
        try:
            if self.apply_change is not None:
                self.apply_change(identifier, enabled)
            else:
                self.host.set_enabled(identifier, enabled)
        except (ExtensionError, OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Extension" if self.language == "en" else "Eklenti", str(exc))
        self.refresh()

    def _reload(self, identifier: str) -> None:
        try:
            if self.reload_extension is not None:
                self.reload_extension(identifier)
            else:
                self.host.reload(identifier)
        except (ExtensionError, OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Extension" if self.language == "en" else "Eklenti", str(exc))
        self.refresh()

    def _refresh_discovery(self) -> None:
        try:
            self.host.refresh_extensions()
        except (ExtensionError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Extension" if self.language == "en" else "Eklenti", str(exc))
            return
        known = {self.category.itemData(index) for index in range(self.category.count())}
        for name in sorted({entry.manifest.category for entry in self.host.list_extensions()} - known):
            self.category.addItem(name, name)
        self.refresh()

    def _settings(self, identifier: str, anchor: QWidget | None = None) -> None:
        contribution = self.host.settings_action(identifier)
        if contribution is not None:
            contribution.action(anchor or self)
            return
        manifest = next(item.manifest for item in self.host.list_extensions()
                        if item.manifest.identifier == identifier)
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
            if self.reload_extension is not None and self.host.is_active(identifier):
                self.reload_extension(identifier)
            elif self.host.is_active(identifier):
                self.host.reload(identifier)
        except (ExtensionError, RuntimeError, ValueError, OSError) as exc:
            QMessageBox.warning(self, manifest.name, str(exc))
        self.refresh()
