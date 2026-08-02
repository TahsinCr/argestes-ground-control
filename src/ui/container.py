from __future__ import annotations

from pathlib import Path

from src.core.dependency import BaseDependencyContainer, DependencyContainer
from src.ui.dependencies import UiContext, register_ui_dependencies
from src.ui.themes.style import ThemePalette


class UiContainer(BaseDependencyContainer):
    """UI-only child container backed by the backend application container."""

    def __init__(
        self,
        backend: DependencyContainer,
        *,
        base_dir: Path,
        theme: ThemePalette | None = None,
    ) -> None:
        self.context = UiContext(base_dir.resolve(), theme)
        super().__init__(parent=backend, set_as_default=False)

    def configure(self) -> None:
        register_ui_dependencies(self, self.context, priority=40)


__all__ = ["UiContainer", "UiContext"]
