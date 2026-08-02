from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from src.core.dependency import BaseDependencyContainer
from src.services.application_dependencies import register_application_dependencies
from src.services.infrastructure_dependencies import (
    ApplicationContext,
    register_infrastructure_dependencies,
)
from src.services.vehicle.dependencies import (
    VehicleStack,
    register_vehicle_dependencies,
)


class DependencyPriority(IntEnum):
    INFRASTRUCTURE = 0
    VEHICLE = 10
    APPLICATION = 20
    RUNTIME = 30


class ApplicationContainer(BaseDependencyContainer):
    """Root container coordinating the YKI dependency modules."""

    def __init__(self, base_dir: Path, **kwargs) -> None:
        self.context = ApplicationContext(base_dir.resolve())
        super().__init__(**kwargs)

    def configure(self) -> None:
        register_infrastructure_dependencies(
            self,
            self.context,
            priority=int(DependencyPriority.INFRASTRUCTURE),
        )
        register_vehicle_dependencies(
            self,
            priority=int(DependencyPriority.VEHICLE),
        )
        register_application_dependencies(
            self,
            priority=int(DependencyPriority.APPLICATION),
            runtime_priority=int(DependencyPriority.RUNTIME),
        )


def create_container(base_dir: Path, *, warmup: bool = False) -> ApplicationContainer:
    container = ApplicationContainer(base_dir)
    if warmup:
        container.warmup()
    return container


__all__ = [
    "ApplicationContainer",
    "ApplicationContext",
    "DependencyPriority",
    "VehicleStack",
    "create_container",
]
