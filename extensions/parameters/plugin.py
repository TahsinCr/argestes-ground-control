class ParametersExtension:
    def configure(self, registrar, context):
        from src.extension.api.parameters import ParameterService
        from src.services.logging_service import LoggingService
        from src.services.settings import SettingsService
        from src.services.abstracts.vehicle import VehicleService
        from src.core.mavlink import MavlinkClient
        from src.extension.api.protocol import ActiveProtocol
        from .protocols.mavlink import VehicleParameterService
        from .protocols.mavlink_defaults import MavlinkParameterDefaultSource

        def create(settings, logging, vehicle, client, protocol):
            def mavlink():
                config = settings.current.backend.mavlink
                defaults = MavlinkParameterDefaultSource(
                    client, timeout=min(5.0, max(1.0, float(config.operation_timeout)))
                )
                return VehicleParameterService(
                    vehicle,
                    logging.child("PARAMETER"),
                    write_timeout=float(config.operation_timeout),
                    refresh_on_connect=config.sync_parameters_on_connect,
                    default_loader=defaults.load,
                )

            return protocol.adapter({"mavlink": mavlink})

        registrar.singleton(
            ParameterService,
            create,
            dependencies={
                "settings": SettingsService,
                "logging": LoggingService,
                "vehicle": VehicleService,
                "client": MavlinkClient,
                "protocol": ActiveProtocol,
            },
        )
        self.context = context
        registrar.visual_view(
            "view",
            lambda parent, theme, language: self.create(
                context, parent, theme, language
            ),
            region="bottom",
            title="Parametreler",
            legacy_key="parameters",
            initial_visible=False,
        )

    def start(self, context):
        from src.extension.api.parameters import ParameterService

        context.services.resolve(ParameterService).start()

    def stop(self):
        from src.extension.api.parameters import ParameterService

        self.context.services.resolve(ParameterService).stop()

    @staticmethod
    def create(context, parent, theme, language):
        from .ui.panel import ParametersPanel
        from .ui.adapter import QtParameterService
        from src.extension.api.parameters import ParameterService

        panel = ParametersPanel("", "", "", parent)
        adapter = QtParameterService(context.services.resolve(ParameterService), panel)
        adapter.parameters_changed.connect(panel.set_parameters)
        panel.apply_requested.connect(adapter.update_values)
        adapter.write_finished.connect(panel.finish_apply)
        panel.reset_requested.connect(adapter.refresh)
        panel.set_parameters(adapter.current)
        panel.set_language(language)
        panel.prepare_shutdown = adapter.close
        return panel


def create_extension():
    return ParametersExtension()
