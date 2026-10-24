"""Provider-neutral vehicle connection profile editor."""

from uuid import uuid4

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QWidget,
)

from src.extension.api.contracts import ConnectionProfiles
from src.models.connections import ConnectionProfile
from src.services.abstracts.connections import ConnectionControlService
from src.services.settings import SettingsService
from src.ui.adapters.services import QtConnectionControlService, QtSettingsService
from src.ui.i18n import t

from .connections_popup import ConnectionsPanel


class ConnectionEditor(QWidget):
    def __init__(self, context, parent, theme, language):
        super().__init__(parent)
        self.hide()
        self.settings_service = QtSettingsService(
            context.services.resolve(SettingsService), self
        )
        self.connection_service = QtConnectionControlService(
            context.services.resolve(ConnectionControlService), self
        )
        self.profiles = context.services.resolve(ConnectionProfiles)
        self.language = language
        self._latest_operation_id = 0
        self._pending_profile_identifier = ""
        self.profiles_panel = ConnectionsPanel(theme, self, language=language)
        self.profiles_panel.profiles_saved.connect(self._save_profiles)
        self.profiles_panel.profile_edit_requested.connect(self._edit_profile)
        self.profiles_panel.profile_connect_requested.connect(self._connect_profile)
        self.settings_service.settings_changed.connect(self._settings_changed)
        self.connection_service.status_changed.connect(lambda _: self._show_profiles())
        self.connection_service.operation_started.connect(self._operation_started)
        self.connection_service.operation_finished.connect(self._operation_finished)
        self._show_profiles()

    def _settings_changed(self, settings):
        self._show_profiles()

    def set_theme(self, theme):
        self.profiles_panel.set_theme(theme)

    def set_language(self, language):
        self.language = language
        self.profiles_panel.set_language(language)

    def _operation_started(self, channel: str, operation_id: int, _action: str) -> None:
        if channel != "telemetry":
            return
        self._latest_operation_id = operation_id
        if self._pending_profile_identifier:
            self.profiles_panel.set_connecting(self._pending_profile_identifier)

    def _operation_finished(
        self,
        channel: str,
        operation_id: int,
        _action: str,
        successful: bool,
        message: str,
        status,
    ) -> None:
        if channel != "telemetry" or operation_id != self._latest_operation_id:
            return
        status = status or self.connection_service.status(channel)
        detail = status.detail
        if not successful:
            detail = (
                t("connection.operation.in_progress", self.language)
                if message == "operation_in_progress"
                else message or detail
            )
        elif not detail:
            detail = message
        self.profiles_panel.finish_connection(
            active_identifier=self.profiles.active_identifier,
            connected=status.connected,
            running=bool(status.options.get("running", status.connected)),
            detail=detail,
        )

    def prepare_shutdown(self):
        self.profiles_panel.close()
        self.settings_service.close()
        self.connection_service.close()

    def _show_profiles(self) -> None:
        status = self.connection_service.status("telemetry")
        self.profiles_panel.show_profiles(
            self.profiles.profiles,
            active_identifier=self.profiles.active_identifier,
            connected=status.connected,
            running=bool(status.options.get("running", status.connected)),
            detail=status.detail,
        )

    def open(self, anchor) -> None:
        from src.ui.widgets.anchored_popup import open_above_anchor

        self._show_profiles()
        self._popup_anchor = anchor
        open_above_anchor(self.profiles_panel, anchor)

    def _connect_profile(self, profile: ConnectionProfile) -> None:
        status = self.connection_service.status("telemetry")
        self._pending_profile_identifier = profile.identifier
        try:
            if self.profiles_panel.is_connecting(profile.identifier) or (
                status.connected and profile.identifier == self.profiles.active_identifier
            ):
                self.connection_service.disconnect("telemetry")
                return
            self.connection_service.reconnect(
                "telemetry",
                {
                    "profile_id": profile.identifier,
                    "profile_name": profile.name,
                    "endpoint": profile.endpoint,
                    "baud": profile.baud,
                    "timeout_s": profile.heartbeat_timeout,
                    "auto_connect": profile.auto_connect,
                },
            )
        finally:
            self._pending_profile_identifier = ""

    def _edit_profile(self, profile: ConnectionProfile | None) -> None:
        existing = tuple(self.profiles.profiles)
        template = self.profiles.default_profile
        profile = profile or ConnectionProfile(
            identifier=uuid4().hex,
            name=f"{t('status.telemetry', self.language)} {len(existing) + 1}",
            endpoint=template.endpoint,
            baud=template.baud,
            heartbeat_timeout=template.heartbeat_timeout,
        )
        dialog = QDialog(self)
        dialog.setWindowTitle(t("extension.vehicle_connection.name", self.language))
        form = QFormLayout(dialog)
        name = QLineEdit(profile.name, dialog)
        endpoint = QLineEdit(profile.endpoint, dialog)
        baud = QSpinBox(dialog)
        baud.setRange(1200, 4_000_000)
        baud.setValue(profile.baud)
        timeout = QSpinBox(dialog)
        timeout.setRange(1, 120)
        timeout.setValue(profile.heartbeat_timeout)
        form.addRow(t("connection.profile.name", self.language), name)
        form.addRow(t("connection.label.address", self.language), endpoint)
        form.addRow("Baud", baud)
        form.addRow(t("connection.label.timeout", self.language), timeout)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        if self.profiles_panel.isWindow() and hasattr(self, "_popup_anchor"):
            self.open(self._popup_anchor)
        if not accepted:
            return
        updated = ConnectionProfile(
            identifier=profile.identifier,
            name=name.text().strip(),
            endpoint=endpoint.text().strip(),
            baud=baud.value(),
            heartbeat_timeout=timeout.value(),
            auto_connect=profile.auto_connect,
        )
        profiles = [
            updated if item.identifier == profile.identifier else item
            for item in existing
        ]
        if not any(item.identifier == updated.identifier for item in existing):
            profiles.append(updated)
        self._save_profiles(tuple(profiles), updated.identifier)

    def _save_profiles(self, profiles, active_identifier: str | None = None) -> None:
        try:
            self.settings_service.save_async(
                self.profiles.settings_payload(profiles, active_identifier)
            )
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(
                self, t("extension.vehicle_connection.name", self.language), str(exc)
            )
