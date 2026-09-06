from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.models.settings import MavlinkConnectionConfig
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette


class MavlinkConnectionsPopup(QDialog):
    """Selectable MAVLink profiles, styled consistently with server profiles."""

    profiles_saved = pyqtSignal(tuple)
    profile_edit_requested = pyqtSignal(object)
    profile_connect_requested = pyqtSignal(object)

    def __init__(self, theme: ThemePalette, parent: QWidget | None = None, *, language: str = "tr") -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("MavlinkConnectionsPopup")
        self.setMinimumSize(405, 360)
        self.setMaximumHeight(590)
        self._theme, self._language = theme, language
        self._profiles: list[MavlinkConnectionConfig] = []
        self._active_identifier = ""
        self._connected = self._running = False
        self._connecting_identifier = ""
        self._detail = ""
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render)
        self._build_ui()
        self.set_theme(theme)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(f"""
            QDialog#MavlinkConnectionsPopup {{ background: {theme.surface}; border: 1px solid {theme.border_strong}; border-radius: 7px; }}
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

    def show_profiles(self, profiles: tuple[MavlinkConnectionConfig, ...], *, active_identifier: str, connected: bool, running: bool, detail: str = "") -> None:
        self._profiles = list(profiles)
        self._active_identifier, self._connected, self._running, self._detail = active_identifier, connected, running, detail
        if self._connected:
            self._connecting_identifier = ""
        self._render()

    def set_connecting(self, identifier: str) -> None:
        if self._connecting_identifier == identifier:
            return
        self._connecting_identifier = identifier
        self._active_identifier, self._connected, self._running = identifier, False, True
        self._render()

    def finish_connection(self, *, active_identifier: str, connected: bool, running: bool, detail: str = "") -> None:
        """Unlock the selected profile after its asynchronous operation ends."""
        self._connecting_identifier = ""
        self._active_identifier = active_identifier
        self._connected = bool(connected)
        self._running = bool(running)
        self._detail = detail
        self._render()

    def update_connection(
        self,
        *,
        active_identifier: str,
        connected: bool,
        running: bool,
        detail: str = "",
    ) -> None:
        signature = (active_identifier, bool(connected), bool(running), detail)
        previous = (self._active_identifier, self._connected, self._running, self._detail)
        if signature == previous:
            return
        self._active_identifier, self._connected, self._running, self._detail = signature
        if self._connected:
            self._connecting_identifier = ""
        self._render_timer.start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(12, 10, 12, 12); root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("TELEMETRİ", self); title.setProperty("role", "title")
        header.addWidget(title); header.addStretch(1)
        self.back_button = QPushButton(t("common.action.back", self._language), self); self.back_button.clicked.connect(self.hide)
        header.addWidget(self.back_button); root.addLayout(header)
        detail = QLabel("Yalnızca bir MAVLink telemetri profili aktiftir. Otomatik bağlan profilleri sırayla denenir.", self)
        detail.setWordWrap(True); detail.setProperty("role", "detail"); root.addWidget(detail)
        self.scroll = QScrollArea(self); self.scroll.setWidgetResizable(True); self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.host = QWidget(self.scroll); self.layout = QVBoxLayout(self.host); self.layout.setContentsMargins(0, 0, 3, 0); self.layout.setSpacing(6)
        self.scroll.setWidget(self.host); root.addWidget(self.scroll, 1)
        add = QPushButton("+ Telemetri ekle", self); add.setProperty("role", "primary"); add.clicked.connect(lambda: self.profile_edit_requested.emit(None)); root.addWidget(add)

    def _render(self) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for profile in self._profiles: self._add_card(profile)
        self.layout.addStretch(1)

    def _add_card(self, profile: MavlinkConnectionConfig) -> None:
        active = profile.identifier == self._active_identifier
        color = self._theme.success if active and self._connected else self._theme.warning if active and self._running else self._theme.danger
        card = QFrame(self.host); card.setProperty("role", "card")
        layout = QVBoxLayout(card); layout.setContentsMargins(8, 7, 8, 7); layout.setSpacing(3)
        operation_active = bool(self._connecting_identifier)
        row = QHBoxLayout(); dot = QLabel("●", card); dot.setStyleSheet(f"color: {color};"); row.addWidget(dot)
        name = QLabel(profile.name, card); name.setStyleSheet("font-weight: 700;"); row.addWidget(name, 1)
        edit = QPushButton("Düzenle", card); edit.setProperty("compact", "true"); edit.setEnabled(not operation_active); edit.clicked.connect(lambda _=False, p=profile: self.profile_edit_requested.emit(p)); row.addWidget(edit); layout.addLayout(row)
        endpoint = QLabel(profile.endpoint, card); endpoint.setProperty("role", "detail"); endpoint.setWordWrap(True); layout.addWidget(endpoint)
        is_connecting = profile.identifier == self._connecting_identifier
        state = "Bağlı" if active and self._connected else "Bağlanıyor" if is_connecting else "Beklemede"
        info = QLabel(f"{profile.baud} baud · {profile.heartbeat_timeout} sn · {self._detail if active and self._detail else state}", card); info.setProperty("role", "detail"); info.setWordWrap(True); layout.addWidget(info)
        actions = QHBoxLayout()
        auto = QPushButton("OTO: AÇIK" if profile.auto_connect else "OTO: KAPALI", card); auto.setCheckable(True); auto.setChecked(profile.auto_connect); auto.setEnabled(not operation_active); auto.setProperty("compact", "true"); auto.setProperty("role", "primary" if profile.auto_connect else "secondary")
        auto.clicked.connect(lambda checked, ident=profile.identifier: self._set_auto(ident, checked)); actions.addWidget(auto)
        busy = is_connecting
        connect = QPushButton("Bağlantıyı Kes" if active and self._connected else "Bağlanıyor" if busy else "Bağlan", card); connect.setProperty("compact", "true"); connect.setProperty("role", "danger" if active and self._connected else "primary"); connect.setEnabled(not operation_active); connect.clicked.connect(lambda _=False, p=profile: self.profile_connect_requested.emit(p)); actions.addWidget(connect)
        delete = QPushButton("Sil", card); delete.setProperty("compact", "true"); delete.setProperty("role", "danger"); delete.setEnabled(len(self._profiles) > 1 and not operation_active); delete.clicked.connect(lambda _=False, ident=profile.identifier: self._delete(ident)); actions.addWidget(delete)
        layout.addLayout(actions); self.layout.addWidget(card)

    def _set_auto(self, identifier: str, enabled: bool) -> None:
        self._profiles = [replace(p, auto_connect=enabled) if p.identifier == identifier else p for p in self._profiles]
        self.profiles_saved.emit(tuple(self._profiles)); self._render_timer.start()

    def _delete(self, identifier: str) -> None:
        self._profiles = [p for p in self._profiles if p.identifier != identifier]
        self.profiles_saved.emit(tuple(self._profiles)); self._render()
