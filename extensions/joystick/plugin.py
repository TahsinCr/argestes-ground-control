from src.extension.api import ExtensionRegistrar
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
            lambda settings, logging: LinuxRcJoystickService(
                settings, controller(context), logging.child("JOYSTICK")
            ),
            dependencies={"settings": SettingsService, "logging": LoggingService},
        )
        registrar.settings_ui(self.settings)

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
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()


def create_extension():
    return JoystickExtension()
