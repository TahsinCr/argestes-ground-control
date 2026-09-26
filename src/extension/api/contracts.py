from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Callable
from typing import Protocol

from src.core.dependency import BaseDependencyContainer

API_MAJOR = 1


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    identifier: str
    name: str
    description: str
    category: str
    version: str
    entry_point: str
    api_major: int = API_MAJOR
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    default_enabled: bool = False
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtensionContext:
    base_dir: Path
    settings: dict[str, Any]
    services: BaseDependencyContainer


@dataclass(frozen=True, slots=True)
class ServiceBinding:
    token: Any
    factory: Callable[..., Any]
    dependencies: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SidebarContribution:
    identifier: str
    title: str
    factory: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class VisualContribution:
    identifier: str
    factory: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class StatusContribution:
    identifier: str
    factory: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class SettingsContribution:
    action: Callable[[Any], None]


class ExtensionRegistrar:
    """Collect registrations before applying them; reject duplicate ownership."""

    def __init__(self) -> None:
        self.bindings: list[ServiceBinding] = []
        self.sidebar_views: list[SidebarContribution] = []
        self.visual_views: list[VisualContribution] = []
        self.status_widgets: list[StatusContribution] = []
        self.settings_action: SettingsContribution | None = None

    def singleton(
        self,
        token: Any,
        factory: Callable[..., Any],
        *,
        dependencies: dict[str, Any] | None = None,
    ) -> None:
        if any(binding.token == token for binding in self.bindings):
            raise ValueError(f"Duplicate extension service: {token}")
        self.bindings.append(ServiceBinding(token, factory, dict(dependencies or {})))

    def sidebar_view(self, identifier: str, title: str,
                     factory: Callable[[Any], Any]) -> None:
        """Contribute a lazily created left-sidebar QWidget without importing Qt here."""
        if not re.fullmatch(r"[a-z][a-z0-9_]*", identifier):
            raise ValueError(f"Invalid sidebar identifier: {identifier}")
        if any(view.identifier == identifier for view in self.sidebar_views):
            raise ValueError(f"Duplicate sidebar view: {identifier}")
        self.sidebar_views.append(SidebarContribution(identifier, title, factory))

    def visual_view(self, identifier: str, factory: Callable[[Any], Any]) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", identifier):
            raise ValueError(f"Invalid visual identifier: {identifier}")
        if any(view.identifier == identifier for view in self.visual_views):
            raise ValueError(f"Duplicate visual view: {identifier}")
        self.visual_views.append(VisualContribution(identifier, factory))

    def status_widget(self, identifier: str, factory: Callable[[Any], Any]) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", identifier):
            raise ValueError(f"Invalid status identifier: {identifier}")
        if any(widget.identifier == identifier for widget in self.status_widgets):
            raise ValueError(f"Duplicate status widget: {identifier}")
        self.status_widgets.append(StatusContribution(identifier, factory))

    def settings_ui(self, action: Callable[[Any], None]) -> None:
        """Register one extension-owned settings action; its argument is the UI anchor."""
        if self.settings_action is not None:
            raise ValueError("Duplicate extension settings action")
        self.settings_action = SettingsContribution(action)


class TelemetrySource(Protocol):
    """Protocol adapter supplying the common telemetry service."""

    def create_telemetry(self, settings: Any, logging: Any) -> Any: ...
