"""MAVLink-owned connection profile editor."""
from PyQt6.QtWidgets import QWidget, QDialog, QFormLayout, QLineEdit, QSpinBox, QDialogButtonBox, QMessageBox
from src.models.settings import MavlinkConnectionConfig, SettingsModel
from src.services.abstracts.connections import ConnectionControlService
from src.services.settings import SettingsService
from src.ui.adapters.services import QtConnectionControlService, QtSettingsService
from .connections_popup import MavlinkConnectionsPopup


class ConnectionEditor(QWidget):
    def __init__(self, context, parent, theme, language):
        super().__init__(parent)
        self.hide()
        self.settings_service = QtSettingsService(context.services.resolve(SettingsService), self)
        self.connection_service = QtConnectionControlService(context.services.resolve(ConnectionControlService), self)
        self._settings = self.settings_service.current
        self.mavlink_popup = MavlinkConnectionsPopup(theme, self, language=language)
        self.mavlink_popup.profiles_saved.connect(self._save_mavlink_profiles)
        self.mavlink_popup.profile_edit_requested.connect(self._edit_mavlink_profile)
        self.mavlink_popup.profile_connect_requested.connect(self._connect_mavlink_profile)
        self.settings_service.settings_changed.connect(self._settings_changed)
        self.connection_service.status_changed.connect(lambda _: self._show_mavlink_profiles())
        self._show_mavlink_profiles()

    def _settings_changed(self, settings):
        self._settings = settings
        self._show_mavlink_profiles()

    def set_theme(self, theme):
        self.mavlink_popup.set_theme(theme)

    def set_language(self, language):
        self.mavlink_popup.set_language(language)

    def prepare_shutdown(self):
        self.mavlink_popup.close()
        self.settings_service.close()
        self.connection_service.close()

    def _show_mavlink_profiles(self) -> None:
        backend = self._settings.backend
        status = self.connection_service.status("telemetry")
        self.mavlink_popup.show_profiles(
            tuple(backend.mavlink_connections),
            active_identifier=backend.active_mavlink_connection_id,
            connected=status.connected,
            running=bool(status.options.get("running", status.connected)),
            detail=status.detail,
        )

    def open(self, anchor) -> None:
        self._anchor = anchor
        self._show_mavlink_profiles()
        self.mavlink_popup.adjustSize()
        anchor = self._anchor
        self.mavlink_popup.move(anchor.mapToGlobal(anchor.rect().topLeft()))
        self.mavlink_popup.show()
        self.mavlink_popup.raise_()

    def _connect_mavlink_profile(self, profile: MavlinkConnectionConfig) -> None:
        self._save_mavlink_profiles(tuple(self._settings.backend.mavlink_connections), profile.identifier)
        status = self.connection_service.status("telemetry")
        if status.connected and profile.identifier == self._settings.backend.active_mavlink_connection_id:
            self.connection_service.disconnect("telemetry")
            return
        self.mavlink_popup.set_connecting(profile.identifier)
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

    def _edit_mavlink_profile(self, profile: MavlinkConnectionConfig | None) -> None:
        existing = tuple(self._settings.backend.mavlink_connections)
        template = self._settings.backend.mavlink
        profile = profile or MavlinkConnectionConfig(
            name=f"Telemetri {len(existing) + 1}",
            endpoint=template.endpoint,
            baud=template.baud,
            heartbeat_timeout=template.heartbeat_timeout,
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("Telemetri bağlantısı")
        form = QFormLayout(dialog)
        name = QLineEdit(profile.name, dialog)
        endpoint = QLineEdit(profile.endpoint, dialog)
        baud = QSpinBox(dialog); baud.setRange(1200, 4_000_000); baud.setValue(profile.baud)
        timeout = QSpinBox(dialog); timeout.setRange(1, 120); timeout.setValue(profile.heartbeat_timeout)
        form.addRow("İsim", name); form.addRow("MAVLink adresi", endpoint)
        form.addRow("Baud", baud); form.addRow("Zaman aşımı", timeout)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = MavlinkConnectionConfig(
            identifier=profile.identifier,
            name=name.text().strip(),
            endpoint=endpoint.text().strip(),
            baud=baud.value(),
            heartbeat_timeout=timeout.value(),
            auto_connect=profile.auto_connect,
        )
        profiles = [updated if item.identifier == profile.identifier else item for item in existing]
        if not any(item.identifier == updated.identifier for item in existing):
            profiles.append(updated)
        self._save_mavlink_profiles(tuple(profiles), updated.identifier)

    def _save_mavlink_profiles(self, profiles, active_identifier: str | None = None) -> None:
        payload = self._settings.to_dict()
        payload["backend"]["mavlink_connections"] = [item.to_dict() for item in profiles]
        if active_identifier is not None:
            payload["backend"]["active_mavlink_connection_id"] = active_identifier
        try:
            self.settings_service.save_async(SettingsModel.from_dict(payload))
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Telemetri bağlantısı", str(exc))
