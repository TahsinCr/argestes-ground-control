from __future__ import annotations

from dataclasses import dataclass

from src.core.dependency import BaseDependencyContainer
from src.core.mavlink import MavlinkClient, MavlinkEndpoint
from src.services.contracts.orin import OrinService
from src.services.contracts.parameters import ParameterService
from src.services.contracts.telemetry import TelemetryService
from src.services.contracts.vehicle import VehicleService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from src.services.vehicle import (
    MavlinkOrinService,
    MavlinkVehicleService,
    NullOrinService,
    NullVehicleService,
)
from src.services.vehicle.parameters import (
    MutableParameterService,
    VehicleParameterService,
)
from src.services.vehicle.parameter_defaults import MavlinkParameterDefaultSource
from src.services.vehicle.telemetry import (
    MockTelemetryService,
    TelemetryDecoder,
    VehicleTelemetryService,
)


@dataclass(frozen=True, slots=True)
class VehicleStack:
    """Vehicle services sharing the application's single MAVLink client."""

    client: MavlinkClient | None
    vehicle: VehicleService
    telemetry: TelemetryService
    parameters: ParameterService
    orin: OrinService


def _create_vehicle_stack(
    settings_service: SettingsService,
    logging: LoggingService,
) -> VehicleStack:
    settings = settings_service.current
    if settings.backend.mock_mode:
        return VehicleStack(
            client=None,
            vehicle=NullVehicleService(),
            telemetry=MockTelemetryService(logging.child("TELEMETRY")),
            parameters=MutableParameterService(),
            orin=NullOrinService(),
        )

    mavlink = settings.backend.mavlink
    endpoint = MavlinkEndpoint(
        uri=mavlink.endpoint,
        baud=mavlink.baud,
        heartbeat_timeout=float(mavlink.heartbeat_timeout),
        dialect=mavlink.dialect,
        source_system=mavlink.source_system,
        source_component=mavlink.source_component,
        autoreconnect=mavlink.autoreconnect,
    )
    client = MavlinkClient(endpoint)
    vehicle = MavlinkVehicleService(
        client,
        logging.child("VEHICLE"),
        operation_timeout=float(mavlink.operation_timeout),
        default_mission_altitude=float(settings.backend.mission.default_altitude_m),
    )
    parameter_defaults = MavlinkParameterDefaultSource(
        client,
        timeout=min(5.0, max(1.0, float(mavlink.operation_timeout))),
    )
    return VehicleStack(
        client=client,
        vehicle=vehicle,
        telemetry=VehicleTelemetryService(
            vehicle,
            logging.child("TELEMETRY"),
            decoder=TelemetryDecoder(settings.backend.battery_telemetry),
            settings_service=settings_service,
        ),
        parameters=VehicleParameterService(
            vehicle,
            logging.child("PARAMETER"),
            write_timeout=float(mavlink.operation_timeout),
            refresh_on_connect=mavlink.sync_parameters_on_connect,
            default_loader=parameter_defaults.load,
        ),
        orin=MavlinkOrinService(
            client,
            vehicle,
            settings.backend.orin,
            logging.child("ORIN"),
        ),
    )


def _mavlink_client(stack: VehicleStack) -> MavlinkClient:
    if stack.client is None:
        raise RuntimeError("MAVLink client is unavailable while mock mode is enabled")
    return stack.client


def _vehicle(stack: VehicleStack) -> VehicleService:
    return stack.vehicle


def _telemetry(stack: VehicleStack) -> TelemetryService:
    return stack.telemetry


def _parameters(stack: VehicleStack) -> ParameterService:
    return stack.parameters


def _orin(stack: VehicleStack) -> OrinService:
    return stack.orin


def register_vehicle_dependencies(
    container: BaseDependencyContainer,
    *,
    priority: int,
) -> None:
    container.singleton(
        VehicleStack,
        factory=_create_vehicle_stack,
        dependencies={
            "settings_service": SettingsService,
            "logging": LoggingService,
        },
        priority=priority,
    )
    if not container.resolve(SettingsService).current.backend.mock_mode:
        container.singleton(
            MavlinkClient,
            factory=_mavlink_client,
            dependencies={"stack": VehicleStack},
            priority=priority,
        )
    for token, factory in (
        (VehicleService, _vehicle),
        (TelemetryService, _telemetry),
        (ParameterService, _parameters),
        (OrinService, _orin),
    ):
        container.singleton(
            token,
            factory=factory,
            dependencies={"stack": VehicleStack},
            priority=priority,
        )


__all__ = ["VehicleStack", "register_vehicle_dependencies"]
