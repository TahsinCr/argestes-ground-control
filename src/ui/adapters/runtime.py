from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.services.runtime import RuntimeServices, create_runtime_services
from src.ui.adapters.services import (
    QtCommandService,
    QtConnectionControlService,
    QtLoggingService,
    QtPlannerService,
    QtParameterService,
    QtSettingsService,
    QtTelemetryService,
    QtTileCacheService,
    QtVideoService,
)


@dataclass(slots=True, weakref_slot=True)
class QtRuntimeServices:
    backend: RuntimeServices
    log_service: QtLoggingService
    settings_service: QtSettingsService
    telemetry_service: QtTelemetryService
    planner_service: QtPlannerService
    command_service: QtCommandService
    parameter_service: QtParameterService
    video_service: QtVideoService
    tile_cache_service: QtTileCacheService
    connection_service: QtConnectionControlService
    _start_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _lifecycle_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
    )
    _stopping: threading.Event = field(
        default_factory=threading.Event,
        init=False,
        repr=False,
    )
    _active: bool = field(default=False, init=False, repr=False)

    @property
    def archive_service(self): return self.backend.archive_service
    @property
    def facade(self): return self.backend.facade

    def start(self) -> None:
        self.video_service.start()
        with self._lifecycle_lock:
            if self._active:
                return
            self._active = True
            self._stopping.clear()
            thread = threading.Thread(
                target=self._start_backend,
                name="QtBackendStartup",
                daemon=True,
            )
            self._start_thread = thread
            thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._active:
                return
            self._active = False
            self._stopping.set()
            thread = self._start_thread
        self.video_service.stop()
        for adapter in self._adapters():
            adapter.close()
        self.backend.stop()
        deadline = time.monotonic() + 1.0
        for adapter in self._adapters():
            wait_for_workers = getattr(adapter, "wait_for_workers", None)
            if wait_for_workers is not None:
                wait_for_workers(max(0.0, deadline - time.monotonic()))
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lifecycle_lock:
            if (
                thread is not None
                and self._start_thread is thread
                and not thread.is_alive()
            ):
                self._start_thread = None

    def _start_backend(self) -> None:
        try:
            self.backend.start()
        finally:
            if self._stopping.is_set():
                self.backend.stop()

    def _adapters(self):
        return (
            self.log_service,
            self.settings_service,
            self.telemetry_service,
            self.planner_service,
            self.command_service,
            self.parameter_service,
            self.video_service,
            self.tile_cache_service,
            self.connection_service,
        )


def create_qt_runtime_services(base_dir: Path) -> QtRuntimeServices:
    backend = create_runtime_services(base_dir)
    runtime = QtRuntimeServices(
        backend=backend,
        log_service=QtLoggingService(backend.log_service),
        settings_service=QtSettingsService(backend.settings_service),
        telemetry_service=QtTelemetryService(backend.telemetry_service),
        planner_service=QtPlannerService(backend.planner_service),
        command_service=QtCommandService(backend.command_service),
        parameter_service=QtParameterService(backend.parameter_service),
        video_service=QtVideoService(
            backend.video_service,
            mock_mode=(
                backend.settings_service.current.backend.mock_mode
                or backend.settings_service.current.backend.video_source_type == "mock"
            ),
        ),
        tile_cache_service=QtTileCacheService(backend.tile_cache_service),
        connection_service=QtConnectionControlService(backend.connection_service),
    )
    runtime.telemetry_service.telemetry_updated.connect(runtime.video_service.consume_snapshot)
    return runtime
