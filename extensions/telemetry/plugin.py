from __future__ import annotations

from src.extension.api import ExtensionContext, ExtensionRegistrar, TelemetrySource
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService


def _telemetry(source: TelemetrySource, settings: SettingsService,
               logging: LoggingService) -> TelemetryService:
    return source.create_telemetry(settings, logging)


class TelemetryExtension:
    def configure(self, registrar: ExtensionRegistrar, context: ExtensionContext) -> None:
        registrar.singleton(TelemetryService, _telemetry,
                            dependencies={"source": TelemetrySource, "settings": SettingsService,
                                          "logging": LoggingService})


def create_extension() -> TelemetryExtension:
    return TelemetryExtension()
