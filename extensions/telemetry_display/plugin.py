class TelemetryDisplayExtension:
    def configure(self, registrar, context):
        self.context = context
        registrar.visual_view(
            "view",
            lambda parent, theme, language: self.create(
                context, parent, theme, language
            ),
            region="left",
            title="Araç Telemetrisi",
            legacy_key="telemetry",
            order=200,
            preferred_height=280,
        )
        registrar.settings_page(lambda anchor: self.settings(context, anchor))

    @staticmethod
    def create(context, parent, theme, language):
        from src.services.abstracts.telemetry import TelemetryService
        from src.services.settings import SettingsService
        from src.ui.adapters.services import QtTelemetryService

        from .ui.panel import TelemetryPanel

        panel = TelemetryPanel("", "", theme, parent)
        settings = context.services.resolve(SettingsService)
        feed = QtTelemetryService(context.services.resolve(TelemetryService), panel)
        feed.telemetry_updated.connect(panel.update_snapshot)
        panel.update_snapshot(feed.current)

        def configure(_=None):
            panel.configure_layout(settings.extension_config("telemetry_display"))

        subscription = settings.extensions_changed.subscribe(
            lambda key: feed._post(configure) if key == "telemetry_display" else None
        )
        configure()
        panel.prepare_shutdown = lambda: (subscription.cancel(), feed.close())
        return panel

    @staticmethod
    def settings(context, anchor):
        from .ui.settings import TelemetrySettingsPage

        return TelemetrySettingsPage(context, anchor)


def create_extension():
    return TelemetryDisplayExtension()
