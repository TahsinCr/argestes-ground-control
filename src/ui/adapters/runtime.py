from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from src.services.runtime import RuntimeServices
from src.ui.adapters.services import QtLoggingService, QtSettingsService


class RuntimeStartupSignals(QObject):
    changed = pyqtSignal(str, str)


@dataclass(slots=True, weakref_slot=True)
class QtRuntimeServices:
    backend: RuntimeServices
    log_service: QtLoggingService
    settings_service: QtSettingsService
    _start_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _lifecycle_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )
    _stopping: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False
    )
    _active: bool = field(default=False, init=False, repr=False)
    _starting: bool = field(default=False, init=False, repr=False)
    _startup_state: str = field(default="stopped", init=False, repr=False)
    _startup_message: str = field(default="", init=False, repr=False)
    _signals: RuntimeStartupSignals = field(
        default_factory=RuntimeStartupSignals, init=False, repr=False
    )

    @property
    def facade(self):
        return self.backend.facade

    @property
    def startup_changed(self):
        return self._signals.changed

    @property
    def startup_state(self) -> str:
        with self._lifecycle_lock:
            return self._startup_state

    @property
    def startup_message(self) -> str:
        with self._lifecycle_lock:
            return self._startup_message

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._active or self._starting:
                return
            self._starting = True
            self._startup_state = "starting"
            self._startup_message = ""
            self._stopping.clear()
            self._start_thread = threading.Thread(
                target=self._start_backend,
                name="QtBackendStartup",
                daemon=True,
            )
            thread = self._start_thread
        self._signals.changed.emit("starting", "")
        try:
            thread.start()
        except Exception as exc:
            with self._lifecycle_lock:
                self._starting = False
                self._active = False
                self._start_thread = None
                self._startup_state = "failed"
                self._startup_message = str(exc)
            self._log_startup_failure(str(exc))
            self._signals.changed.emit("failed", str(exc))

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._active and not self._starting:
                return
            self._active = False
            self._stopping.set()
            thread = self._start_thread
            self._startup_state = "stopping"
        self._signals.changed.emit("stopping", "")
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
        stopped = thread is None or not thread.is_alive()
        with self._lifecycle_lock:
            if (
                thread is self._start_thread
                and thread is not None
                and not thread.is_alive()
            ):
                self._start_thread = None
            if stopped:
                self._starting = False
                self._startup_state = "stopped"
                self._startup_message = ""
        if stopped:
            self._signals.changed.emit("stopped", "")

    def _start_backend(self) -> None:
        state = "failed"
        message = ""
        try:
            self.backend.start()
            if self._stopping.is_set():
                state = "stopped"
            elif getattr(self.backend, "started", False):
                failures = tuple(getattr(self.backend, "startup_failures", ()))
                state = "degraded" if failures else "ready"
                message = ", ".join(failures)
            else:
                failures = tuple(getattr(self.backend, "startup_failures", ()))
                message = (
                    ", ".join(failures)
                    if failures
                    else "Required backend services did not start"
                )
                self._log_startup_failure(message)
        except Exception as exc:
            message = str(exc)
            self._log_startup_failure(message)
        finally:
            if self._stopping.is_set():
                try:
                    self.backend.stop()
                except Exception as exc:
                    self._log_startup_failure(f"Backend shutdown after startup failed: {exc}")
                state = "stopped"
                message = ""
            with self._lifecycle_lock:
                self._starting = False
                self._active = state in {"ready", "degraded"}
                self._startup_state = state
                self._startup_message = message
                if threading.current_thread() is self._start_thread:
                    self._start_thread = None
            self._signals.changed.emit(state, message)

    def _log_startup_failure(self, message: str) -> None:
        self.backend.log_service.event(
            "RUNTIME",
            "runtime.startup_failed",
            f"Backend startup failed: {message}",
            severity="danger",
        )

    def _adapters(self):
        return (
            self.log_service,
            self.settings_service,
        )


def create_qt_runtime_services(base_dir: Path) -> QtRuntimeServices:
    from src.container import create_container
    from src.ui.container import UiContainer

    backend = create_container(base_dir)
    ui = UiContainer(backend.container, base_dir=base_dir)
    return ui.resolve(QtRuntimeServices)
