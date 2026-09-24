from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.services.runtime import RuntimeServices
from src.ui.adapters.services import (
    QtCommandService,
    QtConnectionControlService,
    QtLoggingService,
    QtParameterService,
    QtPlannerService,
    QtSettingsService,
    QtTelemetryService,
    QtTileCacheService,
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
    tile_cache_service: QtTileCacheService
    connection_service: QtConnectionControlService
    _start_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _lifecycle_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _stopping: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _active: bool = field(default=False, init=False, repr=False)

    @property
    def facade(self):
        return self.backend.facade

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._active:
                return
            self._active = True
            self._stopping.clear()
            self._start_thread = threading.Thread(
                target=self._start_backend,
                name="QtBackendStartup",
                daemon=True,
            )
            self._start_thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._active:
                return
            self._active = False
            self._stopping.set()
            thread = self._start_thread
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
            if thread is self._start_thread and thread is not None and not thread.is_alive():
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
            self.tile_cache_service,
            self.connection_service,
        )


def create_qt_runtime_services(base_dir: Path) -> QtRuntimeServices:
    from src.container import create_container
    from src.ui.container import UiContainer

    backend = create_container(base_dir)
    ui = UiContainer(backend.container, base_dir=base_dir)
    return ui.resolve(QtRuntimeServices)
