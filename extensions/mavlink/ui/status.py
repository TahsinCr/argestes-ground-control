"""Protocol-owned GPS and autopilot health; indicators never open popups."""

from src.services.abstracts.telemetry import TelemetryService
from src.ui.adapters.services import QtTelemetryService
from src.ui.i18n import t
from src.ui.widgets.common import StatusChip


class ProtocolStatusChip(StatusChip):
    def __init__(self, context, parent, theme, language, channel):
        self.channel = channel
        super().__init__(t(f"status.{channel}", language), parent)
        self.set_theme(theme)
        self.bridge = QtTelemetryService(
            context.services.resolve(TelemetryService), self
        )
        self.bridge.telemetry_updated.connect(self._snapshot)
        self._snapshot(self.bridge.current)

    def _snapshot(self, snapshot):
        if self.channel == "autopilot":
            self.set_state("success" if snapshot.autopilot_link else "danger")
        else:
            self.set_state(
                "danger"
                if not snapshot.autopilot_link
                else "warning"
                if snapshot.gps_fix in {"", "NO_GPS", "NO_FIX", "NO GPS", "NO FIX"}
                else "success"
            )

    def set_language(self, language):
        self.set_label(t(f"status.{self.channel}", language))

    def prepare_shutdown(self):
        self.bridge.close()
