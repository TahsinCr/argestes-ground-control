from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.dependency import BaseDependencyContainer
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.tile_cache import TileCacheService
from src.services.abstracts.logging import LogService
from src.services.abstracts.settings import SettingsRepository
from src.services.logging_service import LoggingService
from src.services.planner import PlannerService
from src.services.runtime import RuntimeServices
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer
from src.ui.adapters.runtime import QtRuntimeServices
from src.ui.adapters.services import (
    QtCommandService,
    QtConnectionControlService,
    QtLoggingService,
    QtParameterService,
    QtPlannerService,
    QtSettingsService,
    QtTelemetryService,
    QtTileCacheService,
)
from src.ui.themes.style import ThemePalette
from src.ui.views.main_window import MainWindow


@dataclass(frozen=True, slots=True)
class UiContext:
    base_dir: Path
    theme: ThemePalette | None = None


def _runtime(
    backend: RuntimeServices,
    log_service: QtLoggingService,
    settings_service: QtSettingsService,
    telemetry_service: QtTelemetryService,
    planner_service: QtPlannerService,
    command_service: QtCommandService,
    parameter_service: QtParameterService,
    tile_cache_service: QtTileCacheService,
    connection_service: QtConnectionControlService,
) -> QtRuntimeServices:
    return QtRuntimeServices(
        backend=backend,
        log_service=log_service,
        settings_service=settings_service,
        telemetry_service=telemetry_service,
        planner_service=planner_service,
        command_service=command_service,
        parameter_service=parameter_service,
        tile_cache_service=tile_cache_service,
        connection_service=connection_service,
    )


def _main_window(
    context: UiContext,
    log_service: QtLoggingService,
    settings_service: QtSettingsService,
    telemetry_service: QtTelemetryService,
    planner_service: QtPlannerService,
    command_service: QtCommandService,
    parameter_service: QtParameterService,
    connection_service: QtConnectionControlService,
    tile_cache_service: QtTileCacheService,
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
        connection_service=connection_service,
        tile_cache_service=tile_cache_service,
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
    )
    for adapter, backend in adapters:
        container.singleton(
            adapter,
            concrete=adapter,
            dependencies={"backend": backend},
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
            "tile_cache_service": QtTileCacheService,
            "connection_service": QtConnectionControlService,
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
                "connection_service": QtConnectionControlService,
                "tile_cache_service": QtTileCacheService,
            },
            priority=priority,
        )


__all__ = ["UiContext", "register_ui_dependencies"]
