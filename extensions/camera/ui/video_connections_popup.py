from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from extensions.camera.models import VideoSourceConfig, VideoSourceStats
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette


class VideoConnectionsPopup(QDialog):
    """Selectable camera input profiles, matching the connection-card pattern."""

    sources_saved = pyqtSignal(tuple, str)
    source_connect_requested = pyqtSignal(str)

    def __init__(
        self,
        theme: ThemePalette,
        parent: QWidget | None = None,
        *,
        language: str = "tr",
    ) -> None:
        super().__init__(
            parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setObjectName("VideoConnectionsPopup")
        self.setMinimumSize(405, 360)
        self.setMaximumHeight(590)
        self._theme, self._language = theme, language
        self._sources: list[VideoSourceConfig] = []
        self._active_identifier = ""
        self._connected_source_ids: frozenset[str] = frozenset()
        self._connected = False
        self._build_ui()
        self.set_theme(theme)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(f"""
            QDialog#VideoConnectionsPopup {{ background: {theme.surface}; border: 1px solid {theme.border_strong}; border-radius: 7px; }}
            QLabel {{ background: transparent; color: {theme.text}; }}
            QLabel[role=title] {{ font-size: 11px; font-weight: 750; }}
            QLabel[role=detail] {{ color: {theme.muted_text}; font-size: 10px; }}
            QFrame[role=card] {{ background: {theme.surface_alt}; border: 1px solid {theme.border}; border-radius: 5px; }}
            QScrollArea {{ border: 0; background: transparent; }}
        """)
        self._render()

    def set_language(self, language: str) -> None:
        self._language = language
        self.back_button.setText(t("common.action.back", language))
        self._render()

    def show_sources(
        self,
        sources: tuple[VideoSourceConfig, ...],
        *,
        active_identifier: str,
        connected: bool,
        source_stats: tuple[VideoSourceStats, ...] = (),
    ) -> None:
        self._sources = list(sources)
        self._active_identifier = active_identifier
        self._connected = bool(connected)
        self._connected_source_ids = frozenset(
            item.source_id for item in source_stats if item.stats.connected
        )
        self._render()

    def update_source_stats(self, source_stats: tuple[VideoSourceStats, ...]) -> None:
        connected = frozenset(
            item.source_id for item in source_stats if item.stats.connected
        )
        if connected == self._connected_source_ids:
            return
        self._connected_source_ids = connected
        self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("VIDEO LINK", self)
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch(1)
        self.back_button = QPushButton(t("common.action.back", self._language), self)
        self.back_button.clicked.connect(self.hide)
        header.addWidget(self.back_button)
        root.addLayout(header)
        detail = QLabel(
            "Bir kamera/video bağlantısı seçilir. Kaynak değişimi arka planda yapılır.",
            self,
        )
        detail.setProperty("role", "detail")
        detail.setWordWrap(True)
        root.addWidget(detail)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.host = QWidget(self.scroll)
        self.layout = QVBoxLayout(self.host)
        self.layout.setContentsMargins(0, 0, 3, 0)
        self.layout.setSpacing(6)
        self.scroll.setWidget(self.host)
        root.addWidget(self.scroll, 1)
        self.add_button = QPushButton("+ Video bağlantısı ekle", self)
        self.add_button.setProperty("role", "primary")
        self.add_button.clicked.connect(lambda: self._edit(None))
        root.addWidget(self.add_button)

    def _render(self) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for source in self._sources:
            active = source.identifier == self._active_identifier
            connected = source.identifier in self._connected_source_ids or (
                active and self._connected and not self._connected_source_ids
            )
            card = QFrame(self.host)
            card.setProperty("role", "card")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(8, 7, 8, 7)
            layout.setSpacing(3)
            row = QHBoxLayout()
            dot = QLabel("●", card)
            dot.setStyleSheet(
                f"color: {self._theme.success if connected else self._theme.warning if active else self._theme.danger};"
            )
            row.addWidget(dot)
            name = QLabel(source.name, card)
            name.setStyleSheet("font-weight: 700;")
            row.addWidget(name, 1)
            edit = QPushButton("Düzenle", card)
            edit.setProperty("compact", "true")
            edit.clicked.connect(lambda _=False, value=source: self._edit(value))
            row.addWidget(edit)
            layout.addLayout(row)
            endpoint = QLabel(
                f"{source.source_type.upper()} · {source.codec.upper()} · {source.source_uri}",
                card,
            )
            endpoint.setProperty("role", "detail")
            endpoint.setWordWrap(True)
            layout.addWidget(endpoint)
            info = QLabel(
                f"{source.latency_ms} ms · {source.frame_timeout_s:g} sn · {'Bağlı' if connected else 'Etkin' if active else 'Beklemede'}",
                card,
            )
            info.setProperty("role", "detail")
            layout.addWidget(info)
            actions = QHBoxLayout()
            auto = QPushButton(
                "OTO: AÇIK" if source.auto_connect else "OTO: KAPALI", card
            )
            auto.setCheckable(True)
            auto.setChecked(source.auto_connect)
            auto.setProperty("compact", "true")
            auto.setProperty("role", "primary" if source.auto_connect else "secondary")
            auto.clicked.connect(
                lambda checked, identifier=source.identifier: self._auto(
                    identifier, checked
                )
            )
            actions.addWidget(auto)
            select = QPushButton(
                "Bağlı" if connected else "Bağlan" if active else "Seç", card
            )
            select.setProperty("compact", "true")
            select.setProperty("role", "danger" if connected else "primary")
            select.setEnabled(not connected)
            select.clicked.connect(
                lambda _=False, identifier=source.identifier: self._select(identifier)
            )
            actions.addWidget(select)
            delete = QPushButton("Sil", card)
            delete.setProperty("compact", "true")
            delete.setProperty("role", "danger")
            delete.setEnabled(len(self._sources) > 1)
            delete.clicked.connect(
                lambda _=False, identifier=source.identifier: self._delete(identifier)
            )
            actions.addWidget(delete)
            layout.addLayout(actions)
            self.layout.addWidget(card)
        self.layout.addStretch(1)

    def _edit(self, current: VideoSourceConfig | None) -> None:
        dialog = QDialog(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        dialog.setObjectName("VideoConnectionsPopup")
        dialog.setStyleSheet(self.styleSheet())
        root = QVBoxLayout(dialog)
        root.setContentsMargins(12, 10, 12, 12)
        title = QLabel(
            "VİDEO BAĞLANTISINI DÜZENLE" if current else "YENİ VİDEO BAĞLANTISI", dialog
        )
        title.setProperty("role", "title")
        root.addWidget(title)
        form = QFormLayout()
        name = QLineEdit(
            current.name if current else f"Kamera {len(self._sources) + 1}", dialog
        )
        uri = QLineEdit(current.source_uri if current else "", dialog)
        kind = QComboBox(dialog)
        kind.addItems(["udp", "rtsp", "http", "usb", "file", "pipeline"])
        kind.setCurrentText(current.source_type if current else "udp")
        codec = QComboBox(dialog)
        codec.addItems(["h265", "h264", "mjpeg"])
        codec.setCurrentText(current.codec if current else "h265")
        latency = QSpinBox(dialog)
        latency.setRange(0, 10_000)
        latency.setValue(current.latency_ms if current else 100)
        latency.setSuffix(" ms")
        timeout = QSpinBox(dialog)
        timeout.setRange(1, 60)
        timeout.setValue(int(current.frame_timeout_s if current else 3))
        timeout.setSuffix(" s")
        for label, field in (
            ("Ad", name),
            ("Kaynak", uri),
            ("Bağlantı tipi", kind),
            ("Codec", codec),
            ("Gecikme", latency),
            ("Zaman aşımı", timeout),
        ):
            form.addRow(label, field)
        root.addLayout(form)
        auto = QCheckBox("Açılışta otomatik bağlan", dialog)
        auto.setChecked(current.auto_connect if current else False)
        root.addWidget(auto)
        buttons = QHBoxLayout()
        cancel = QPushButton("Geri", dialog)
        save = QPushButton("Kaydet", dialog)
        save.setProperty("role", "primary")
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        buttons.addWidget(save)
        root.addLayout(buttons)
        cancel.clicked.connect(dialog.reject)

        def commit() -> None:
            try:
                value = VideoSourceConfig(
                    identifier=current.identifier
                    if current
                    else VideoSourceConfig().identifier,
                    name=name.text(),
                    source_type=kind.currentText(),
                    source_uri=uri.text(),
                    codec=codec.currentText(),
                    latency_ms=latency.value(),
                    frame_timeout_s=timeout.value(),
                    auto_connect=auto.isChecked(),
                    aim_enabled=current.aim_enabled if current else False,
                    aim_x=current.aim_x if current else 0.5,
                    aim_y=current.aim_y if current else 0.5,
                )
            except ValueError:
                return
            self._sources = [
                value if item.identifier == value.identifier else item
                for item in self._sources
            ]
            if current is None:
                self._sources.append(value)
            self._save(value.identifier if current is None else self._active_identifier)
            dialog.accept()

        save.clicked.connect(commit)
        dialog.exec()
        self._render()

    def _save(self, active: str) -> None:
        self.sources_saved.emit(tuple(self._sources), active)

    def _select(self, identifier: str) -> None:
        if identifier != self._active_identifier:
            self._save(identifier)
            return
        self.source_connect_requested.emit(identifier)

    def _auto(self, identifier: str, enabled: bool) -> None:
        self._sources = [
            replace(item, auto_connect=enabled)
            if item.identifier == identifier
            else item
            for item in self._sources
        ]
        self._save(self._active_identifier)

    def _delete(self, identifier: str) -> None:
        self._sources = [
            item for item in self._sources if item.identifier != identifier
        ]
        self._save(
            self._active_identifier
            if self._active_identifier != identifier
            else self._sources[0].identifier
        )
