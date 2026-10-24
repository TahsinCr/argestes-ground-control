"""Map uses normalized telemetry and planner operations, never wire packets."""

from src.services.settings import SettingsService
from extensions.map_autonomy.services.planner import PlannerService
from extensions.map_autonomy.services.tile_cache import TileCacheServer
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.commands import CommandService
from src.services.logging_service import LoggingService


class MapExtension:
    def configure(self, registrar, context):
        from src.extension.api.vehicle import MissionControl
        from src.services.async_runtime import AsyncRuntime
        from dataclasses import asdict
        from src.models.settings import SettingsModel

        self.context = context
        settings = context.services.resolve(SettingsService)

        def project(model):
            return {
                "cache": asdict(model.backend.cache),
                "mission": asdict(model.backend.mission),
            }

        def restore(model, config):
            payload = model.to_dict()
            for key in ("cache", "mission"):
                if key in config:
                    payload["backend"][key] = config[key]
            return SettingsModel.from_dict(payload)

        settings.bind_extension_settings("map_autonomy", project, restore)
        registrar.singleton(
            PlannerService,
            lambda vehicle, settings, logging: PlannerService(
                logging.child("PLANNER"),
                vehicle=vehicle,
                default_radii=settings.current.backend.mission.radii,
                sync_from_vehicle_on_connect=vehicle.synchronize_mission_on_connect,
            ),
            dependencies={
                "vehicle": MissionControl,
                "settings": SettingsService,
                "logging": LoggingService,
            },
        )

        def cache(settings, runtime):
            config = settings.current.backend.cache
            return TileCacheServer(
                cache_path=config.path or context.base_dir / "data" / "map-cache",
                enabled=config.enabled,
                limit_mb=config.limit_mb,
                async_runtime=runtime,
            )

        registrar.singleton(
            TileCacheServer,
            cache,
            dependencies={"settings": SettingsService, "runtime": AsyncRuntime},
        )
        registrar.settings_page(lambda anchor: self.settings(context, anchor))
        registrar.status_widget(
            "gps",
            lambda parent, theme, language: self._gps_status(
                context, parent, theme, language
            ),
            order=300,
        )
        registrar.visual_view(
            "view",
            lambda parent, theme, language: self.create(
                context, parent, theme, language
            ),
            primary=True,
        )

    def start(self, context):
        settings = context.services.resolve(SettingsService)
        self._planner = context.services.resolve(PlannerService)
        self._cache = context.services.resolve(TileCacheServer)
        self._configuration = None
        self._subscription = settings.changed.subscribe(self._apply_settings)
        self._apply_settings(settings.current)
        self._cache.start()
        self._planner.start()

    def _apply_settings(self, settings):
        self._planner.set_default_radii(settings.backend.mission.radii)
        config = settings.backend.cache
        signature = (config.path, config.enabled, config.limit_mb)
        if signature != self._configuration:
            self._configuration = signature
            self._cache.configure(
                cache_path=config.path or self.context.base_dir / "data" / "map-cache",
                enabled=config.enabled,
                limit_mb=config.limit_mb,
            )
            if config.enabled:
                self._cache.start()

    def stop(self):
        self._subscription.cancel()
        self._planner.stop()
        self._cache.stop()

    @staticmethod
    def create(context, parent, theme, language):
        from .ui.panel import MapPanel
        from .ui.settings import MapSettings
        from .ui.adapters import QtPlannerService, QtTileCacheService
        from src.ui.adapters.services import QtTelemetryService, QtCommandService

        panel = MapPanel(
            "",
            "",
            theme=theme,
            language=language,
            base_dir=context.base_dir,
            planner_service=QtPlannerService(context.services.resolve(PlannerService)),
            settings_service=MapSettings(
                context.services.resolve(SettingsService),
                cache=context.services.resolve(TileCacheServer),
            ),
            tile_cache_service=QtTileCacheService(
                context.services.resolve(TileCacheServer)
            ),
            parent=parent,
        )
        telemetry = QtTelemetryService(
            context.services.resolve(TelemetryService), panel
        )
        commands = QtCommandService(context.services.resolve(CommandService), panel)
        panel.command_requested.connect(commands.issue_command)
        telemetry.telemetry_updated.connect(panel.update_snapshot)
        panel.update_snapshot(telemetry.current)
        logger = context.services.resolve(LoggingService).child("MAP")
        panel.info_generated.connect(
            lambda severity, message: logger.emit(severity, message)
        )
        panel.set_language = panel.retranslate
        panel._settings_service.settings_changed.connect(
            lambda _: panel._send_ui_config()
        )
        adapters = (
            panel._planner_service,
            panel._settings_service,
            panel._tile_cache,
            telemetry,
            commands,
        )

        def close():
            panel.shutdown()
            for adapter in adapters:
                adapter.close()

        panel.prepare_shutdown = close
        return panel

    @staticmethod
    def settings(context, anchor):
        from .ui.settings import configure

        return configure(
            context.services.resolve(SettingsService),
            anchor,
            cache=context.services.resolve(TileCacheServer),
        )

    @staticmethod
    def _gps_status(context, parent, theme, language):
        from .ui.status import GpsStatusChip

        return GpsStatusChip(context, parent, theme, language)


def create_extension():
    return MapExtension()
