from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.core.dependency import DependencyContainer
from src.core.events import Subscription
from src.models.video import VideoShareConfig
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.joystick import JoystickService
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


def _video_capture_configuration(backend) -> tuple[object, ...]:
    """Return only fields that can change capture/share execution."""
    return (
        tuple(
            (
                source.identifier,
                source.name,
                source.source_type,
                source.source_uri,
                source.codec,
                source.latency_ms,
                source.frame_timeout_s,
                source.auto_connect,
            )
            for source in backend.video_sources
        ),
        backend.active_video_source_id,
    )


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
    connection_service: ConnectionControlService
    joystick_service: JoystickService
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
    _share_switch_revision: int = field(default=0, repr=False)
    _video_configuration: tuple[object, ...] = field(default_factory=tuple, repr=False)
    _share_configuration: tuple[object, ...] = field(default_factory=tuple, repr=False)
    _cache_configuration: tuple[object, ...] = field(default_factory=tuple, repr=False)
    _mission_radii_configuration: tuple[tuple[str, float], ...] = field(default_factory=tuple, repr=False)
    _mission_altitude_configuration: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        backend = self.settings_service.current.backend
        self._share_configuration = tuple(backend.video_shares)
        self.video_service.configure_shares(self._share_configuration)
        self._video_configuration = _video_capture_configuration(backend)
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
        shares = tuple(backend.video_shares)
        if shares != self._share_configuration:
            self._queue_share_switch(shares)
        configuration = (
            tuple(backend.video_sources),
            backend.active_video_source_id,
        )
        capture_configuration = _video_capture_configuration(backend)
        if capture_configuration != self._video_configuration:
            self._queue_video_switch(configuration, capture_configuration)

    def _queue_share_switch(self, shares: tuple[VideoShareConfig, ...]) -> None:
        """Apply encoder-output changes without blocking the settings/UI thread."""
        with self._lifecycle_lock:
            self._share_switch_revision += 1
            revision = self._share_switch_revision

        def switch() -> None:
            with self._video_switch_lock:
                with self._lifecycle_lock:
                    if revision != self._share_switch_revision or self._stop_requested.is_set():
                        return
                try:
                    self.video_service.configure_shares(tuple(shares))
                    self._share_configuration = tuple(shares)
                except Exception as exc:
                    self.log_service.event(
                        "VIDEO",
                        "video.outputs_configure_failed",
                        f"Video yayın/kayıt çıkışları uygulanamadı: {exc}",
                        severity="danger",
                    )

        threading.Thread(target=switch, name="VideoOutputSwitch", daemon=True).start()

    def _queue_video_switch(
        self,
        configuration: tuple[object, ...],
        capture_configuration: tuple[object, ...],
    ) -> None:
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
                    self._video_configuration = capture_configuration
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
            ("joystick", self.joystick_service),
            ("parameters", self.parameter_service),
        )

    def _service_stop_order(self):
        return (
            ("parameters", self.parameter_service),
            ("joystick", self.joystick_service),
            ("telemetry", self.telemetry_service),
            # Stop recovery before closing the single MAVLink transport.  This
            # prevents the retry worker from reopening it during shutdown.
            ("connections", self.connection_service),
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
