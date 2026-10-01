from __future__ import annotations

from src.core.mavlink import MavlinkClient
from src.extension.api import ExtensionContext, ExtensionRegistrar, TelemetrySource
from src.extension.api.contracts import ConnectionProfiles, ConnectionSource
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from .connection_control import VehicleConnectionControlService
from .runtime import MavlinkTelemetryRuntime

from .wiring import VehicleStack, _create_vehicle_stack, _mavlink_client, _parameters, _vehicle


def _connection(settings: SettingsRepository, vehicle: VehicleService,
                telemetry: TelemetryService, logging: LoggingService) -> ConnectionControlService:
    return VehicleConnectionControlService(settings=settings, vehicle=vehicle,
                                           telemetry=telemetry, logger=logging.child("CONNECTION"))


class MavlinkTelemetrySource:
    def __init__(self, vehicle: VehicleService) -> None:
        self.vehicle = vehicle

    def create_telemetry(self, settings: SettingsService, logging: LoggingService) -> TelemetryService:
        from src.services.vehicle.telemetry_base import MockTelemetryService
        from .telemetry import TelemetryDecoder, VehicleTelemetryService

        if settings.current.backend.mock_mode:
            return MockTelemetryService(logging.child("TELEMETRY"))
        return VehicleTelemetryService(
            self.vehicle, logging.child("TELEMETRY"),
            decoder=TelemetryDecoder(settings.current.backend.battery_telemetry),
            settings_service=settings,
        )


def _source(vehicle: VehicleService) -> TelemetrySource:
    return MavlinkTelemetrySource(vehicle)


class MavlinkExtension:
    def configure(self, registrar: ExtensionRegistrar, context: ExtensionContext) -> None:
        registrar.settings_ui(lambda anchor: self._open_settings(context, anchor))
        for channel in ("autopilot", "gps"):
            registrar.status_widget(channel, lambda parent, theme, language, selected=channel:
                                    self._status(context, parent, theme, language, selected))
        registrar.singleton(VehicleStack, _create_vehicle_stack,
                            dependencies={"settings_service": SettingsService, "logging": LoggingService})
        if not context.services.resolve(SettingsService).current.backend.mock_mode:
            registrar.singleton(MavlinkClient, _mavlink_client, dependencies={"stack": VehicleStack})
        registrar.singleton(VehicleService, _vehicle, dependencies={"stack": VehicleStack})
        registrar.singleton(ParameterService, _parameters, dependencies={"stack": VehicleStack})
        registrar.singleton(TelemetrySource, _source, dependencies={"vehicle": VehicleService})
        self._context = context
        registrar.singleton(ConnectionSource, lambda: MavlinkConnectionSource(context))
        registrar.singleton(ConnectionProfiles, lambda source: source,
                            dependencies={"source": ConnectionSource})
        registrar.singleton(MavlinkTelemetryRuntime, MavlinkTelemetryRuntime,
                            dependencies={"source": TelemetrySource, "telemetry": TelemetryService,
                                          "settings": SettingsService, "logging": LoggingService})

    def start(self, context):
        context.services.resolve(MavlinkTelemetryRuntime).start()

    def stop(self):
        self._context.services.resolve(MavlinkTelemetryRuntime).stop()

    @staticmethod
    def _status(context, parent, theme, language, channel):
        from .ui.status import ProtocolStatusChip
        return ProtocolStatusChip(context, parent, theme, language, channel)


    @staticmethod
    def _open_settings(context, anchor):
        from PyQt6.QtWidgets import QDialog
        from src.ui.adapters.services import QtSettingsService
        from .ui.settings_dialog import MavlinkSettingsDialog
        from src.ui.themes.palette import palette_for
        settings = context.services.resolve(SettingsService)
        current = settings.current
        dialog = MavlinkSettingsDialog(current, current.ui.language, palette_for(current.ui.theme), anchor)
        adapter = QtSettingsService(settings, dialog)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                adapter.save_async(dialog.to_settings())
        finally:
            adapter.close()
            dialog.deleteLater()


def create_extension() -> MavlinkExtension:
    return MavlinkExtension()


class MavlinkConnectionSource:
    def __init__(self, context):
        self.context = context

    def create_connection(self):
        services = self.context.services
        return _connection(services.resolve(SettingsRepository), services.resolve(VehicleService),
                           services.resolve(TelemetryService), services.resolve(LoggingService))

    @property
    def profiles(self):
        from src.models.connections import ConnectionProfile
        return tuple(ConnectionProfile(**item.to_dict()) for item in self.context.services.resolve(SettingsService).current.backend.mavlink_connections)

    @property
    def active_identifier(self):
        return self.context.services.resolve(SettingsService).current.backend.active_mavlink_connection_id

    @property
    def default_profile(self):
        from src.models.connections import ConnectionProfile
        settings = self.context.services.resolve(SettingsService).current.backend.mavlink
        return ConnectionProfile("", "", settings.endpoint, settings.baud, settings.heartbeat_timeout, settings.auto_connect)

    def settings_payload(self, profiles, active_identifier):
        from src.models.settings import SettingsModel
        payload = self.context.services.resolve(SettingsService).current.to_dict()
        payload["backend"]["mavlink_connections"] = [item.to_dict() for item in profiles]
        if active_identifier is not None:
            payload["backend"]["active_mavlink_connection_id"] = active_identifier
        return SettingsModel.from_dict(payload)
