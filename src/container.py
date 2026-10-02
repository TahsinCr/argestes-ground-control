from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from src.core.dependency import BaseDependencyContainer, DependencyContainer
from src.extension.host import ExtensionHost
from src.extension.api.contracts import ConnectionBinding
from src.extension.api.telemetry import TelemetryPipeline
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.logging import LogService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.async_runtime import AsyncRuntime
from src.services.connections import ConnectionHub
from src.services.facade import BackendFacade
from src.services.logging_service import LoggingService
from src.services.runtime import RuntimeServices
from src.services.settings import SettingsService
from src.services.telemetry import MutableTelemetryService


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    base_dir: Path


class DependencyPriority(IntEnum):
    INFRASTRUCTURE = 0
    VEHICLE = 10
    APPLICATION = 20
    RUNTIME = 30


def _logging(context):
    session = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    return LoggingService(
        log_path=context.base_dir / "data" / "logs" / f"gcs_{session}.json",
        max_file_bytes=2**63 - 1,
    )


class ApplicationContainer(BaseDependencyContainer):
    """Application-only composition. Features contribute their own factories."""

    def __init__(self, base_dir: Path, **kwargs):
        self.context = ApplicationContext(base_dir.resolve())
        super().__init__(**kwargs)

    def configure(self):
        self.instance(ApplicationContext, self.context)
        self.instance(DependencyContainer, self.container)
        self.singleton(AsyncRuntime, concrete=AsyncRuntime)
        self.singleton(
            LoggingService,
            factory=_logging,
            dependencies={"context": ApplicationContext},
        )
        self.singleton(
            LogService,
            factory=lambda service: service,
            dependencies={"service": LoggingService},
        )
        self.singleton(
            SettingsService,
            factory=lambda context, logging: SettingsService(
                context.base_dir, logging.child("SETTINGS")
            ),
            dependencies={"context": ApplicationContext, "logging": LoggingService},
        )
        self.singleton(
            SettingsRepository,
            factory=lambda service: service,
            dependencies={"service": SettingsService},
        )
        self.singleton(TelemetryService, concrete=MutableTelemetryService, priority=10)
        self.singleton(
            TelemetryPipeline,
            factory=lambda service: service,
            dependencies={"service": TelemetryService},
            priority=10,
        )
        self.singleton(ConnectionControlService, concrete=ConnectionHub, priority=10)
        self.singleton(
            ConnectionBinding,
            factory=lambda service: service,
            dependencies={"service": ConnectionControlService},
            priority=10,
        )
        host = ExtensionHost(self.context.base_dir, self)
        self.instance(ExtensionHost, host, priority=10)
        host.configure()
        self.singleton(
            BackendFacade,
            concrete=BackendFacade,
            dependencies={
                "telemetry": TelemetryService,
                "commands": CommandService,
                "settings": SettingsRepository,
            },
            priority=30,
        )
        self.singleton(
            RuntimeServices,
            concrete=RuntimeServices,
            dependencies={
                "container": DependencyContainer,
                "async_runtime": AsyncRuntime,
                "log_service": LoggingService,
                "settings_service": SettingsService,
                "vehicle_service": VehicleService,
                "telemetry_service": TelemetryService,
                "command_service": CommandService,
                "connection_service": ConnectionControlService,
                "facade": BackendFacade,
                "extension_host": ExtensionHost,
            },
            priority=30,
        )


def create_container(base_dir: Path, *, warmup=False):
    container = ApplicationContainer(base_dir)
    if warmup:
        container.warmup()
    return container
