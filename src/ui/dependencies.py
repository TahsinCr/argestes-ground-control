from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from src.extension.host import ExtensionHost
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from src.services.runtime import RuntimeServices
from src.ui.adapters.services import QtLoggingService, QtSettingsService
from src.ui.adapters.runtime import QtRuntimeServices
from src.ui.themes.style import ThemePalette
from src.ui.views.main_window import MainWindow


@dataclass(frozen=True, slots=True)
class UiContext:
    base_dir: Path
    theme: ThemePalette | None = None


def register_ui_dependencies(container, context, *, priority):
    container.instance(UiContext, context, priority=priority)
    for adapter, backend in (
        (QtLoggingService, LoggingService),
        (QtSettingsService, SettingsService),
    ):
        container.singleton(
            adapter,
            concrete=adapter,
            dependencies={"backend": backend},
            priority=priority,
        )
    container.singleton(
        QtRuntimeServices,
        concrete=QtRuntimeServices,
        dependencies={
            "backend": RuntimeServices,
            "log_service": QtLoggingService,
            "settings_service": QtSettingsService,
        },
        priority=priority,
    )
    if context.theme is not None:
        container.singleton(
            MainWindow,
            factory=lambda log_service, settings_service, extension_host: MainWindow(
                log_service=log_service,
                settings_service=settings_service,
                extension_host=extension_host,
                base_dir=context.base_dir,
                theme=context.theme,
            ),
            dependencies={
                "log_service": QtLoggingService,
                "settings_service": QtSettingsService,
                "extension_host": ExtensionHost,
            },
            priority=priority,
        )
