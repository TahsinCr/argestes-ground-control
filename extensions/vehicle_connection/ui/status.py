"""Connection-extension-owned state and quality indicators."""

from PyQt6.QtWidgets import QHBoxLayout, QWidget

from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.telemetry import TelemetryService
from src.ui.adapters.services import QtConnectionControlService, QtTelemetryService
from src.ui.i18n import t
from src.ui.widgets.common import SignalMeter, StatusChip


class ConnectionStatusWidget(QWidget):
    def __init__(self, context, parent, theme, language, *, metrics=False):
        super().__init__(parent)
        self.context = context
        self.theme = theme
        self.editor = None
        self.language = language
        self.metrics = metrics
        self.telemetry = QtTelemetryService(
            context.services.resolve(TelemetryService), self
        )
        self.connections = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if metrics:
            self.indicator = SignalMeter(t("status.command_link", language), self)
        else:
            self.indicator = StatusChip(t("status.telemetry", language), self)
            self.indicator.clicked.connect(self._open_connections)
            if context.services.container.has(ConnectionControlService):
                self.connections = QtConnectionControlService(
                    context.services.resolve(ConnectionControlService), self
                )
                self.connections.status_changed.connect(self._connection_changed)
        layout.addWidget(self.indicator)
        self.set_theme(theme)
        self.telemetry.telemetry_updated.connect(self._snapshot)
        self._snapshot(self.telemetry.current)
        if self.connections is not None:
            self._connection_changed(self.connections.status("telemetry"))

    def _open_connections(self):
        from src.extension.api.contracts import ConnectionProfiles

        from .connection_editor import ConnectionEditor

        if not self.context.services.container.has(ConnectionProfiles):
            return
        if self.editor is None:
            self.editor = ConnectionEditor(
                self.context, self, self.theme, self.language
            )
            self.editor.profiles_panel.back_button.clicked.connect(
                self.editor.profiles_panel.close
            )
        self.editor.open(self.indicator)

    def _snapshot(self, snapshot):
        if self.metrics:
            if snapshot.rc_rssi is None or not snapshot.telemetry_link:
                self.indicator.set_unavailable()
            else:
                self.indicator.set_value(int(snapshot.rc_rssi), "RC RSSI")
        else:
            self.indicator.set_state("success" if snapshot.telemetry_link else "danger")

    def _connection_changed(self, status):
        if status.channel == "telemetry":
            self.indicator.set_state(
                "success"
                if status.connected
                else "warning"
                if status.detail
                else "danger"
            )
            self.indicator.setToolTip(status.detail)

    def set_theme(self, theme):
        self.theme = theme
        self.indicator.set_theme(theme)
        if self.editor is not None:
            self.editor.set_theme(theme)

    def set_language(self, language):
        self.language = language
        if self.editor is not None:
            self.editor.set_language(language)
        self.indicator.set_label(
            t("status.command_link" if self.metrics else "status.telemetry", language)
        )

    def prepare_shutdown(self):
        if self.editor is not None:
            self.editor.prepare_shutdown()
        self.telemetry.close()
        if self.connections is not None:
            self.connections.close()
