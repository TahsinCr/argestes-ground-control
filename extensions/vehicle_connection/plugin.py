from src.extension.api import ExtensionContext, ExtensionRegistrar
from src.extension.api.contracts import (
    ConnectionSource,
    ConnectionBinding,
    ConnectionProfiles,
)
from .runtime import ConnectionRuntime


class VehicleConnectionExtension:
    def configure(self, registrar: ExtensionRegistrar, context: ExtensionContext):
        self._context = context
        registrar.singleton(
            ConnectionRuntime,
            ConnectionRuntime,
            dependencies={"source": ConnectionSource, "binding": ConnectionBinding},
        )
        registrar.status_widget(
            "connection",
            lambda parent, theme, language: self._status(
                context, parent, theme, language
            ),
        )
        registrar.status_widget(
            "quality",
            lambda parent, theme, language: self._status(
                context, parent, theme, language, metrics=True
            ),
            placement="metrics",
        )
        registrar.settings_ui(lambda anchor: self._open_settings(context, anchor))

    @staticmethod
    def _status(context, parent, theme, language, **options):
        from .ui.status import ConnectionStatusWidget

        return ConnectionStatusWidget(context, parent, theme, language, **options)

    @staticmethod
    def _open_settings(context, anchor):
        from src.services.settings import SettingsService
        from src.ui.themes.palette import palette_for

        settings = context.services.resolve(SettingsService).current
        if not context.services.container.has(ConnectionProfiles):
            from PyQt6.QtWidgets import QMessageBox
            from src.ui.i18n import t

            QMessageBox.information(
                anchor,
                t("extension.vehicle_connection.name", settings.ui.language),
                t("connection.profiles.unsupported", settings.ui.language),
            )
            return
        from .ui.connection_editor import ConnectionEditor

        editor = ConnectionEditor(
            context, anchor, palette_for(settings.ui.theme), settings.ui.language
        )
        try:
            editor.open(anchor)
        finally:
            editor.prepare_shutdown()
            editor.deleteLater()

    def start(self, context):
        context.services.resolve(ConnectionRuntime).start()

    def stop(self):
        self._context.services.resolve(ConnectionRuntime).stop()


def create_extension():
    return VehicleConnectionExtension()
