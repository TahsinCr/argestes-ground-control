class QuickCommandsExtension:
    def configure(self, registrar, context):
        registrar.visual_view(
            "view",
            lambda parent, theme, language: self.create(
                context, parent, theme, language
            ),
            region="left",
            title="Hızlı Komutlar",
            legacy_key="commands",
            order=300,
        )

    @staticmethod
    def create(context, parent, theme, language):
        from .ui.panels import VehicleControlsPanel
        from src.ui.adapters.services import QtTelemetryService, QtCommandService
        from src.services.abstracts.telemetry import TelemetryService
        from src.services.abstracts.commands import CommandService

        commands = QtCommandService(context.services.resolve(CommandService), parent)
        panel = VehicleControlsPanel(commands.available_flight_modes, language, parent)
        feed = QtTelemetryService(context.services.resolve(TelemetryService), panel)
        commands.setParent(panel)
        panel.command_requested.connect(commands.issue_command)
        commands.command_dispatched.connect(panel.command_started)
        commands.command_completed.connect(panel.command_completed)

        def update(snapshot):
            panel.set_flight_modes(commands.available_flight_modes)
            panel.update_snapshot(snapshot)

        feed.telemetry_updated.connect(update)
        update(feed.current)
        panel.prepare_shutdown = lambda: (feed.close(), commands.close())
        return panel


def create_extension():
    return QuickCommandsExtension()
