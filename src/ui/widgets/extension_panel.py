"""Compact extension browser in the existing left sidebar."""

from __future__ import annotations

from collections.abc import Callable
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QTabWidget,
)

from src.extension.host import ExtensionError, ExtensionHost
from src.ui.i18n import t, reload_language_packs


class ExtensionDialog(QDialog):
    """Responsive management shell; future catalog tabs reuse the browser UI."""

    def __init__(self, host, language, parent=None, **callbacks):
        super().__init__(parent)
        self.setWindowTitle("Extensions" if language == "en" else "Eklentiler")
        self.setMinimumSize(480, 360)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.browser = ExtensionPanel(host, language, self.tabs, **callbacks)
        self.tabs.addTab(self.browser, "Installed" if language == "en" else "Yüklü")
        layout.addWidget(self.tabs)

    def resize_for(self, window):
        self.resize(
            max(480, min(960, int(window.width() * 0.7))),
            max(360, min(760, int(window.height() * 0.8))),
        )
        center = window.frameGeometry().center()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)


class ExtensionPanel(QWidget):
    def _metadata(self, manifest, field):
        key = f"extension.{manifest.identifier}.{field}"
        translated = t(key, self.language)
        return getattr(manifest, field) if translated == f"[{key}]" else translated

    def _category_name(self, name):
        entry = next(
            item
            for item in self.host.list_extensions()
            if item.manifest.category == name
        )
        return self._metadata(entry.manifest, "category")

    def __init__(
        self,
        host: ExtensionHost,
        language: str,
        parent: QWidget | None = None,
        apply_change: Callable[[str, bool], None] | None = None,
        reload_extension: Callable[[str], None] | None = None,
    ) -> None:
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
        self.discover_button = QPushButton(
            "Refresh" if language == "en" else "Yenile", self
        )
        self.discover_button.clicked.connect(self._refresh_discovery)
        heading_row.addWidget(self.discover_button)
        layout.addLayout(heading_row)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(
            "Search extensions" if language == "en" else "Eklenti ara"
        )
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.filter = QComboBox(self)
        self.filter.addItems(
            (
                ["All", "Enabled", "Disabled"]
                if language == "en"
                else ["Tümü", "Etkin", "Devre dışı"]
            )
        )
        self.filter.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.filter)
        self.category = QComboBox(self)
        self.category.addItem(
            "All categories" if language == "en" else "Tüm kategoriler", ""
        )
        for name in sorted(
            {entry.manifest.category for entry in host.list_extensions()}
        ):
            self.category.addItem(self._category_name(name), name)
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
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        query = self.search.text().casefold().strip()
        entries = self.host.list_extensions()
        self.restart_label.setText(
            (
                "Settings need extension reload"
                if self.language == "en"
                else "Ayarlar için eklentiyi yeniden yükleyin"
            )
            if any(item.pending_restart for item in entries)
            else ""
        )
        for item in entries:
            manifest = item.manifest
            name, detail = (
                self._metadata(manifest, "name"),
                self._metadata(manifest, "description"),
            )
            if query and query not in f"{name} {detail} {manifest.name}".casefold():
                continue
            if self.filter.currentIndex() == 1 and not item.enabled:
                continue
            if self.filter.currentIndex() == 2 and item.enabled:
                continue
            if (
                self.category.currentData()
                and self.category.currentData() != manifest.category
            ):
                continue
            card = QWidget(self.cards)
            card.setObjectName("ExtensionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(6, 8, 6, 8)
            title = QLabel(f"{name}  ·  {manifest.version}", card)
            card_layout.addWidget(title)
            description = QLabel(detail, card)
            description.setWordWrap(True)
            card_layout.addWidget(description)
            row = QHBoxLayout()
            toggle = QPushButton(
                ("Disable" if item.enabled else "Enable")
                if self.language == "en"
                else ("Devre dışı bırak" if item.enabled else "Etkinleştir"),
                card,
            )
            toggle.clicked.connect(
                lambda _checked=False, key=manifest.identifier, value=not item.enabled: (
                    self._toggle(key, value)
                )
            )
            row.addWidget(toggle)
            if item.enabled and self.host.can_reload(manifest.identifier):
                reload_button = QPushButton("↻", card)
                reload_button.setFixedWidth(30)
                reload_button.setToolTip(
                    "Reload extension"
                    if self.language == "en"
                    else "Eklentiyi yeniden yükle"
                )
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
        self.search.setPlaceholderText(
            "Search extensions" if language == "en" else "Eklenti ara"
        )
        labels = (
            ["All", "Enabled", "Disabled"]
            if language == "en"
            else ["Tümü", "Etkin", "Devre dışı"]
        )
        for index, label in enumerate(labels):
            self.filter.setItemText(index, label)
        self.category.setItemText(
            0, "All categories" if language == "en" else "Tüm kategoriler"
        )
        for index in range(1, self.category.count()):
            self.category.setItemText(
                index, self._category_name(self.category.itemData(index))
            )
        self.refresh()

    def _toggle(self, identifier: str, enabled: bool) -> None:
        try:
            if self.apply_change is not None:
                self.apply_change(identifier, enabled)
            else:
                self.host.set_enabled(identifier, enabled)
        except (ExtensionError, OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(
                self, "Extension" if self.language == "en" else "Eklenti", str(exc)
            )
        self.refresh()

    def _reload(self, identifier: str) -> None:
        try:
            if self.reload_extension is not None:
                self.reload_extension(identifier)
            else:
                self.host.reload(identifier)
        except (ExtensionError, OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(
                self, "Extension" if self.language == "en" else "Eklenti", str(exc)
            )
        self.refresh()

    def _refresh_discovery(self) -> None:
        try:
            self.host.refresh_extensions()
            reload_language_packs()
        except (ExtensionError, OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Extension" if self.language == "en" else "Eklenti", str(exc)
            )
            return
        known = {
            self.category.itemData(index) for index in range(self.category.count())
        }
        for name in sorted(
            {entry.manifest.category for entry in self.host.list_extensions()} - known
        ):
            self.category.addItem(self._category_name(name), name)
        self.refresh()
