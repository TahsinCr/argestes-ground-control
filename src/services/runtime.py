from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.core.dependency import DependencyContainer
from src.core.events import Subscription
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.async_runtime import AsyncRuntime
from src.services.facade import BackendFacade
from src.services.logging_service import LoggingService, ServiceLogHandler
from src.services.planner import PlannerService
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer


@dataclass(slots=True)
class RuntimeServices:
    """Own lifecycle for the shared GCS and MAVLink vehicle services."""

    container: DependencyContainer
    async_runtime: AsyncRuntime
    log_service: LoggingService
    settings_service: SettingsService
    vehicle_service: VehicleService
    telemetry_service: TelemetryService
    planner_service: PlannerService
    command_service: CommandService
    parameter_service: ParameterService
    tile_cache_service: TileCacheServer
    connection_service: ConnectionControlService
    facade: BackendFacade
    _subscriptions: list[Subscription] = field(default_factory=list, repr=False)
    _python_handler: ServiceLogHandler | None = field(default=None, repr=False)
    _started: bool = field(default=False, repr=False)
    _starting: bool = field(default=False, repr=False)
    _stop_requested: threading.Event = field(default_factory=threading.Event, repr=False)
    _lifecycle_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _stop_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cache_configuration: tuple[object, ...] = field(default_factory=tuple, repr=False)
    _mission_radii_configuration: tuple[tuple[str, float], ...] = field(
        default_factory=tuple, repr=False
    )
    _mission_altitude_configuration: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        backend = self.settings_service.current.backend
        self._cache_configuration = (
            backend.cache.path,
            backend.cache.enabled,
            backend.cache.limit_mb,
        )
        self._mission_radii_configuration = tuple(
            sorted((str(key), float(value)) for key, value in backend.mission.radii.items())
        )
        self._mission_altitude_configuration = float(backend.mission.default_altitude_m)
        self._subscriptions.append(
            self.settings_service.changed.subscribe(self._apply_settings)
        )

    def _apply_settings(self, updated) -> None:
        backend = updated.backend
        radii = tuple(sorted((str(key), float(value)) for key, value in backend.mission.radii.items()))
        if radii != self._mission_radii_configuration:
            self._mission_radii_configuration = radii
            self.planner_service.set_default_radii(backend.mission.radii)
        altitude = float(backend.mission.default_altitude_m)
        if altitude != self._mission_altitude_configuration:
            self._mission_altitude_configuration = altitude
            self.vehicle_service.set_default_mission_altitude(altitude)
        cache = (backend.cache.path, backend.cache.enabled, backend.cache.limit_mb)
        if cache != self._cache_configuration:
            self._cache_configuration = cache
            self.tile_cache_service.configure(
                cache_path=backend.cache.path,
                enabled=backend.cache.enabled,
                limit_mb=backend.cache.limit_mb,
            )

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
            ("tile_cache", self.tile_cache_service),
            ("connections", self.connection_service),
            ("planner", self.planner_service),
            ("telemetry", self.telemetry_service),
            ("parameters", self.parameter_service),
        )

    def _service_stop_order(self):
        return (
            ("parameters", self.parameter_service),
            ("telemetry", self.telemetry_service),
            ("connections", self.connection_service),
            ("vehicle", self.vehicle_service),
            ("planner", self.planner_service),
            ("tile_cache", self.tile_cache_service),
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
