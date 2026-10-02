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
        registrar.settings_page(lambda parent: self._settings_page(context, parent))

    @staticmethod
    def _settings_page(context, parent):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLabel, QVBoxLayout
        from src.services.settings import SettingsService
        from src.ui.themes.palette import palette_for
        from .ui.connection_editor import ConnectionEditor

        current = context.services.resolve(SettingsService).current
        if not context.services.container.has(ConnectionProfiles):
            return QLabel("Etkin protokol bağlantı profillerini desteklemiyor.", parent)
        editor = ConnectionEditor(
            context, parent, palette_for(current.ui.theme), current.ui.language
        )
        editor.profiles_panel.setWindowFlags(Qt.WindowType.Widget)
        editor.profiles_panel.back_button.hide()
        layout = QVBoxLayout(editor)
        layout.addWidget(editor.profiles_panel)
        editor.profiles_panel.show()
        return editor

    @staticmethod
    def _status(context, parent, theme, language, **options):
        from .ui.status import ConnectionStatusWidget

        return ConnectionStatusWidget(context, parent, theme, language, **options)

    def start(self, context):
        context.services.resolve(ConnectionRuntime).start()

    def stop(self):
        self._context.services.resolve(ConnectionRuntime).stop()


def create_extension():
    return VehicleConnectionExtension()
