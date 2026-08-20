from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.dependency import BaseDependencyContainer, DependencyContainer
from src.services.async_runtime import AsyncRuntime
from src.services.abstracts.logging import LogService
from src.services.abstracts.settings import SettingsRepository
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from src.services.vehicle import configured_orin_commands


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    base_dir: Path


@dataclass(frozen=True, slots=True)
class OrinCommandCatalog:
    codes: tuple[str, ...]


def _logging(context: ApplicationContext) -> LoggingService:
    return LoggingService(log_path=context.base_dir / "data" / "logs" / "yki.jsonl")


def _log_contract(service: LoggingService) -> LogService:
    return service


def _settings(
    context: ApplicationContext,
    logging: LoggingService,
) -> SettingsService:
    return SettingsService(context.base_dir, logging.child("SETTINGS"))


def _settings_contract(service: SettingsService) -> SettingsRepository:
    return service


def _orin_commands(
    context: ApplicationContext,
    logging: LoggingService,
) -> OrinCommandCatalog:
    config_path = context.base_dir / "config.json"
    try:
        codes = configured_orin_commands(config_path)
    except (OSError, ValueError) as exc:
        logging.event(
            "ORIN",
            "orin.command_config_failed",
            f"Orin command configuration could not be loaded: {exc}",
            severity="danger",
            details={"config_path": str(config_path)},
        )
        return OrinCommandCatalog(())
    if codes:
        logging.event(
            "ORIN",
            "orin.command_config_loaded",
            f"Loaded {len(codes)} configured Orin commands",
            severity="success",
            details={"commands": list(codes)},
        )
    return OrinCommandCatalog(tuple(codes))


def register_infrastructure_dependencies(
    container: BaseDependencyContainer,
    context: ApplicationContext,
    *,
    priority: int,
) -> None:
    container.instance(ApplicationContext, context, priority=priority)
    container.instance(DependencyContainer, container.container, priority=priority)
    container.singleton(AsyncRuntime, concrete=AsyncRuntime, priority=priority)
    container.singleton(
        LoggingService,
        factory=_logging,
        dependencies={"context": ApplicationContext},
        priority=priority,
    )
    container.singleton(
        LogService,
        factory=_log_contract,
        dependencies={"service": LoggingService},
        priority=priority,
    )
    container.singleton(
        SettingsService,
        factory=_settings,
        dependencies={"context": ApplicationContext, "logging": LoggingService},
        priority=priority,
    )
    container.singleton(
        SettingsRepository,
        factory=_settings_contract,
        dependencies={"service": SettingsService},
        priority=priority,
    )
    container.singleton(
        OrinCommandCatalog,
        factory=_orin_commands,
        dependencies={"context": ApplicationContext, "logging": LoggingService},
        priority=priority,
    )


__all__ = [
    "ApplicationContext",
    "AsyncRuntime",
    "OrinCommandCatalog",
    "register_infrastructure_dependencies",
]
