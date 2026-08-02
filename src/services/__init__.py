from typing import TYPE_CHECKING, Any

from src.services.facade import BackendFacade
from src.services.logging_service import LoggingService, ServiceLogger
from src.services.settings import SettingsService

if TYPE_CHECKING:
    from src.services.runtime import RuntimeServices


def __getattr__(name: str) -> Any:
    if name in {"RuntimeServices", "create_runtime_services"}:
        from src.services.runtime import RuntimeServices, create_runtime_services

        return {
            "RuntimeServices": RuntimeServices,
            "create_runtime_services": create_runtime_services,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "BackendFacade",
    "LoggingService",
    "RuntimeServices",
    "ServiceLogger",
    "SettingsService",
    "create_runtime_services",
]
