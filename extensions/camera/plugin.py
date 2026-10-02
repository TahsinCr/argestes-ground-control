"""Complete camera package: inputs, wall, recording and live outputs."""

from __future__ import annotations

from src.extension.api import ExtensionContext, ExtensionRegistrar
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService

from .runtime import CameraRuntime


class CameraExtension:
    def __init__(self) -> None:
        self._workspace = None
        self._runtime: CameraRuntime | None = None

    def configure(
        self, registrar: ExtensionRegistrar, context: ExtensionContext
    ) -> None:
        registrar.singleton(
            CameraRuntime,
            lambda logging, settings: CameraRuntime(
                context.base_dir, logging, settings
            ),
            dependencies={"logging": LoggingService, "settings": SettingsService},
        )
        registrar.visual_view(
            "wall",
            lambda parent, theme, language: self._create_workspace(
                context, parent, theme, language
            ),
        )
        registrar.status_widget(
            "connections",
            lambda parent, theme, language: self._create_status(
                parent, theme, language
            ),
        )
        registrar.settings_page(self._settings_page)

    def _settings_page(self, parent):
        from .ui.settings import CameraSettingsPage

        return CameraSettingsPage(self._workspace, parent)

    def _create_workspace(self, context: ExtensionContext, parent, theme, language):
        from .ui.workspace import CameraWorkspace

        self._workspace = CameraWorkspace(
            context.services.resolve(CameraRuntime), theme, language, parent
        )
        return self._workspace

    def _create_status(self, parent, theme, language):
        from .ui.workspace import CameraStatusWidget

        if self._workspace is None:
            raise RuntimeError("Camera workspace must exist before status widgets")
        return CameraStatusWidget(self._workspace, theme, language, parent)

    def start(self, context: ExtensionContext) -> None:
        self._runtime = context.services.resolve(CameraRuntime)
        self._runtime.start()

    def stop(self) -> None:
        if self._runtime is not None:
            self._runtime.stop()
            self._runtime = None


def create_extension() -> CameraExtension:
    return CameraExtension()
