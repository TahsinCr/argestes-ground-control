from __future__ import annotations

from src.core.dependency import BaseDependencyContainer, DependencyContainer
from src.models.mission import MissionState
from src.services.commands import DispatchCommandService
from src.services.battery_alarm import BatteryAlarmService
from src.services.competition import TeknofestCompetitionService
from src.services.connections import BackendConnectionControlService
from src.services.abstracts.archive import MissionArchiveRepository
from src.services.abstracts.commands import CommandService
from src.services.abstracts.competition import CompetitionService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.orin import OrinService
from src.services.abstracts.onboard import OnboardMissionServiceContract
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.tile_cache import TileCacheService
from src.services.abstracts.vehicle import VehicleService
from src.services.abstracts.video import VideoService
from src.services.facade import BackendFacade
from src.services.logging_service import LoggingService
from src.services.onboard import OnboardMissionService
from src.services.infrastructure_dependencies import (
    ApplicationContext,
    OrinCommandCatalog,
)
from src.services.mission_archive import MissionArchiveService
from src.services.planner import PlannerService
from src.services.runtime import RuntimeServices
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer
from src.services.vehicle.dependencies import VehicleStack
from src.services.video import GStreamerVideoService, MutableVideoService


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


def _commands(
    stack: VehicleStack,
    catalog: OrinCommandCatalog,
    logging: LoggingService,
    onboard: OnboardMissionServiceContract,
) -> CommandService:
    return DispatchCommandService(
        stack.vehicle,
        stack.parameters,
        logging.child("COMMAND"),
        orin=stack.orin,
        onboard=onboard,
        configured_orin_tasks=catalog.codes,
    )


def _onboard(
    orin: OrinService,
    competition: CompetitionService,
    logging: LoggingService,
) -> OnboardMissionService:
    return OnboardMissionService(
        orin,
        competition,
        logging.child("ONBOARD"),
    )


def _onboard_contract(service: OnboardMissionService) -> OnboardMissionServiceContract:
    return service


def _video(
    settings_service: SettingsService,
    logging: LoggingService,
) -> VideoService:
    backend = settings_service.current.backend
    if backend.mock_mode or backend.video_source_type == "mock":
        return MutableVideoService(logging.child("VIDEO"))
    return GStreamerVideoService(
        backend.video_source_type,
        backend.video_source_uri,
        logging.child("VIDEO"),
        latency_ms=backend.video_latency_ms,
        codec=backend.video_codec,
        frame_timeout_s=backend.video_frame_timeout_s,
    )


def _tile_cache(settings_service: SettingsService) -> TileCacheServer:
    cache = settings_service.current.backend.cache
    return TileCacheServer(
        cache_path=cache.path,
        enabled=cache.enabled,
        limit_mb=cache.limit_mb,
    )


def _tile_cache_contract(service: TileCacheServer) -> TileCacheService:
    return service


def _archive(context: ApplicationContext) -> MissionArchiveService:
    return MissionArchiveService(context.base_dir)


def _archive_contract(service: MissionArchiveService) -> MissionArchiveRepository:
    return service


def _competition(
    settings_service: SettingsService,
    telemetry: TelemetryService,
    planner: PlannerService,
    vehicle: VehicleService,
    logging: LoggingService,
) -> CompetitionService:
    return TeknofestCompetitionService(
        settings_service.current.backend.competition,
        telemetry,
        planner,
        vehicle,
        logging.child("TEKNOFEST"),
    )


def _connections(
    settings: SettingsRepository,
    vehicle: VehicleService,
    telemetry: TelemetryService,
    video: VideoService,
    competition: CompetitionService,
    orin: OrinService,
    logging: LoggingService,
) -> ConnectionControlService:
    return BackendConnectionControlService(
        settings=settings,
        vehicle=vehicle,
        telemetry=telemetry,
        video=video,
        competition=competition,
        orin=orin,
        logger=logging.child("CONNECTION"),
    )


def _battery_alarm(
    commands: CommandService,
    logging: LoggingService,
) -> BatteryAlarmService:
    def request_failsafe(mode: str):
        return commands.issue_command(
            "APPLY_MODE",
            {"mode": mode, "battery_failsafe": True},
            source="battery-failsafe",
        )

    return BatteryAlarmService(logging.child("BATTERY"), request_failsafe)


def _facade(
    telemetry: TelemetryService,
    competition: CompetitionService,
    planner: PlannerService,
    orin: OrinService,
    commands: CommandService,
    parameters: ParameterService,
    settings: SettingsRepository,
) -> BackendFacade:
    return BackendFacade(
        telemetry=telemetry,
        competition=competition,
        planner=planner,
        orin=orin,
        commands=commands,
        parameters=parameters,
        settings=settings,
    )


def register_application_dependencies(
    container: BaseDependencyContainer,
    *,
    priority: int,
    runtime_priority: int,
) -> None:
    container.singleton(
        PlannerService,
        factory=_planner,
        dependencies={
            "stack": VehicleStack,
            "settings_service": SettingsService,
            "logging": LoggingService,
        },
        priority=priority,
    )
    container.singleton(
        OnboardMissionService,
        factory=_onboard,
        dependencies={
            "orin": OrinService,
            "competition": CompetitionService,
            "logging": LoggingService,
        },
        priority=priority,
    )
    container.singleton(
        OnboardMissionServiceContract,
        factory=_onboard_contract,
        dependencies={"service": OnboardMissionService},
        priority=priority,
    )
    container.singleton(
        CommandService,
        factory=_commands,
        dependencies={
            "stack": VehicleStack,
            "catalog": OrinCommandCatalog,
            "logging": LoggingService,
            "onboard": OnboardMissionServiceContract,
        },
        priority=priority,
    )
    container.singleton(
        VideoService,
        factory=_video,
        dependencies={
            "settings_service": SettingsService,
            "logging": LoggingService,
        },
        priority=priority,
    )
    container.singleton(
        TileCacheServer,
        factory=_tile_cache,
        dependencies={"settings_service": SettingsService},
        priority=priority,
    )
    container.singleton(
        TileCacheService,
        factory=_tile_cache_contract,
        dependencies={"service": TileCacheServer},
        priority=priority,
    )
    container.singleton(
        MissionArchiveService,
        factory=_archive,
        dependencies={"context": ApplicationContext},
        priority=priority,
    )
    container.singleton(
        MissionArchiveRepository,
        factory=_archive_contract,
        dependencies={"service": MissionArchiveService},
        priority=priority,
    )
    container.singleton(
        CompetitionService,
        factory=_competition,
        dependencies={
            "settings_service": SettingsService,
            "telemetry": TelemetryService,
            "planner": PlannerService,
            "vehicle": VehicleService,
            "logging": LoggingService,
        },
        priority=priority,
    )
    container.singleton(
        ConnectionControlService,
        factory=_connections,
        dependencies={
            "settings": SettingsRepository,
            "vehicle": VehicleService,
            "telemetry": TelemetryService,
            "video": VideoService,
            "competition": CompetitionService,
            "orin": OrinService,
            "logging": LoggingService,
        },
        priority=priority,
    )
    container.singleton(
        BatteryAlarmService,
        factory=_battery_alarm,
        dependencies={
            "commands": CommandService,
            "logging": LoggingService,
        },
        priority=priority,
    )
    container.singleton(
        BackendFacade,
        factory=_facade,
        dependencies={
            "telemetry": TelemetryService,
            "competition": CompetitionService,
            "planner": PlannerService,
            "orin": OrinService,
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
            "log_service": LoggingService,
            "settings_service": SettingsService,
            "vehicle_service": VehicleService,
            "telemetry_service": TelemetryService,
            "planner_service": PlannerService,
            "orin_service": OrinService,
            "onboard_service": OnboardMissionServiceContract,
            "command_service": CommandService,
            "parameter_service": ParameterService,
            "video_service": VideoService,
            "tile_cache_service": TileCacheServer,
            "competition_service": CompetitionService,
            "connection_service": ConnectionControlService,
            "archive_service": MissionArchiveService,
            "facade": BackendFacade,
        },
        priority=runtime_priority,
    )


__all__ = [
    "register_application_dependencies",
]
