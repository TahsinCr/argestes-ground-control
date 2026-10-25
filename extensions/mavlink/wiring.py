from __future__ import annotations

from dataclasses import dataclass

from src.core.mavlink import MavlinkClient, MavlinkEndpoint
from src.services.abstracts.vehicle import VehicleService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from .vehicle import MavlinkVehicleService


@dataclass(frozen=True, slots=True)
class VehicleStack:
    """Vehicle services sharing the application's single MAVLink client."""

    client: MavlinkClient
    vehicle: VehicleService


def _create_vehicle_stack(
    settings_service: SettingsService,
    logging: LoggingService,
) -> VehicleStack:
    settings = settings_service.current
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
    client = MavlinkClient(
        endpoint,
        router_options={"state_capacity": 512, "source_capacity": 64},
    )
    vehicle = MavlinkVehicleService(
        client,
        logging.child("VEHICLE"),
        operation_timeout=float(mavlink.operation_timeout),
        default_mission_altitude=float(settings.backend.mission.default_altitude_m),
        synchronize_mission_on_connect=mavlink.sync_mission_on_connect,
    )
    return VehicleStack(client=client, vehicle=vehicle)


def _mavlink_client(stack: VehicleStack) -> MavlinkClient:
    return stack.client


def _vehicle(stack: VehicleStack) -> VehicleService:
    return stack.vehicle
