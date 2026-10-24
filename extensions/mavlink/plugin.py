from __future__ import annotations

from src.core.mavlink import MavlinkClient
from src.extension.api import ExtensionContext, ExtensionRegistrar, TelemetrySource
from src.extension.api.contracts import ConnectionProfiles, ConnectionSource
from src.extension.api.protocol import ActiveProtocol
from src.extension.api.vehicle import RcControl, MissionControl
from src.services.abstracts.commands import CommandService
from .commands import DispatchCommandService
from src.services.abstracts.connections import ConnectionControlService
from src.extension.api.parameters import ParameterService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from .connection_control import VehicleConnectionControlService
from .runtime import MavlinkTelemetryRuntime

from .wiring import VehicleStack, _create_vehicle_stack, _mavlink_client, _vehicle


def _connection(
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


class MavlinkTelemetrySource:
    def __init__(self, vehicle: VehicleService) -> None:
        self.vehicle = vehicle

    def create_telemetry(
        self, settings: SettingsService, logging: LoggingService
    ) -> TelemetryService:
        from .telemetry import TelemetryDecoder, VehicleTelemetryService

        return VehicleTelemetryService(
            self.vehicle,
            logging.child("TELEMETRY"),
            decoder=TelemetryDecoder(),
        )


def _source(vehicle: VehicleService) -> TelemetrySource:
    return MavlinkTelemetrySource(vehicle)


class MavlinkExtension:
    def configure(
        self, registrar: ExtensionRegistrar, context: ExtensionContext
    ) -> None:
        from dataclasses import asdict
        from src.models.settings import SettingsModel

        def project(settings):
            return {"mavlink": asdict(settings.backend.mavlink)}

        def restore(settings, config):
            payload = settings.to_dict()
            payload["backend"].update(
                {
                    key: config[key]
                    for key in (
                        "mavlink",
                        "mavlink_connections",
                        "active_mavlink_connection_id",
                    )
                    if key in config
                }
            )
            return SettingsModel.from_dict(payload)

        context.services.resolve(SettingsService).bind_extension_settings(
            "mavlink", project, restore
        )

        def connection_project(settings):
            return {
                "profiles": [
                    item.to_dict() for item in settings.backend.mavlink_connections
                ],
                "active_profile": settings.backend.active_mavlink_connection_id,
            }

        def connection_restore(settings, config):
            payload = settings.to_dict()
            if "profiles" in config:
                payload["backend"]["mavlink_connections"] = config["profiles"]
                payload["backend"]["active_mavlink_connection_id"] = config.get(
                    "active_profile", ""
                )
            return SettingsModel.from_dict(payload)

        context.services.resolve(SettingsService).bind_extension_settings(
            "vehicle_connection", connection_project, connection_restore
        )
        registrar.singleton(ActiveProtocol, lambda: ActiveProtocol("mavlink"))
        registrar.settings_page(lambda parent: self._settings_page(context, parent))
        registrar.status_widget(
            "autopilot",
            lambda parent, theme, language: self._status(
                context, parent, theme, language, "autopilot"
            ),
            order=300,
        )
        registrar.singleton(
            VehicleStack,
            _create_vehicle_stack,
            dependencies={
                "settings_service": SettingsService,
                "logging": LoggingService,
            },
        )
        registrar.singleton(
            MavlinkClient, _mavlink_client, dependencies={"stack": VehicleStack}
        )
        registrar.singleton(
            VehicleService, _vehicle, dependencies={"stack": VehicleStack}
        )
        registrar.singleton(
            RcControl, lambda vehicle: vehicle, dependencies={"vehicle": VehicleService}
        )
        registrar.singleton(
            MissionControl,
            lambda vehicle: vehicle,
            dependencies={"vehicle": VehicleService},
        )

        def parameters():
            return (
                context.services.resolve(ParameterService)
                if context.services.container.has(ParameterService)
                else None
            )

        registrar.singleton(
            CommandService,
            lambda vehicle, logging: DispatchCommandService(
                vehicle, parameters, logging.child("COMMAND")
            ),
            dependencies={"vehicle": VehicleService, "logging": LoggingService},
        )
        registrar.singleton(
            TelemetrySource, _source, dependencies={"vehicle": VehicleService}
        )
        self._context = context
        registrar.singleton(ConnectionSource, lambda: MavlinkConnectionSource(context))
        registrar.singleton(
            ConnectionProfiles,
            lambda source: source,
            dependencies={"source": ConnectionSource},
        )
        registrar.singleton(
            MavlinkTelemetryRuntime,
            MavlinkTelemetryRuntime,
            dependencies={
                "source": TelemetrySource,
                "telemetry": TelemetryService,
                "settings": SettingsService,
                "logging": LoggingService,
            },
        )

    def start(self, context):
        context.services.resolve(MavlinkTelemetryRuntime).start()

    def stop(self):
        self._context.services.resolve(MavlinkTelemetryRuntime).stop()

    @staticmethod
    def _status(context, parent, theme, language, channel):
        from .ui.status import ProtocolStatusChip

        return ProtocolStatusChip(context, parent, theme, language, channel)

    @staticmethod
    def _settings_page(context, anchor):
        from src.ui.adapters.services import QtSettingsService
        from .ui.settings_dialog import MavlinkSettingsDialog
        from src.ui.themes.palette import palette_for

        settings = context.services.resolve(SettingsService)
        current = settings.current
        dialog = MavlinkSettingsDialog(
            current, current.ui.language, palette_for(current.ui.theme), anchor
        )
        from src.ui.widgets.settings_page import embed_dialog

        dialog._current_settings = lambda: settings.current
        adapter = QtSettingsService(settings, dialog)
        dialog.prepare_shutdown = adapter.close
        return embed_dialog(
            dialog, commit=lambda: adapter.save_async(dialog.to_settings())
        )


def create_extension() -> MavlinkExtension:
    return MavlinkExtension()


class MavlinkConnectionSource:
    def __init__(self, context):
        self.context = context

    def create_connection(self):
        services = self.context.services
        return _connection(
            services.resolve(SettingsRepository),
            services.resolve(VehicleService),
            services.resolve(TelemetryService),
            services.resolve(LoggingService),
        )

    @property
    def profiles(self):
        from src.models.connections import ConnectionProfile

        return tuple(
            ConnectionProfile(**item.to_dict())
            for item in self.context.services.resolve(
                SettingsService
            ).current.backend.mavlink_connections
        )

    @property
    def active_identifier(self):
        return self.context.services.resolve(
            SettingsService
        ).current.backend.active_mavlink_connection_id

    @property
    def default_profile(self):
        from src.models.connections import ConnectionProfile

        settings = self.context.services.resolve(
            SettingsService
        ).current.backend.mavlink
        return ConnectionProfile(
            "",
            "",
            settings.endpoint,
            settings.baud,
            settings.heartbeat_timeout,
            settings.auto_connect,
        )

    def settings_payload(self, profiles, active_identifier):
        from src.models.settings import SettingsModel

        payload = self.context.services.resolve(SettingsService).current.to_dict()
        payload["backend"]["mavlink_connections"] = [
            item.to_dict() for item in profiles
        ]
        payload["backend"]["active_mavlink_connection_id"] = active_identifier or ""
        return SettingsModel.from_dict(payload)
