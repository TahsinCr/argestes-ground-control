from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.models.competition import CompetitionServerConfig
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette


class CompetitionServersPopup(QDialog):
    """Profile list for TEKNOFEST servers; editing stays in ConnectionPopup."""

    servers_saved = pyqtSignal(tuple)
    server_edit_requested = pyqtSignal(object)
    server_connect_requested = pyqtSignal(object)

    def __init__(self, theme: ThemePalette, parent: QWidget | None = None, *, language: str = "tr") -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("CompetitionServersPopup")
        self.setMinimumSize(405, 360)
        self.setMaximumHeight(590)
        self._theme = theme
        self._language = language
        self._servers: list[CompetitionServerConfig] = []
        self._active_identifier = ""
        self._connected = False
        self._running = False
        self._detail = ""
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render)
        self._build_ui()
        self.set_theme(theme)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"""
            QDialog#CompetitionServersPopup {{ background: {theme.surface}; border: 1px solid {theme.border_strong}; border-radius: 7px; }}
            QLabel {{ background: transparent; color: {theme.text}; }}
            QLabel[role=title] {{ font-size: 11px; font-weight: 750; }}
            QLabel[role=detail] {{ color: {theme.muted_text}; font-size: 10px; }}
            QFrame[role=server-card] {{ background: {theme.surface_alt}; border: 1px solid {theme.border}; border-radius: 5px; }}
            QScrollArea {{ border: 0; background: transparent; }}
            """
        )
        self._render()

    def set_language(self, language: str) -> None:
        self._language = language
        self.back_button.setText(t("common.action.back", language))
        self._render()

    def update_connection(
        self,
        *,
        active_identifier: str,
        connected: bool,
        running: bool,
        detail: str = "",
    ) -> None:
        signature = (active_identifier, bool(connected), bool(running))
        previous = (self._active_identifier, self._connected, self._running)
        if signature == previous:
            return
        self._active_identifier, self._connected, self._running = signature
        self._detail = detail
        self._render()

    def set_connecting(self, identifier: str) -> None:
        if (
            self._active_identifier == identifier
            and not self._connected
            and self._running
        ):
            return
        self._active_identifier = identifier
        self._connected = False
        self._running = True
        self._detail = t("competition.server.connecting", self._language)
        self._render()

    def show_servers(
        self,
        servers: tuple[CompetitionServerConfig, ...],
        *,
        active_identifier: str,
        connected: bool,
        running: bool,
        detail: str = "",
    ) -> None:
        self._servers = list(servers)
        self._active_identifier = active_identifier
        self._connected = connected
        self._running = running
        self._detail = detail
        self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("TEKNOFEST", self)
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch(1)
        self.back_button = QPushButton(t("common.action.back", self._language), self)
        self.back_button.clicked.connect(self.hide)
        header.addWidget(self.back_button)
        root.addLayout(header)
        detail = QLabel(
            "Yalnızca bir sunucu aktif olur. Açılışta otomatik bağlanma seçili hedefler sırayla denenir.",
            self,
        )
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
        self.add_button = QPushButton("+ Sunucu ekle", self)
        self.add_button.setProperty("role", "primary")
        self.add_button.clicked.connect(lambda: self.server_edit_requested.emit(None))
        root.addWidget(self.add_button)

    def _render(self) -> None:
        self._clear_list()
        for server in self._servers:
            self._add_card(server)
        self.list_layout.addStretch(1)

    def _add_card(self, server: CompetitionServerConfig) -> None:
        active = server.identifier == self._active_identifier
        status = "success" if active and self._connected else "warning" if active and self._running else "danger"
        card = QFrame(self.list_host)
        card.setProperty("role", "server-card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(3)
        row = QHBoxLayout()
        dot = QLabel("●", card)
        dot.setStyleSheet(f"color: {getattr(self._theme, status)};")
        row.addWidget(dot)
        name = QLabel(server.name, card)
        name.setStyleSheet("font-weight: 700;")
        row.addWidget(name, 1)
        edit = QPushButton("Düzenle", card)
        edit.setProperty("compact", "true")
        edit.clicked.connect(lambda _checked=False, item=server: self.server_edit_requested.emit(item))
        row.addWidget(edit)
        layout.addLayout(row)
        endpoint = QLabel(server.base_url, card)
        endpoint.setProperty("role", "detail")
        endpoint.setWordWrap(True)
        layout.addWidget(endpoint)
        state = "Bağlı" if active and self._connected else "Bağlanıyor" if active and self._running else "Beklemede"
        detail = self._detail if active and self._detail else state
        info = QLabel(f"Takım {server.team_number} · {state} · {detail}", card)
        info.setProperty("role", "detail")
        info.setWordWrap(True)
        layout.addWidget(info)
        actions = QHBoxLayout()
        auto = QPushButton("OTO: AÇIK" if server.auto_connect else "OTO: KAPALI", card)
        auto.setCheckable(True)
        auto.setChecked(server.auto_connect)
        auto.setProperty("role", "primary" if server.auto_connect else "secondary")
        auto.setProperty("compact", "true")
        auto.clicked.connect(lambda checked, identifier=server.identifier: self._set_auto_connect(identifier, checked))
        actions.addWidget(auto)
        is_connecting = active and self._running and not self._connected
        connect = QPushButton(
            t("competition.server.disconnect", self._language)
            if active and self._connected
            else t("competition.server.connecting", self._language)
            if is_connecting
            else t("competition.server.connect", self._language),
            card,
        )
        connect.setProperty("role", "danger" if active and self._connected else "primary")
        connect.setProperty("compact", "true")
        connect.setEnabled(not is_connecting)
        connect.clicked.connect(lambda _checked=False, item=server: self.server_connect_requested.emit(item))
        actions.addWidget(connect)
        delete = QPushButton("Sil", card)
        delete.setProperty("role", "danger")
        delete.setProperty("compact", "true")
        delete.setEnabled(len(self._servers) > 1)
        delete.clicked.connect(lambda _checked=False, identifier=server.identifier: self._delete_server(identifier))
        actions.addWidget(delete)
        layout.addLayout(actions)
        self.list_layout.addWidget(card)

    def _set_auto_connect(self, identifier: str, enabled: bool) -> None:
        self._servers = [
            replace(server, auto_connect=enabled) if server.identifier == identifier else server
            for server in self._servers
        ]
        self.servers_saved.emit(tuple(self._servers))
        self._render_timer.start()

    def _delete_server(self, identifier: str) -> None:
        self._servers = [server for server in self._servers if server.identifier != identifier]
        self.servers_saved.emit(tuple(self._servers))
        self._render()

    def _clear_list(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
