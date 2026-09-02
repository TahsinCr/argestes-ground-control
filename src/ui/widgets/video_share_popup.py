from __future__ import annotations

from uuid import uuid4

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
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

from src.models.video import (
    VideoShareConfig,
    VideoShareState,
    VideoSourceConfig,
    VideoSourceStats,
)
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette


class VideoSharePopup(QDialog):
    """Compact editor for the camera-only Video Link rebroadcast targets."""

    shares_saved = pyqtSignal(tuple)

    _STATUS_COLORS = {"active": "success", "waiting": "danger", "error": "danger", "inactive": "danger"}

    def __init__(self, theme: ThemePalette, parent: QWidget | None = None, *, language: str = "tr") -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("VideoSharePopup")
        self.setMinimumSize(405, 390)
        self.setMaximumHeight(590)
        self._theme = theme
        self._language = language
        self._shares: list[VideoShareConfig] = []
        self._states: dict[str, VideoShareState] = {}
        self._network_interfaces: tuple[tuple[str, str], ...] = ()
        self._sources: tuple[VideoSourceConfig, ...] = ()
        self._connected_source_ids: frozenset[str] = frozenset()
        self._active_source_id = ""
        self._editing = False
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render)
        self._build_ui()
        self.set_theme(theme)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(f"""
            QDialog#VideoSharePopup {{ background: {theme.surface}; border: 1px solid {theme.border_strong}; border-radius: 7px; }}
            QLabel {{ background: transparent; color: {theme.text}; }}
            QLabel[role=title] {{ font-size: 11px; font-weight: 750; }}
            QLabel[role=detail] {{ color: {theme.muted_text}; font-size: 10px; }}
            QFrame[role=share-card] {{ background: {theme.surface_alt}; border: 1px solid {theme.border}; border-radius: 5px; }}
            QScrollArea {{ border: 0; background: transparent; }}
        """)
        self._render()

    def set_language(self, language: str) -> None:
        self._language = language
        self.back_button.setText(t("common.action.back", language))
        self._render()

    def show_shares(
        self,
        shares: tuple[VideoShareConfig, ...],
        states: tuple[VideoShareState, ...],
        *,
        network_interfaces: tuple[tuple[str, str], ...] = (),
        sources: tuple[VideoSourceConfig, ...] = (),
        source_stats: tuple[VideoSourceStats, ...] = (),
        active_source_id: str = "",
    ) -> None:
        self._shares = list(shares)
        self._states = {state.identifier: state for state in states}
        self._network_interfaces = network_interfaces
        self._sources = sources
        self._connected_source_ids = frozenset(
            item.source_id for item in source_stats if item.stats.connected
        )
        self._active_source_id = active_source_id
        self._render()

    def update_states(self, states: tuple[VideoShareState, ...]) -> None:
        updated = {state.identifier: state for state in states}
        if updated == self._states:
            return
        self._states = updated
        self._render()

    def update_sources(
        self,
        sources: tuple[VideoSourceConfig, ...],
        source_stats: tuple[VideoSourceStats, ...],
        active_source_id: str,
    ) -> None:
        connected = frozenset(
            item.source_id for item in source_stats if item.stats.connected
        )
        changed = (
            sources != self._sources
            or connected != self._connected_source_ids
            or active_source_id != self._active_source_id
        )
        self._sources = sources
        self._connected_source_ids = connected
        self._active_source_id = active_source_id
        if changed and not self._editing:
            self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("VIDEO SHARE", self)
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch(1)
        self.back_button = QPushButton(t("common.action.back", self._language), self)
        self.back_button.clicked.connect(self._go_back)
        header.addWidget(self.back_button)
        root.addLayout(header)
        detail = QLabel("Video Link kamera kareleri paylaşılır; ekran kaynağı seçilemez.", self)
        detail.setWordWrap(True)
        detail.setProperty("role", "detail")
        root.addWidget(detail)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_host = QWidget(self.scroll)
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 3, 0)
        self.list_layout.setSpacing(6)
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)
        self.interface_row = QHBoxLayout()
        self.interface_label = QLabel("Multicast çıkışı", self)
        self.interface_label.setProperty("role", "detail")
        self.interface_combo = QComboBox(self)
        self.interface_combo.setToolTip("UDP multicast yayınını seçilen ağ arayüzünden gönderir.")
        self.interface_row.addWidget(self.interface_label)
        self.interface_row.addWidget(self.interface_combo, 1)
        root.addLayout(self.interface_row)
        self.add_button = QPushButton("+ Yayın ekle", self)
        self.add_button.setProperty("role", "primary")
        self.add_button.clicked.connect(lambda: self._edit_share(None))
        root.addWidget(self.add_button)

    def _render(self) -> None:
        self._editing = False
        self.back_button.show()
        self.add_button.show()
        self.interface_label.show()
        self.interface_combo.show()
        self._render_interface_selector()
        self._clear_list()
        if not self._shares:
            empty = QLabel("Henüz yayın hedefi eklenmedi.", self.list_host)
            empty.setProperty("role", "detail")
            self.list_layout.addWidget(empty)
        for share in self._shares:
            state = self._states.get(share.identifier, VideoShareState(share.identifier, status="waiting" if share.enabled else "inactive"))
            card = QFrame(self.list_host)
            card.setProperty("role", "share-card")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(8, 7, 8, 7)
            layout.setSpacing(3)
            row = QHBoxLayout()
            color = getattr(self._theme, self._STATUS_COLORS[state.status])
            dot = QLabel("●", card)
            dot.setStyleSheet(f"color: {color};")
            name = QLabel(share.name, card)
            name.setStyleSheet("font-weight: 700;")
            row.addWidget(dot)
            row.addWidget(name, 1)
            edit = QPushButton("Düzenle", card)
            edit.setProperty("compact", "true")
            edit.clicked.connect(lambda _checked=False, item=share: self._edit_share(item))
            row.addWidget(edit)
            layout.addLayout(row)
            address = QLabel(f"{share.connection_type.upper()} · {share.codec.upper()} · {share.endpoint}", card)
            address.setProperty("role", "detail")
            address.setWordWrap(True)
            layout.addWidget(address)
            source_name = self._share_source_name(share.source_id)
            source = QLabel(
                f"{t('video.share.source', self._language)}: {source_name}",
                card,
            )
            source.setProperty("role", "detail")
            layout.addWidget(source)
            flags = "Sabit FPS" if share.fixed_frame_rate else "Değişken FPS"
            flags += " · Sunucu saati" if share.timestamp_enabled else " · Zaman damgası kapalı"
            status = QLabel(f"{flags} · {share.fps} FPS · {state.detail}", card)
            status.setProperty("role", "detail")
            status.setWordWrap(True)
            layout.addWidget(status)
            enabled = QPushButton(
                "YAYIN AÇIK" if share.enabled else "YAYIN KAPALI",
                card,
            )
            enabled.setCheckable(True)
            enabled.setChecked(share.enabled)
            enabled.setProperty("role", "primary" if share.enabled else "danger")
            enabled.setProperty("compact", "true")
            enabled.clicked.connect(
                lambda checked, identifier=share.identifier: self._set_share_enabled(
                    identifier, checked
                )
            )
            layout.addWidget(enabled, 0, Qt.AlignmentFlag.AlignLeft)
            self.list_layout.addWidget(card)
        self.list_layout.addStretch(1)

    def _render_interface_selector(self) -> None:
        selected = next(
            (share.network_interface for share in self._shares if share.network_interface),
            "",
        )
        self.interface_combo.blockSignals(True)
        self.interface_combo.clear()
        self.interface_combo.addItem("Otomatik (sistem rotası)", "")
        names = {name for name, _address in self._network_interfaces}
        for name, address in self._network_interfaces:
            self.interface_combo.addItem(f"{name} — {address}", name)
        if selected and selected not in names:
            self.interface_combo.addItem(f"{selected} — kullanılamıyor", selected)
        self.interface_combo.setCurrentIndex(
            max(0, self.interface_combo.findData(selected))
        )
        self.interface_combo.blockSignals(False)
        try:
            self.interface_combo.currentIndexChanged.disconnect(self._set_multicast_interface)
        except TypeError:
            pass
        self.interface_combo.currentIndexChanged.connect(self._set_multicast_interface)

    def _set_multicast_interface(self, _index: int) -> None:
        interface = str(self.interface_combo.currentData() or "")
        if all(share.network_interface == interface for share in self._shares):
            return
        self._shares = [
            VideoShareConfig(
                identifier=share.identifier,
                name=share.name,
                endpoint=share.endpoint,
                connection_type=share.connection_type,
                codec=share.codec,
                fps=share.fps,
                enabled=share.enabled,
                timestamp_enabled=share.timestamp_enabled,
                fixed_frame_rate=share.fixed_frame_rate,
                network_interface=interface,
                source_id=share.source_id,
            )
            for share in self._shares
        ]
        self._save(defer_render=True)

    def _clear_list(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _edit_share(self, current: VideoShareConfig | None) -> None:
        self._editing = True
        self.back_button.hide()
        self.add_button.hide()
        self.interface_label.hide()
        self.interface_combo.hide()
        self._clear_list()
        editor = QFrame(self.list_host)
        editor.setProperty("role", "share-card")
        root = QVBoxLayout(editor)
        root.setContentsMargins(9, 8, 9, 8)
        root.setSpacing(6)
        title = QLabel("YAYIN HEDEFİNİ DÜZENLE" if current else "YENİ YAYIN HEDEFİ", editor)
        title.setProperty("role", "title")
        root.addWidget(title)
        form = QFormLayout()
        name = QLineEdit(current.name if current else "", editor)
        endpoint = QLineEdit(current.endpoint if current else "", editor)
        connection_type = QComboBox(editor)
        connection_type.addItems(["udp", "srt", "rtmp", "rtsp"])
        connection_type.setCurrentText(current.connection_type if current else "udp")
        codec = QComboBox(editor)
        codec.addItems(["h264-baseline", "h264", "h265"])
        codec.setCurrentText(current.codec if current else "h264")
        fps = QSpinBox(editor)
        fps.setRange(15, 120)
        fps.setValue(current.fps if current else 30)
        source = QComboBox(editor)
        source.addItem(t("video.share.source.panel", self._language), "panel")
        for item in self._sources:
            if item.identifier in self._connected_source_ids:
                source.addItem(item.name, item.identifier)
        selected_source = current.source_id if current else "panel"
        if source.findData(selected_source) < 0:
            unavailable = self._share_source_name(selected_source)
            source.addItem(
                f"{unavailable} ({t('video.share.source.disconnected', self._language)})",
                selected_source,
            )
        source.setCurrentIndex(max(0, source.findData(selected_source)))
        form.addRow("Ad", name)
        form.addRow("Bağlantı", endpoint)
        form.addRow("Bağlantı tipi", connection_type)
        form.addRow("Codec", codec)
        form.addRow("FPS", fps)
        form.addRow(t("video.share.source.label", self._language), source)
        root.addLayout(form)
        timestamp = QCheckBox("Sağ üstte milisaniyeli sunucu saati", editor)
        timestamp.setChecked(current.timestamp_enabled if current else True)
        fixed_fps = QCheckBox("Sabit frame rate", editor)
        fixed_fps.setChecked(current.fixed_frame_rate if current else True)
        root.addWidget(timestamp)
        root.addWidget(fixed_fps)
        note = QLabel("Kamera kaynağı, en az 640×480 ve 15 FPS gerekir. Yayın çözünürlüğü Video Link'ten aynen alınır.", editor)
        note.setWordWrap(True)
        note.setProperty("role", "detail")
        root.addWidget(note)
        error = QLabel("", editor)
        error.setStyleSheet(f"color: {self._theme.danger};")
        error.setWordWrap(True)
        root.addWidget(error)
        buttons = QHBoxLayout()
        cancel = QPushButton(t("common.action.back", self._language), editor)
        cancel.clicked.connect(self._render)
        save = QPushButton("Kaydet", editor)
        save.setProperty("role", "primary")
        def commit() -> None:
            try:
                updated = VideoShareConfig(
                    identifier=current.identifier if current else uuid4().hex,
                    name=name.text(), endpoint=endpoint.text(),
                    connection_type=connection_type.currentText(), codec=codec.currentText(),
                    fps=fps.value(), enabled=current.enabled if current else True,
                    timestamp_enabled=timestamp.isChecked(), fixed_frame_rate=fixed_fps.isChecked(),
                    network_interface=(
                        current.network_interface
                        if current else str(self.interface_combo.currentData() or "")
                    ),
                    source_id=str(source.currentData() or "panel"),
                )
            except ValueError as exc:
                error.setText(str(exc))
                return
            self._shares = [updated if share.identifier == updated.identifier else share for share in self._shares]
            if current is None:
                self._shares.append(updated)
            self._save()
        save.clicked.connect(commit)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        if current is not None:
            delete = QPushButton("Sil", editor)
            delete.setProperty("role", "danger")
            delete.clicked.connect(lambda: self._delete_share(current.identifier))
            buttons.addWidget(delete)
        buttons.addWidget(save)
        root.addLayout(buttons)
        self.list_layout.addWidget(editor)
        self.list_layout.addStretch(1)

    def _go_back(self) -> None:
        if self._editing:
            self._render()
            return
        self.hide()

    def _delete_share(self, identifier: str) -> None:
        self._shares = [share for share in self._shares if share.identifier != identifier]
        self._save()

    def _set_share_enabled(self, identifier: str, enabled: bool) -> None:
        self._shares = [
            VideoShareConfig(
                identifier=share.identifier,
                name=share.name,
                endpoint=share.endpoint,
                connection_type=share.connection_type,
                codec=share.codec,
                fps=share.fps,
                enabled=enabled if share.identifier == identifier else share.enabled,
                timestamp_enabled=share.timestamp_enabled,
                fixed_frame_rate=share.fixed_frame_rate,
                network_interface=share.network_interface,
                source_id=share.source_id,
            )
            for share in self._shares
        ]
        self._save(defer_render=True)

    def _save(self, *, defer_render: bool = False) -> None:
        self.shares_saved.emit(tuple(self._shares))
        if defer_render:
            self._render_timer.start()
            return
        self._render()

    def _share_source_name(self, source_id: str) -> str:
        if source_id == "panel":
            active = next(
                (item.name for item in self._sources if item.identifier == self._active_source_id),
                "",
            )
            panel = t("video.share.source.panel", self._language)
            return f"{panel}{f' · {active}' if active else ''}"
        return next(
            (item.name for item in self._sources if item.identifier == source_id),
            source_id,
        )
