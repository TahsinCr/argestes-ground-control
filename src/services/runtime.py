from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.core.dependency import DependencyContainer
from src.core.events import Subscription
from src.services.abstracts.commands import CommandService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.abstracts.video import VideoService
from src.services.async_runtime import AsyncRuntime
from src.services.facade import BackendFacade
from src.services.logging_service import LoggingService, ServiceLogHandler
from src.services.mission_archive import MissionArchiveService
from src.services.planner import PlannerService
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer


@dataclass(slots=True)
class RuntimeServices:
    container: DependencyContainer
    async_runtime: AsyncRuntime
    log_service: LoggingService
    settings_service: SettingsService
    vehicle_service: VehicleService
    telemetry_service: TelemetryService
    planner_service: PlannerService
    command_service: CommandService
    parameter_service: ParameterService
    video_service: VideoService
    tile_cache_service: TileCacheServer
    archive_service: MissionArchiveService
    facade: BackendFacade
    _subscriptions: list[Subscription] = field(default_factory=list, repr=False)
    _python_handler: ServiceLogHandler | None = field(default=None, repr=False)
    _started: bool = field(default=False, repr=False)
    _starting: bool = field(default=False, repr=False)
    _stop_requested: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
    )
    _lifecycle_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
    )
    _stop_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )
    _video_switch_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _video_switch_revision: int = field(default=0, repr=False)
    _video_configuration: tuple[object, ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        backend = self.settings_service.current.backend
        self.video_service.configure_shares(tuple(backend.video_shares))
        self._video_configuration = (
            tuple(backend.video_sources),
            backend.active_video_source_id,
        )
        self._subscriptions.append(
            self.settings_service.changed.subscribe(self._apply_settings)
        )

    def _apply_settings(self, updated) -> None:
        self.planner_service.set_default_radii(updated.backend.mission.radii)
        self.vehicle_service.set_default_mission_altitude(
            updated.backend.mission.default_altitude_m
        )
        self.tile_cache_service.configure(
            cache_path=updated.backend.cache.path,
            enabled=updated.backend.cache.enabled,
            limit_mb=updated.backend.cache.limit_mb,
        )
        self.video_service.configure_shares(tuple(updated.backend.video_shares))
        configuration = (
            tuple(updated.backend.video_sources),
            updated.backend.active_video_source_id,
        )
        if configuration != self._video_configuration:
            self._queue_video_switch(configuration)

    def _queue_video_switch(self, configuration: tuple[object, ...]) -> None:
        """Reconfigure capture off the publisher/UI thread; keep only latest intent."""

        with self._lifecycle_lock:
            self._video_switch_revision += 1
            revision = self._video_switch_revision

        def switch() -> None:
            with self._video_switch_lock:
                with self._lifecycle_lock:
                    if revision != self._video_switch_revision or self._stop_requested.is_set():
                        return
                sources, active_identifier = configuration
                try:
                    self.video_service.configure_sources(
                        tuple(sources),
                        str(active_identifier),
                    )
                    if self._started and not self._stop_requested.is_set():
                        self.video_service.start()
                    self._video_configuration = configuration
                    self.log_service.event(
                        "VIDEO",
                        "video.source_switched",
                        "Aktif video bağlantısı değiştirildi",
                        severity="success",
                        details={"source_id": active_identifier},
                    )
                except Exception as exc:
                    self.log_service.event(
                        "VIDEO",
                        "video.source_switch_failed",
                        f"Video bağlantısı değiştirilemedi: {exc}",
                        severity="danger",
                        details={"source_id": active_identifier},
                    )

        threading.Thread(target=switch, name="VideoSourceSwitch", daemon=True).start()

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
        settings = self.settings_service.current
        if (
            not self._stop_requested.is_set()
            and (
                settings.backend.mock_mode
                or settings.backend.video_source_type == "mock"
                or settings.backend.video_auto_connect
            )
        ):
            try:
                self.video_service.start()
            except Exception as exc:
                self.log_service.event(
                    "VIDEO",
                    "runtime.video_start_failed",
                    f"Video servisi başlatılamadı: {exc}",
                    severity="danger",
                    details={
                        "source_type": settings.backend.video_source_type,
                        "source_uri": settings.backend.video_source_uri,
                    },
                )
        if (
            not self._stop_requested.is_set()
            and not settings.backend.mock_mode
            and settings.backend.mavlink.auto_connect
        ):
            try:
                self.vehicle_service.start()
            except Exception as exc:
                self.log_service.event(
                    "VEHICLE",
                    "vehicle.connect_failed",
                    f"Uçuş cihazı bağlantısı kurulamadı: {exc}",
                    severity="danger",
                    details={"endpoint": settings.backend.mavlink.endpoint},
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
            ("planner", self.planner_service),
            ("telemetry", self.telemetry_service),
            ("parameters", self.parameter_service),
        )

    def _service_stop_order(self):
        return (
            ("parameters", self.parameter_service),
            ("telemetry", self.telemetry_service),
            ("vehicle", self.vehicle_service),
            ("planner", self.planner_service),
            ("video", self.video_service),
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
