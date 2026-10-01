"""Telemetry-owned connection state and quality indicators."""

from PyQt6.QtWidgets import QHBoxLayout, QWidget
from src.extension.api.contracts import ConnectionSettings
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.connections import ConnectionControlService
from src.ui.adapters.services import QtTelemetryService, QtConnectionControlService
from src.ui.i18n import t
from src.ui.widgets.common import SignalMeter, StatusChip


class TelemetryStatusWidget(QWidget):
    def __init__(self, context, parent, theme, language, *, metrics=False):
        super().__init__(parent)
        self.language = language
        self.metrics = metrics
        self.telemetry = QtTelemetryService(context.services.resolve(TelemetryService), self)
        self.connections = None
        self.editor = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if metrics:
            self.indicator = SignalMeter(t("status.command_link", language), self)
        else:
            self.indicator = StatusChip(t("status.telemetry", language), self)
            if context.services.container.has(ConnectionSettings):
                self.editor = context.services.resolve(ConnectionSettings).create_ui(self, theme, language)
                self.indicator.clicked.connect(lambda: self.editor.open(self.indicator))
            if context.services.container.has(ConnectionControlService):
                self.connections = QtConnectionControlService(context.services.resolve(ConnectionControlService), self)
                self.connections.status_changed.connect(self._connection_changed)
        layout.addWidget(self.indicator)
        self.set_theme(theme)
        self.telemetry.telemetry_updated.connect(self._snapshot)
        self._snapshot(self.telemetry.current)
        if self.connections is not None:
            self._connection_changed(self.connections.status("telemetry"))

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
            self.indicator.set_state("success" if status.connected else "warning" if status.detail else "danger")
            self.indicator.setToolTip(status.detail)

    def set_theme(self, theme):
        self.indicator.set_theme(theme)
        if self.editor is not None:
            self.editor.set_theme(theme)

    def set_language(self, language):
        self.language = language
        self.indicator.set_label(t("status.command_link" if self.metrics else "status.telemetry", language))
        if self.editor is not None:
            self.editor.set_language(language)

    def prepare_shutdown(self):
        if self.editor is not None:
            self.editor.prepare_shutdown()
        self.telemetry.close()
        if self.connections is not None:
            self.connections.close()
