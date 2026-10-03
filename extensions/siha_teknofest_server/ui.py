"""Archived profile cards and an independent profile editor."""

from dataclasses import asdict

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.services.settings import SettingsService
from src.ui.i18n import t
from src.ui.themes.palette import palette_for
from src.ui.widgets.anchored_popup import open_above_anchor
from src.ui.widgets.common import StatusChip

from .connections_popup import CompetitionServersPopup
from .models import CompetitionServerConfig
from .runtime import CompetitionRuntime


class ServerPanel(QWidget):
    def __init__(self, context, parent=None, *, popup_mode=False):
        super().__init__(parent)
        self.settings = context.services.resolve(SettingsService)
        self.runtime = context.services.resolve(CompetitionRuntime)
        self.language = self.settings.current.ui.language
        self.popup_mode, self.pending, self.anchor = popup_mode, None, None
        self.profiles, self._signature = [], None
        self.popup = CompetitionServersPopup(
            palette_for(self.settings.current.ui.theme), self, language=self.language
        )
        self.popup.server_edit_requested.connect(self.edit)
        self.popup.servers_saved.connect(self.save_profiles)
        self.popup.server_connect_requested.connect(self.connect_profile)
        if not popup_mode:
            self.popup.setWindowFlags(Qt.WindowType.Widget)
            self.popup.setMinimumSize(0, 0)
            self.popup.setMaximumSize(16777215, 16777215)
            self.popup.back_button.hide()
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.popup)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def refresh(self):
        if self.pending is not None and self.pending.done():
            error = self.pending.exception()
            self.pending = None
            if error:
                QMessageBox.warning(self, "TEKNOFEST", str(error))
        profiles = tuple(
            CompetitionServerConfig(**item)
            for item in self.settings.extension_config("siha_teknofest_server").get(
                "servers", []
            )
        )
        states = {
            key: (session.connected, session.running, session.error)
            for key, session in self.runtime.sessions.copy().items()
        }
        signature = (profiles, tuple(states.items()), self.pending is None)
        if signature != self._signature:
            self._signature, self.profiles = signature, list(profiles)
            self.popup._server_states = states
            self.popup.show_servers(
                profiles,
                active_identifier="",
                connected=False,
                running=False,
                available=self.pending is None,
            )

    def save_profiles(self, profiles):
        try:
            self.pending = self.runtime.configure([asdict(item) for item in profiles])
            self.refresh()
        except (ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "TEKNOFEST", str(error))

    def connect_profile(self, profile):
        session = self.runtime.sessions.get(profile.identifier)
        if session is None or self.pending is not None:
            return
        if session.running:
            self.pending = self.runtime.disconnect(profile.identifier)
        else:
            self.runtime.connect(profile.identifier)
        self.refresh()

    def open(self, anchor):
        self.anchor = anchor
        self.refresh()
        open_above_anchor(self.popup, anchor)
        self.timer.start()

    def edit(self, profile=None):
        profile = profile or CompetitionServerConfig(name="TEKNOFEST")
        dialog = QDialog(self)
        dialog.setWindowTitle(t("teknofest.edit", self.language))
        dialog.resize(480, 360)
        root = QVBoxLayout(dialog)
        box = QGroupBox(
            t("extension.siha_teknofest_server.name", self.language), dialog
        )
        form = QFormLayout(box)
        form.setSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        controls = {}
        for key in ("name", "base_url", "username", "password"):
            field = QLineEdit(str(getattr(profile, key)), box)
            if key == "password":
                field.setEchoMode(QLineEdit.EchoMode.Password)
            controls[key] = field
            form.addRow(t("teknofest." + key, self.language), field)
        team, timeout = QSpinBox(box), QDoubleSpinBox(box)
        team.setRange(1, 9999)
        team.setValue(profile.team_number)
        timeout.setRange(0.1, 15)
        timeout.setValue(profile.timeout_s)
        timeout.setSuffix(" s")
        auto = QCheckBox(t("teknofest.auto", self.language), box)
        auto.setChecked(profile.auto_connect)
        form.addRow(t("teknofest.team", self.language), team)
        form.addRow(t("teknofest.timeout", self.language), timeout)
        form.addRow(auto)
        root.addWidget(box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        root.addWidget(buttons)
        result = []

        def accept():
            try:
                result.append(
                    CompetitionServerConfig(
                        identifier=profile.identifier,
                        team_number=team.value(),
                        timeout_s=timeout.value(),
                        auto_connect=auto.isChecked(),
                        **{
                            key: field.text().strip() for key, field in controls.items()
                        },
                    )
                )
            except ValueError as error:
                QMessageBox.warning(dialog, "TEKNOFEST", str(error))
                return
            dialog.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = result[0]
            profiles = [
                updated if item.identifier == updated.identifier else item
                for item in self.profiles
            ]
            if not any(item.identifier == updated.identifier for item in self.profiles):
                profiles.append(updated)
            self.save_profiles(profiles)
        dialog.deleteLater()
        if self.popup_mode and self.anchor is not None:
            self.open(self.anchor)

    def set_theme(self, theme):
        self.popup.set_theme(theme)

    def set_language(self, language):
        self.language = language
        self.popup.set_language(language)

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def prepare_shutdown(self):
        self.timer.stop()
        self.popup.close()


class ServerStatusWidget(QWidget):
    def __init__(self, context, parent, theme, language):
        super().__init__(parent)
        self.context, self.theme, self.language = context, theme, language
        self.runtime = context.services.resolve(CompetitionRuntime)
        self.popup = self.panel = None
        self.indicator = StatusChip("TEKNOFEST", self)
        self.indicator.setToolTip(t("extension.siha_teknofest_server.name", language))
        self.indicator.set_theme(theme)
        self.indicator.clicked.connect(self.open)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.indicator)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def open(self):
        if self.panel is None:
            self.panel = ServerPanel(self.context, self, popup_mode=True)
            self.popup = self.panel.popup
            self.panel.set_theme(self.theme)
            self.panel.set_language(self.language)
        self.panel.open(self.indicator)

    def refresh(self):
        self.indicator.set_state(
            "success"
            if any(item.connected for item in self.runtime.sessions.copy().values())
            else "danger"
        )
        if (
            self.panel is not None
            and not self.popup.isVisible()
            and self.panel.pending is None
        ):
            self.panel.timer.stop()

    def set_theme(self, theme):
        self.theme = theme
        self.indicator.set_theme(theme)
        if self.panel is not None:
            self.panel.set_theme(theme)

    def set_language(self, language):
        self.language = language
        self.indicator.setToolTip(t("extension.siha_teknofest_server.name", language))
        if self.panel is not None:
            self.panel.set_language(language)

    def prepare_shutdown(self):
        self.timer.stop()
        if self.panel is not None:
            self.panel.prepare_shutdown()
