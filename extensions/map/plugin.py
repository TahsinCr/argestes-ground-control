"""Map uses normalized telemetry and planner operations, never wire packets."""
from src.services.settings import SettingsService
from src.services.planner import PlannerService
from src.services.tile_cache import TileCacheServer
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.commands import CommandService
from src.services.logging_service import LoggingService


class MapExtension:
    def configure(self, registrar, context):
        registrar.settings_ui(lambda anchor: self.settings(context, anchor))
        registrar.visual_view("view", lambda parent, theme, language:
                              self.create(context, parent, theme, language), primary=True, legacy_key="map")

    @staticmethod
    def create(context, parent, theme, language):
        from .ui.panel import MapPanel
        from .ui.settings import MapSettings
        from src.ui.adapters.services import (QtPlannerService, QtSettingsService,
                                              QtTileCacheService, QtTelemetryService, QtCommandService)
        panel = MapPanel("", "", theme=theme, language=language, base_dir=context.base_dir,
                         planner_service=QtPlannerService(context.services.resolve(PlannerService)),
                         settings_service=MapSettings(context.services.resolve(SettingsService)),
                         tile_cache_service=QtTileCacheService(context.services.resolve(TileCacheServer)),
                         parent=parent)
        telemetry = QtTelemetryService(context.services.resolve(TelemetryService), panel)
        commands = QtCommandService(context.services.resolve(CommandService), panel)
        panel.command_requested.connect(commands.issue_command)
        telemetry.telemetry_updated.connect(panel.update_snapshot)
        panel.update_snapshot(telemetry.current)
        logger = context.services.resolve(LoggingService).child("MAP")
        panel.info_generated.connect(lambda severity, message: logger.emit(severity, message))
        panel.set_language = panel.retranslate
        panel._settings_service.settings_changed.connect(lambda _: panel._send_ui_config())
        adapters = (panel._planner_service, panel._settings_service, panel._tile_cache,
                    telemetry, commands)
        def close():
            panel.shutdown()
            for adapter in adapters:
                adapter.close()
        panel.prepare_shutdown = close
        return panel

    @staticmethod
    def settings(context, anchor):
        from .ui.settings import configure
        configure(context.services.resolve(SettingsService), anchor)


def create_extension():
    return MapExtension()
