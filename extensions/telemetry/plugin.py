from __future__ import annotations

from src.extension.api import ExtensionContext, ExtensionRegistrar, TelemetrySource
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from src.services.vehicle.telemetry_base import SwitchableTelemetryService


def _telemetry(source: TelemetrySource, settings: SettingsService,
               logging: LoggingService) -> TelemetryService:
    return source.create_telemetry(settings, logging)


class TelemetryExtension:
    def configure(self, registrar: ExtensionRegistrar, context: ExtensionContext) -> None:
        self._context = context

    def start(self, context: ExtensionContext) -> None:
        proxy = context.services.resolve(TelemetryService)
        if not isinstance(proxy, SwitchableTelemetryService):
            raise TypeError("Telemetry service must support extension switching")
        proxy.attach(_telemetry(context.services.resolve(TelemetrySource),
                                context.services.resolve(SettingsService),
                                context.services.resolve(LoggingService)))

    def stop(self) -> None:
        proxy = self._context.services.resolve(TelemetryService)
        proxy.attach(None)


def create_extension() -> TelemetryExtension:
    return TelemetryExtension()
