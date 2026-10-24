"""Map-owned GPS quality indicator for the shared application status bar."""

from src.extension.api import ConnectionPhase
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.telemetry import TelemetryService
from src.services.settings import SettingsService
from src.ui.adapters.services import QtConnectionControlService, QtTelemetryService
from src.ui.i18n import t
from src.ui.widgets.common import StatusChip

from ..gps import gps_quality_state
from .settings import MapSettings


class GpsStatusChip(StatusChip):
    def __init__(self, context, parent, theme, language):
        self.context = context
        self.language = language
        self._phase = ConnectionPhase.DISCONNECTED
        self._gps_ready = False
        self._snapshot = None
        super().__init__(t("status.gps", language), parent)
        self.set_theme(theme)
        self.settings = MapSettings(
            context.services.resolve(SettingsService), self
        )
        self.settings.settings_changed.connect(self._settings_changed)
        self.telemetry = QtTelemetryService(
            context.services.resolve(TelemetryService), self
        )
        self.telemetry.telemetry_updated.connect(self._telemetry_changed)
        self.connections = None
        if context.services.container.has(ConnectionControlService):
            self.connections = QtConnectionControlService(
                context.services.resolve(ConnectionControlService), self
            )
            self.connections.status_changed.connect(self._connection_changed)
            self._connection_changed(self.connections.status("gps"))
        self._telemetry_changed(self.telemetry.current)

    def _telemetry_changed(self, snapshot):
        self._snapshot = snapshot
        if self.connections is not None:
            self._gps_ready = self.connections.status("gps").connected
        else:
            self._gps_ready = bool(
                snapshot.has_valid_position
                and snapshot.gps_satellites is not None
                and snapshot.gps_satellites > 0
            )
        self._render()

    def _connection_changed(self, status):
        if status.channel != "gps":
            return
        self._phase = status.phase
        self._gps_ready = status.connected
        self._render()

    def _settings_changed(self, _settings):
        self._render()

    def _render(self):
        snapshot = self._snapshot
        satellites = snapshot.gps_satellites if snapshot is not None else None
        good_min, fair_min = self.settings.gps_satellite_thresholds
        quality = (
            gps_quality_state(satellites, good_min=good_min, fair_min=fair_min)
            if self._gps_ready
            else "danger"
        )
        no_satellites = satellites is None or int(satellites) <= 0

        if no_satellites or not self._gps_ready:
            self.set_state("danger")
            message = t("status.gps.quality.poor", self.language)
        elif self._phase in {
            ConnectionPhase.CONNECTING,
            ConnectionPhase.DISCONNECTING,
        }:
            self.set_connection_phase(self._phase)
            message = t(
                "status.gps.connecting"
                if self._phase == ConnectionPhase.CONNECTING
                else "status.gps.disconnecting",
                self.language,
            )
        elif quality == "success":
            self.set_state("success")
            message = t("status.gps.quality.good", self.language)
        elif quality == "warning":
            self.set_state("warning")
            message = t("status.gps.quality.fair", self.language)
        else:
            self.set_state("danger")
            message = t("status.gps.quality.poor", self.language)

        gps_label = t("status.gps", self.language)
        if satellites is None:
            count = t("status.gps.satellites.unknown", self.language)
        else:
            count = f"{max(0, int(satellites))} {t('status.gps.satellites', self.language)}"
        self.set_label(f"{gps_label} {count}")
        thresholds = t(
            "status.gps.thresholds",
            self.language,
            good=good_min,
            fair=fair_min,
        )
        self.setToolTip(f"{message}\n{thresholds}")

    def set_language(self, language):
        self.language = language
        self._render()

    def prepare_shutdown(self):
        self.telemetry.close()
        self.settings.close()
        if self.connections is not None:
            self.connections.close()
