from __future__ import annotations

from src.extension.api import ExtensionContext, ExtensionRegistrar, TelemetrySource
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from .runtime import TelemetryRuntime


class TelemetryExtension:
    def configure(self, registrar: ExtensionRegistrar, context: ExtensionContext) -> None:
        self._context = context
        registrar.singleton(TelemetryRuntime, TelemetryRuntime,
                            dependencies={"source": TelemetrySource, "telemetry": TelemetryService,
                                          "settings": SettingsService, "logging": LoggingService})
        registrar.status_widget("connection", lambda parent, theme, language:
                                self._status(context, parent, theme, language))
        registrar.status_widget("quality", lambda parent, theme, language:
                                self._status(context, parent, theme, language, metrics=True),
                                placement="metrics")

    @staticmethod
    def _status(context, parent, theme, language, **options):
        from .ui import TelemetryStatusWidget
        return TelemetryStatusWidget(context, parent, theme, language, **options)

    def start(self, context: ExtensionContext) -> None:
        context.services.resolve(TelemetryRuntime).start()

    def stop(self) -> None:
        self._context.services.resolve(TelemetryRuntime).stop()


def create_extension() -> TelemetryExtension:
    return TelemetryExtension()
