from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.dependency import BaseDependencyContainer
from src.services.battery_alarm import BatteryAlarmService
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.video import VideoService
from src.services.abstracts.onboard import OnboardMissionServiceContract
from src.services.logging_service import LoggingService
from src.services.mission_archive import MissionArchiveService
from src.services.planner import PlannerService
from src.services.runtime import RuntimeServices
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer
from src.services.target_selection import TargetSelectionService
from src.ui.adapters.runtime import QtRuntimeServices
from src.ui.adapters.services import (
    QtCommandService,
    QtConnectionControlService,
    QtLoggingService,
    QtOnboardMissionService,
    QtTargetSelectionService,
    QtParameterService,
    QtPlannerService,
    QtSettingsService,
    QtTelemetryService,
    QtTileCacheService,
    QtVideoService,
)
from src.ui.themes.style import ThemePalette
from src.ui.views.main_window import MainWindow


@dataclass(frozen=True, slots=True)
class UiContext:
    base_dir: Path
    theme: ThemePalette | None = None


def _video_adapter(
    backend: VideoService,
    settings: SettingsService,
) -> QtVideoService:
    backend_settings = settings.current.backend
    return QtVideoService(
        backend,
        mock_mode=(
            backend_settings.mock_mode
            or backend_settings.video_source_type == "mock"
        ),
    )


def _runtime(
    backend: RuntimeServices,
    log_service: QtLoggingService,
    settings_service: QtSettingsService,
    telemetry_service: QtTelemetryService,
    planner_service: QtPlannerService,
    command_service: QtCommandService,
    parameter_service: QtParameterService,
    video_service: QtVideoService,
    tile_cache_service: QtTileCacheService,
    connection_service: QtConnectionControlService,
    target_selection_service: QtTargetSelectionService,
    onboard_service: QtOnboardMissionService,
) -> QtRuntimeServices:
    runtime = QtRuntimeServices(
        backend=backend,
        log_service=log_service,
        settings_service=settings_service,
        telemetry_service=telemetry_service,
        planner_service=planner_service,
        command_service=command_service,
        parameter_service=parameter_service,
        video_service=video_service,
        tile_cache_service=tile_cache_service,
        connection_service=connection_service,
        target_selection_service=target_selection_service,
        onboard_service=onboard_service,
    )
    telemetry_service.telemetry_updated.connect(video_service.consume_snapshot)
    return runtime


def _main_window(
    context: UiContext,
    log_service: QtLoggingService,
    settings_service: QtSettingsService,
    telemetry_service: QtTelemetryService,
    planner_service: QtPlannerService,
    command_service: QtCommandService,
    parameter_service: QtParameterService,
    video_service: QtVideoService,
    connection_service: QtConnectionControlService,
    tile_cache_service: QtTileCacheService,
    archive_service: MissionArchiveService,
    battery_alarm_service: BatteryAlarmService,
    target_selection_service: QtTargetSelectionService,
    onboard_service: QtOnboardMissionService,
) -> MainWindow:
    if context.theme is None:
        raise RuntimeError("A UI theme is required to construct MainWindow")
    return MainWindow(
        log_service=log_service,
        settings_service=settings_service,
        telemetry_service=telemetry_service,
        planner_service=planner_service,
        command_service=command_service,
        parameter_service=parameter_service,
        video_service=video_service,
        connection_service=connection_service,
        tile_cache_service=tile_cache_service,
        archive_service=archive_service,
        battery_alarm_service=battery_alarm_service,
        target_selection_service=target_selection_service,
        onboard_service=onboard_service,
        base_dir=context.base_dir,
        theme=context.theme,
    )


def register_ui_dependencies(
    container: BaseDependencyContainer,
    context: UiContext,
    *,
    priority: int,
) -> None:
    container.instance(UiContext, context, priority=priority)
    adapters = (
        (QtLoggingService, LoggingService),
        (QtSettingsService, SettingsService),
        (QtTelemetryService, TelemetryService),
        (QtPlannerService, PlannerService),
        (QtCommandService, CommandService),
        (QtParameterService, ParameterService),
        (QtTileCacheService, TileCacheServer),
        (QtConnectionControlService, ConnectionControlService),
        (QtTargetSelectionService, TargetSelectionService),
        (QtOnboardMissionService, OnboardMissionServiceContract),
    )
    for adapter, backend in adapters:
        container.singleton(
            adapter,
            concrete=adapter,
            dependencies={"backend": backend},
            priority=priority,
        )
    container.singleton(
        QtVideoService,
        factory=_video_adapter,
        dependencies={"backend": VideoService, "settings": SettingsService},
        priority=priority,
    )
    container.singleton(
        QtRuntimeServices,
        factory=_runtime,
        dependencies={
            "backend": RuntimeServices,
            "log_service": QtLoggingService,
            "settings_service": QtSettingsService,
            "telemetry_service": QtTelemetryService,
            "planner_service": QtPlannerService,
            "command_service": QtCommandService,
            "parameter_service": QtParameterService,
            "video_service": QtVideoService,
            "tile_cache_service": QtTileCacheService,
            "connection_service": QtConnectionControlService,
            "target_selection_service": QtTargetSelectionService,
            "onboard_service": QtOnboardMissionService,
        },
        priority=priority,
    )
    if context.theme is not None:
        container.singleton(
            MainWindow,
            factory=_main_window,
            dependencies={
                "context": UiContext,
                "log_service": QtLoggingService,
                "settings_service": QtSettingsService,
                "telemetry_service": QtTelemetryService,
                "planner_service": QtPlannerService,
                "command_service": QtCommandService,
                "parameter_service": QtParameterService,
                "video_service": QtVideoService,
                "connection_service": QtConnectionControlService,
                "tile_cache_service": QtTileCacheService,
                "archive_service": MissionArchiveService,
                "battery_alarm_service": BatteryAlarmService,
                "target_selection_service": QtTargetSelectionService,
                "onboard_service": QtOnboardMissionService,
            },
            priority=priority,
        )


__all__ = ["UiContext", "register_ui_dependencies"]
