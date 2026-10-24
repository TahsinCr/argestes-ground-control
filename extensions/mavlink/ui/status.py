"""MAVLink-owned autopilot connection status; it never opens a popup."""

from src.services.abstracts.connections import ConnectionControlService
from src.ui.adapters.services import QtConnectionControlService
from src.ui.i18n import t
from src.ui.widgets.common import StatusChip


class ProtocolStatusChip(StatusChip):
    def __init__(self, context, parent, theme, language, channel):
        self.channel = channel
        super().__init__(t(f"status.{channel}", language), parent)
        self.set_theme(theme)
        self.bridge = QtConnectionControlService(
            context.services.resolve(ConnectionControlService), self
        )
        self.bridge.status_changed.connect(self._connection_changed)
        self._connection_changed(self.bridge.status(channel))

    def _connection_changed(self, status):
        if status.channel != self.channel:
            return
        self.set_connection_phase(status.phase)
        self.setToolTip(status.detail)

    def set_language(self, language):
        self.set_label(t(f"status.{self.channel}", language))

    def prepare_shutdown(self):
        self.bridge.close()
