from src.extension.api import ExtensionRegistrar
from src.services.abstracts.commands import CommandService
from src.services.abstracts.vehicle import VehicleService
from src.services.settings import SettingsService
from src.services.logging_service import LoggingService
from .contracts import JoystickService
from .runtime import LinuxRcJoystickService
from .protocol import controller


class JoystickExtension:
    def configure(self, registrar: ExtensionRegistrar, context):
        self.context = context
        registrar.singleton(
            JoystickService,
            lambda settings, vehicle, commands, logging: LinuxRcJoystickService(
                settings,
                vehicle,
                controller(context),
                commands,
                logging.child("JOYSTICK"),
            ),
            dependencies={
                "settings": SettingsService,
                "vehicle": VehicleService,
                "commands": CommandService,
                "logging": LoggingService,
            },
        )
        registrar.settings_page(self.settings)

    def start(self, context):
        context.services.resolve(JoystickService).start()

    def stop(self):
        self.context.services.resolve(JoystickService).stop()

    def settings(self, anchor):
        from .ui.dialog import JoystickDialog
        from src.ui.themes.palette import palette_for

        settings = self.context.services.resolve(SettingsService)
        current = settings.current
        dialog = JoystickDialog(
            settings,
            self.context.services.resolve(JoystickService),
            current.ui.language,
            palette_for(current.ui.theme),
            anchor,
        )
        from src.ui.widgets.settings_page import embed_dialog

        dialog.prepare_shutdown = dialog._live_timer.stop
        return embed_dialog(dialog)


def create_extension():
    return JoystickExtension()
