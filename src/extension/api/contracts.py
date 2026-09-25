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


class ExtensionRegistrar:
    """Collect registrations before applying them; reject duplicate ownership."""

    def __init__(self) -> None:
        self.bindings: list[ServiceBinding] = []
        self.sidebar_views: list[SidebarContribution] = []

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


class TelemetrySource(Protocol):
    """Protocol adapter supplying the common telemetry service."""

    def create_telemetry(self, settings: Any, logging: Any) -> Any: ...
