from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.core.dependency import DependencyContainer
from src.extension.host import ExtensionHost
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.async_runtime import AsyncRuntime
from src.services.facade import BackendFacade
from src.services.logging_service import LoggingService, ServiceLogHandler
from src.services.settings import SettingsService


@dataclass(slots=True)
class RuntimeServices:
    """Own the application lifecycle; feature lifetimes belong to extensions."""

    container: DependencyContainer
    async_runtime: AsyncRuntime
    log_service: LoggingService
    settings_service: SettingsService
    vehicle_service: VehicleService
    telemetry_service: TelemetryService
    command_service: CommandService
    connection_service: ConnectionControlService
    facade: BackendFacade
    extension_host: ExtensionHost
    _python_handler: ServiceLogHandler | None = field(default=None, repr=False)
    _started: bool = field(default=False, repr=False)
    _starting: bool = field(default=False, repr=False)
    _stop_requested: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    _lifecycle_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False
    )
    _stop_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started or self._starting:
                return
            self._starting = True
            self._stop_requested.clear()
        self._install_python_logging()
        try:
            self._start_services()
        finally:
            cancelled = self._stop_requested.is_set()
            with self._lifecycle_lock:
                self._starting = False
                self._started = not cancelled
            if cancelled:
                self._stop_services()

    def _start_services(self) -> None:
        for name, service in self._service_start_order():
            if self._stop_requested.is_set():
                return
            try:
                service.start()
            except Exception as exc:
                self.log_service.event(
                    "RUNTIME",
                    f"runtime.{name}_start_failed",
                    f"{name} servisi başlatılamadı: {exc}",
                    severity="danger",
                )
                if name == "extensions":
                    self._stop_requested.set()
                    return

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._started and not self._starting:
                return
            self._stop_requested.set()
            self._started = False
        self._stop_services()

    def _stop_services(self) -> None:
        with self._stop_lock:
            for name, service in self._service_stop_order():
                try:
                    service.stop()
                except Exception as exc:
                    self.log_service.event(
                        "RUNTIME",
                        f"runtime.{name}_stop_failed",
                        f"{name} servisi durdurulamadı: {exc}",
                        severity="danger",
                    )
            self._remove_python_logging()
            self.log_service.flush()

    def _service_start_order(self):
        return (
            ("async_runtime", self.async_runtime),
            ("telemetry", self.telemetry_service),
            ("extensions", self.extension_host),
            ("connections", self.connection_service),
        )

    def _service_stop_order(self):
        return (
            ("connections", self.connection_service),
            ("extensions", self.extension_host),
            ("vehicle", self.vehicle_service),
            ("telemetry", self.telemetry_service),
            ("async_runtime", self.async_runtime),
        )

    close = stop

    def _install_python_logging(self) -> None:
        if self._python_handler is not None:
            return
        self._python_handler = self.log_service.create_python_handler("PYTHON")
        self._python_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logging.getLogger().addHandler(self._python_handler)

    def _remove_python_logging(self) -> None:
        if self._python_handler is None:
            return
        logging.getLogger().removeHandler(self._python_handler)
        self._python_handler.close()
        self._python_handler = None


def create_runtime_services(base_dir: Path) -> RuntimeServices:
    from src.container import create_container

    application_container = create_container(base_dir)
    return application_container.resolve(RuntimeServices)
