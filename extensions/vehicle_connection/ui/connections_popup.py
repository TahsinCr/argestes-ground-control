from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
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

from src.extension.api import ConnectionPhase
from src.models.connections import ConnectionProfile
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette


class ConnectionsPanel(QDialog):
    """Protocol-neutral connection profiles for the extension settings dialog."""

    profiles_saved = pyqtSignal(tuple)
    profile_edit_requested = pyqtSignal(object)
    profile_connect_requested = pyqtSignal(object)
    profile_delete_requested = pyqtSignal(str)

    def __init__(
        self,
        theme: ThemePalette,
        parent: QWidget | None = None,
        *,
        language: str = "tr",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ConnectionsPanel")
        self.setMinimumSize(405, 360)
        self.setMaximumHeight(590)
        self._theme, self._language = theme, language
        self._profiles: list[ConnectionProfile] = []
        self._active_identifier = ""
        self._phase = ConnectionPhase.DISCONNECTED
        self._connecting_identifier = ""
        self._detail = ""
        self._build_ui()
        self.set_theme(theme)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(f"""
            QDialog#ConnectionsPanel {{ background: {theme.surface}; border: 1px solid {theme.border_strong}; border-radius: 7px; }}
            QLabel {{ background: transparent; color: {theme.text}; }}
            QLabel[role=title] {{ font-size: 11px; font-weight: 750; }}
            QLabel[role=detail] {{ color: {theme.muted_text}; font-size: 10px; }}
            QFrame[role=card] {{ background: {theme.surface_alt}; border: 1px solid {theme.border}; border-radius: 5px; }}
            QScrollArea {{ border: 0; background: transparent; }}
        """)
        self._render()

    def set_language(self, language: str) -> None:
        self._language = language
        self.title_label.setText(t("extension.vehicle_connection.name", language))
        self.detail_label.setText(t("connection.profiles.description", language))
        self.add_button.setText(t("connection.profiles.add", language))
        self.back_button.setText(t("common.action.back", language))
        self._render()

    def show_profiles(
        self,
        profiles: tuple[ConnectionProfile, ...],
        *,
        active_identifier: str,
        phase: ConnectionPhase,
        detail: str = "",
    ) -> None:
        self._profiles = list(profiles)
        self._active_identifier = active_identifier
        self._phase = phase
        self._detail = detail
        if phase == ConnectionPhase.CONNECTED:
            self._connecting_identifier = ""
        self._render()

    def set_connecting(self, identifier: str) -> None:
        if self._connecting_identifier == identifier:
            return
        self._connecting_identifier = identifier
        self._active_identifier = identifier
        self._phase = ConnectionPhase.CONNECTING
        self._render()

    def is_connecting(self, identifier: str) -> bool:
        return self._connecting_identifier == identifier or (
            self._active_identifier == identifier
            and self._phase == ConnectionPhase.CONNECTING
        )

    def finish_connection(
        self,
        *,
        active_identifier: str,
        phase: ConnectionPhase,
        detail: str = "",
    ) -> None:
        """Unlock the selected profile after its asynchronous operation ends."""
        self._connecting_identifier = ""
        self._active_identifier = active_identifier
        self._detail = detail
        self._phase = phase
        self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = self.title_label = QLabel(
            t("extension.vehicle_connection.name", self._language), self
        )
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch(1)
        self.back_button = QPushButton(t("common.action.back", self._language), self)
        self.back_button.clicked.connect(self.hide)
        header.addWidget(self.back_button)
        root.addLayout(header)
        detail = self.detail_label = QLabel(
            t("connection.profiles.description", self._language), self
        )
        detail.setWordWrap(True)
        detail.setProperty("role", "detail")
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
        add = self.add_button = QPushButton(
            t("connection.profiles.add", self._language), self
        )
        add.setProperty("role", "primary")
        add.clicked.connect(lambda: self.profile_edit_requested.emit(None))
        root.addWidget(add)

    def _render(self) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for profile in self._profiles:
            self._add_card(profile)
        self.layout.addStretch(1)

    def _add_card(self, profile: ConnectionProfile) -> None:
        active = profile.identifier == self._active_identifier
        in_progress = self._phase in {
            ConnectionPhase.CONNECTING,
            ConnectionPhase.DISCONNECTING,
        }
        color = (
            self._theme.success
            if active and self._phase == ConnectionPhase.CONNECTED
            else self._theme.warning
            if active and in_progress
            else self._theme.danger
        )
        card = QFrame(self.host)
        card.setProperty("role", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(3)
        operation_active = bool(self._connecting_identifier) or (
            active and in_progress
        )
        row = QHBoxLayout()
        dot = QLabel("●", card)
        dot.setStyleSheet(f"color: {color};")
        row.addWidget(dot)
        name = QLabel(profile.name, card)
        name.setStyleSheet("font-weight: 700;")
        row.addWidget(name, 1)
        edit = QPushButton(t("connection.profiles.edit", self._language), card)
        edit.setProperty("compact", "true")
        edit.setEnabled(not operation_active)
        edit.clicked.connect(
            lambda _=False, p=profile: self.profile_edit_requested.emit(p)
        )
        row.addWidget(edit)
        layout.addLayout(row)
        endpoint = QLabel(profile.endpoint, card)
        endpoint.setProperty("role", "detail")
        endpoint.setWordWrap(True)
        layout.addWidget(endpoint)
        is_connecting = self.is_connecting(profile.identifier)
        disconnecting = active and self._phase == ConnectionPhase.DISCONNECTING
        if active and self._phase == ConnectionPhase.CONNECTED:
            state_key = "connection.profile.connected"
        elif is_connecting:
            state_key = "connection.profile.connecting"
        elif disconnecting:
            state_key = "connection.profile.disconnecting"
        else:
            state_key = "connection.profile.waiting"
        state = t(state_key, self._language)
        info = QLabel(
            f"{profile.baud} baud · {profile.heartbeat_timeout} sn · {self._detail if active and self._detail else state}",
            card,
        )
        info.setProperty("role", "detail")
        info.setWordWrap(True)
        layout.addWidget(info)
        actions = QHBoxLayout()
        auto = QPushButton(
            t(
                "connection.profile.auto_on"
                if profile.auto_connect
                else "connection.profile.auto_off",
                self._language,
            ),
            card,
        )
        auto.setCheckable(True)
        auto.setChecked(profile.auto_connect)
        auto.setEnabled(not operation_active)
        auto.setProperty("compact", "true")
        auto.setProperty("role", "primary" if profile.auto_connect else "secondary")
        auto.clicked.connect(
            lambda checked, ident=profile.identifier: self._set_auto(ident, checked)
        )
        actions.addWidget(auto)
        disconnect_action = (
            active and self._phase == ConnectionPhase.CONNECTED
        ) or is_connecting
        connect_key = (
            "connection.profile.disconnecting"
            if disconnecting
            else "connection.action.disconnect"
            if disconnect_action
            else "connection.action.connect"
        )
        connect = QPushButton(t(connect_key, self._language), card)
        connect.setProperty("compact", "true")
        connect.setProperty(
            "role",
            "danger"
            if disconnect_action
            else "secondary"
            if disconnecting
            else "primary",
        )
        connect.setEnabled(
            (not operation_active or is_connecting) and not disconnecting
        )
        connect.clicked.connect(
            lambda _=False, p=profile: self.profile_connect_requested.emit(p)
        )
        actions.addWidget(connect)
        delete = QPushButton(t("connection.profiles.delete", self._language), card)
        delete.setObjectName("deleteProfileButton")
        delete.setProperty("compact", "true")
        delete.setProperty("role", "danger")
        delete.setEnabled(True)
        delete.clicked.connect(
            lambda _=False, ident=profile.identifier: self.profile_delete_requested.emit(ident)
        )
        actions.addWidget(delete)
        layout.addLayout(actions)
        self.layout.addWidget(card)

    def _set_auto(self, identifier: str, enabled: bool) -> None:
        self._profiles = [
            replace(p, auto_connect=enabled) if p.identifier == identifier else p
            for p in self._profiles
        ]
        self.profiles_saved.emit(tuple(self._profiles))
        self._render_timer.start()

    def _delete(self, identifier: str) -> None:
        self.profile_delete_requested.emit(identifier)
