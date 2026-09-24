from __future__ import annotations

from enum import IntEnum
from dataclasses import dataclass
from pathlib import Path

from src.core.dependency import BaseDependencyContainer, DependencyContainer
from src.models.mission import MissionState
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.logging import LogService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.tile_cache import TileCacheService
from src.services.abstracts.vehicle import VehicleService
from src.services.async_runtime import AsyncRuntime
from src.services.battery_alarm import BatteryAlarmService
from src.services.commands import DispatchCommandService
from src.services.facade import BackendFacade
from src.services.logging_service import LoggingService
from src.services.planner import PlannerService
from src.services.runtime import RuntimeServices
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer
from src.services.vehicle.connection_control import VehicleConnectionControlService
from src.services.vehicle.dependencies import (
    VehicleStack,
    register_vehicle_dependencies,
)


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    """Immutable context supplied to the application's composition root."""

    base_dir: Path


class DependencyPriority(IntEnum):
    INFRASTRUCTURE = 0
    VEHICLE = 10
    APPLICATION = 20
    RUNTIME = 30


# All dependency wiring intentionally lives in this module.  Services expose
# behavior and contracts only; they do not know which concrete implementation
# the desktop application chooses.
def _logging(context: ApplicationContext) -> LoggingService:
    return LoggingService(log_path=context.base_dir / "data" / "logs" / "yki.jsonl")


def _log_contract(service: LoggingService) -> LogService:
    return service


def _settings(context: ApplicationContext, logging: LoggingService) -> SettingsService:
    return SettingsService(context.base_dir, logging.child("SETTINGS"))


def _settings_contract(service: SettingsService) -> SettingsRepository:
    return service


def _planner(
    stack: VehicleStack,
    settings_service: SettingsService,
    logging: LoggingService,
) -> PlannerService:
    settings = settings_service.current
    return PlannerService(
        logging.child("PLANNER"),
        initial_state=None if settings.backend.mock_mode else MissionState(),
        default_radii=settings.backend.mission.radii,
        vehicle=stack.vehicle if stack.client is not None else None,
        sync_from_vehicle_on_connect=settings.backend.mavlink.sync_mission_on_connect,
    )


def _commands(stack: VehicleStack, logging: LoggingService) -> CommandService:
    return DispatchCommandService(stack.vehicle, stack.parameters, logging.child("COMMAND"))


def _tile_cache(
    settings_service: SettingsService,
    async_runtime: AsyncRuntime,
) -> TileCacheServer:
    cache = settings_service.current.backend.cache
    return TileCacheServer(
        cache_path=cache.path,
        enabled=cache.enabled,
        limit_mb=cache.limit_mb,
        async_runtime=async_runtime,
    )


def _tile_cache_contract(service: TileCacheServer) -> TileCacheService:
    return service


def _battery_alarm(commands: CommandService, logging: LoggingService) -> BatteryAlarmService:
    return BatteryAlarmService(
        logging.child("BATTERY"),
        lambda mode: commands.issue_command(
            "APPLY_MODE",
            {"mode": mode, "battery_failsafe": True},
            source="battery-failsafe",
        ),
    )


def _connection_control(
    settings: SettingsRepository,
    vehicle: VehicleService,
    telemetry: TelemetryService,
    logging: LoggingService,
) -> ConnectionControlService:
    return VehicleConnectionControlService(
        settings=settings,
        vehicle=vehicle,
        telemetry=telemetry,
        logger=logging.child("CONNECTION"),
    )


def _facade(
    telemetry: TelemetryService,
    planner: PlannerService,
    commands: CommandService,
    parameters: ParameterService,
    settings: SettingsRepository,
) -> BackendFacade:
    return BackendFacade(
        telemetry=telemetry,
        planner=planner,
        commands=commands,
        parameters=parameters,
        settings=settings,
    )


def _register_infrastructure(
    container: BaseDependencyContainer,
    context: ApplicationContext,
) -> None:
    priority = int(DependencyPriority.INFRASTRUCTURE)
    container.instance(ApplicationContext, context, priority=priority)
    container.instance(DependencyContainer, container.container, priority=priority)
    container.singleton(AsyncRuntime, concrete=AsyncRuntime, priority=priority)
    container.singleton(
        LoggingService,
        factory=_logging,
        dependencies={"context": ApplicationContext},
        priority=priority,
    )
    container.singleton(
        LogService,
        factory=_log_contract,
        dependencies={"service": LoggingService},
        priority=priority,
    )
    container.singleton(
        SettingsService,
        factory=_settings,
        dependencies={"context": ApplicationContext, "logging": LoggingService},
        priority=priority,
    )
    container.singleton(
        SettingsRepository,
        factory=_settings_contract,
        dependencies={"service": SettingsService},
        priority=priority,
    )


def _register_application(container: BaseDependencyContainer) -> None:
    priority = int(DependencyPriority.APPLICATION)
    runtime_priority = int(DependencyPriority.RUNTIME)
    container.singleton(
        PlannerService,
        factory=_planner,
        dependencies={"stack": VehicleStack, "settings_service": SettingsService, "logging": LoggingService},
        priority=priority,
    )
    container.singleton(
        CommandService,
        factory=_commands,
        dependencies={"stack": VehicleStack, "logging": LoggingService},
        priority=priority,
    )
    container.singleton(
        TileCacheServer,
        factory=_tile_cache,
        dependencies={"settings_service": SettingsService, "async_runtime": AsyncRuntime},
        priority=priority,
    )
    container.singleton(
        TileCacheService,
        factory=_tile_cache_contract,
        dependencies={"service": TileCacheServer},
        priority=priority,
    )
    container.singleton(
        ConnectionControlService,
        factory=_connection_control,
        dependencies={
            "settings": SettingsRepository,
            "vehicle": VehicleService,
            "telemetry": TelemetryService,
            "logging": LoggingService,
        },
        priority=priority,
    )
    container.singleton(
        BatteryAlarmService,
        factory=_battery_alarm,
        dependencies={"commands": CommandService, "logging": LoggingService},
        priority=priority,
    )
    container.singleton(
        BackendFacade,
        factory=_facade,
        dependencies={
            "telemetry": TelemetryService,
            "planner": PlannerService,
            "commands": CommandService,
            "parameters": ParameterService,
            "settings": SettingsRepository,
        },
        priority=runtime_priority,
    )
    container.singleton(
        RuntimeServices,
        concrete=RuntimeServices,
        dependencies={
            "container": DependencyContainer,
            "async_runtime": AsyncRuntime,
            "log_service": LoggingService,
            "settings_service": SettingsService,
            "vehicle_service": VehicleService,
            "telemetry_service": TelemetryService,
            "planner_service": PlannerService,
            "command_service": CommandService,
            "parameter_service": ParameterService,
            "tile_cache_service": TileCacheServer,
            "connection_service": ConnectionControlService,
            "facade": BackendFacade,
        },
        priority=runtime_priority,
    )


class ApplicationContainer(BaseDependencyContainer):
    """Root container coordinating the YKI dependency modules."""

    def __init__(self, base_dir: Path, **kwargs) -> None:
        self.context = ApplicationContext(base_dir.resolve())
        super().__init__(**kwargs)

    def configure(self) -> None:
        _register_infrastructure(self, self.context)
        register_vehicle_dependencies(
            self,
            priority=int(DependencyPriority.VEHICLE),
        )
        _register_application(self)


def create_container(base_dir: Path, *, warmup: bool = False) -> ApplicationContainer:
    container = ApplicationContainer(base_dir)
    if warmup:
        container.warmup()
    return container


__all__ = [
    "ApplicationContainer",
    "ApplicationContext",
    "DependencyPriority",
    "VehicleStack",
    "create_container",
]
