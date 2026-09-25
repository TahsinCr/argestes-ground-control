from __future__ import annotations

from src.core.mavlink import MavlinkClient
from src.extension.api import ExtensionContext, ExtensionRegistrar, TelemetrySource
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from .connection_control import VehicleConnectionControlService
from src.services.vehicle.telemetry import MockTelemetryService, TelemetryDecoder, VehicleTelemetryService

from .wiring import VehicleStack, _create_vehicle_stack, _mavlink_client, _parameters, _vehicle


def _connection(settings: SettingsRepository, vehicle: VehicleService,
                telemetry: TelemetryService, logging: LoggingService) -> ConnectionControlService:
    return VehicleConnectionControlService(settings=settings, vehicle=vehicle,
                                           telemetry=telemetry, logger=logging.child("CONNECTION"))


class MavlinkTelemetrySource:
    def __init__(self, vehicle: VehicleService) -> None:
        self.vehicle = vehicle

    def create_telemetry(self, settings: SettingsService, logging: LoggingService) -> TelemetryService:
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
        registrar.singleton(VehicleStack, _create_vehicle_stack,
                            dependencies={"settings_service": SettingsService, "logging": LoggingService})
        if not context.services.resolve(SettingsService).current.backend.mock_mode:
            registrar.singleton(MavlinkClient, _mavlink_client, dependencies={"stack": VehicleStack})
        registrar.singleton(VehicleService, _vehicle, dependencies={"stack": VehicleStack})
        registrar.singleton(ParameterService, _parameters, dependencies={"stack": VehicleStack})
        registrar.singleton(TelemetrySource, _source, dependencies={"vehicle": VehicleService})
        registrar.singleton(ConnectionControlService, _connection,
                            dependencies={"settings": SettingsRepository, "vehicle": VehicleService,
                                          "telemetry": TelemetryService, "logging": LoggingService})


def create_extension() -> MavlinkExtension:
    return MavlinkExtension()
