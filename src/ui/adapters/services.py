from __future__ import annotations

import queue
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Hashable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot

from src.core.events import Subscription
from src.models.connections import ConnectionStatus
from src.models.logs import LogEntry
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import LoggingService
from src.services.settings import DELETE_SETTING, SettingsService


@dataclass(slots=True)
class _PendingDispatch:
    callback: Callable[..., None]
    args: tuple[Any, ...]
    coalesce_key: Hashable | None = None


class _QtAdapter(QObject):
    # Bound work performed by a single queued callback so a burst of logs or
    # service events cannot monopolize the GUI event loop for a whole frame.
    _MAX_DISPATCH_BATCH = 32
    _MAX_DISPATCH_SECONDS = 0.004
    _dispatch_requested = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._subscriptions: list[Subscription] = []
        self._pending_events: queue.SimpleQueue[_PendingDispatch] = queue.SimpleQueue()
        self._coalesced_events: dict[Hashable, _PendingDispatch] = {}
        self._dispatch_lock = threading.Lock()
        self._dispatch_scheduled = False
        self._closed = False
        self._worker_lock = threading.Lock()
        self._workers: set[threading.Thread] = set()
        self._dispatch_requested.connect(
            self._drain_events,
            Qt.ConnectionType.QueuedConnection,
        )
        self.destroyed.connect(self._cancel_subscriptions)

    def _subscribe(
        self,
        stream,
        callback: Callable[[Any], None],
        *,
        latest_by: Callable[[Any], Hashable | None] | None = None,
    ) -> None:
        """Queue events; only explicitly replaceable snapshots may be coalesced."""

        def enqueue(value: Any) -> None:
            key = latest_by(value) if latest_by is not None else None
            self._post(callback, value, coalesce_key=key)

        self._subscriptions.append(
            stream.subscribe(enqueue)
        )

    def _post(
        self,
        callback: Callable[..., None],
        *args: Any,
        coalesce_key: Hashable | None = None,
    ) -> None:
        """Queue a reliable event, or replace a pending snapshot with its latest value."""

        should_schedule = False
        with self._dispatch_lock:
            if self._closed:
                return
            pending = (
                self._coalesced_events.get(coalesce_key)
                if coalesce_key is not None
                else None
            )
            if pending is not None:
                pending.callback = callback
                pending.args = args
            else:
                pending = _PendingDispatch(callback, args, coalesce_key)
                self._pending_events.put(pending)
                if coalesce_key is not None:
                    self._coalesced_events[coalesce_key] = pending
            if not self._dispatch_scheduled:
                self._dispatch_scheduled = True
                should_schedule = True
        if should_schedule:
            self._dispatch_requested.emit()

    @pyqtSlot()
    def _drain_events(self) -> None:
        """Run pending backend events only on the Qt main thread."""

        with self._dispatch_lock:
            if self._closed:
                self._dispatch_scheduled = False
                return
        deadline = time.monotonic() + self._MAX_DISPATCH_SECONDS
        for _index in range(self._MAX_DISPATCH_BATCH):
            with self._dispatch_lock:
                if self._closed:
                    break
                try:
                    pending = self._pending_events.get_nowait()
                except queue.Empty:
                    break
                if (
                    pending.coalesce_key is not None
                    and self._coalesced_events.get(pending.coalesce_key) is pending
                ):
                    del self._coalesced_events[pending.coalesce_key]
            try:
                pending.callback(*pending.args)
            except Exception:
                # Never let a Python exception escape a Qt slot. PyQt may
                # abort the process when that happens, especially while the
                # widget tree is being destroyed.
                traceback.print_exc()
            if time.monotonic() >= deadline:
                break
        schedule_next = False
        with self._dispatch_lock:
            if self._closed:
                self._dispatch_scheduled = False
            elif self._pending_events.empty():
                self._dispatch_scheduled = False
            else:
                schedule_next = True
        if schedule_next:
            # Yield to paint and input events between bursts. Re-emitting the
            # queued signal immediately can otherwise starve button clicks.
            QTimer.singleShot(0, self._drain_events)

    def close(self) -> None:
        with self._dispatch_lock:
            if self._closed:
                return
            self._closed = True
            self._dispatch_scheduled = False
            self._coalesced_events.clear()
            while True:
                try:
                    self._pending_events.get_nowait()
                except queue.Empty:
                    break
        self._cancel_subscriptions()

    def _start_worker(
        self,
        target: Callable[..., None],
        *,
        name: str,
        args: tuple[Any, ...] = (),
    ) -> threading.Thread:
        """Start a tracked short-lived operation worker."""

        def run() -> None:
            try:
                target(*args)
            finally:
                with self._worker_lock:
                    self._workers.discard(threading.current_thread())

        thread = threading.Thread(target=run, name=name, daemon=True)
        with self._dispatch_lock:
            if self._closed:
                return thread
            with self._worker_lock:
                self._workers.add(thread)
            try:
                thread.start()
            except Exception:
                with self._worker_lock:
                    self._workers.discard(thread)
                raise
        return thread

    def wait_for_workers(self, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._worker_lock:
                workers = tuple(self._workers)
            if not workers:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            worker = workers[0]
            if worker is threading.current_thread():
                return
            worker.join(timeout=min(remaining, 0.1))

    def _cancel_subscriptions(self, *_args: object) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()


class QtLoggingService(_QtAdapter):
    entries_emitted = pyqtSignal(object)
    _UI_BATCH_INTERVAL_MS = 50
    _MAX_PENDING_UI_LOGS = 5_000

    def __init__(self, backend: LoggingService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._log_lock = threading.Lock()
        self._pending_logs: deque[LogEntry] = deque()
        self._omitted_pending_logs = 0
        self._log_batch_scheduled = False
        self._subscriptions.append(backend.entries.subscribe(self._capture_log))
        self._log_timer = QTimer(self)
        self._log_timer.setSingleShot(True)
        self._log_timer.setInterval(self._UI_BATCH_INTERVAL_MS)
        self._log_timer.timeout.connect(self._emit_log_batch)

    def _capture_log(self, entry: LogEntry) -> None:
        should_schedule = False
        with self._log_lock:
            if len(self._pending_logs) >= self._MAX_PENDING_UI_LOGS:
                self._pending_logs.popleft()
                self._omitted_pending_logs += 1
            self._pending_logs.append(entry)
            if not self._log_batch_scheduled:
                self._log_batch_scheduled = True
                should_schedule = True
        if should_schedule:
            self._post(self._start_log_timer)

    @pyqtSlot()
    def _start_log_timer(self) -> None:
        if not self._closed and not self._log_timer.isActive():
            self._log_timer.start()

    @pyqtSlot()
    def _emit_log_batch(self) -> None:
        with self._log_lock:
            entries = tuple(self._pending_logs)
            self._pending_logs.clear()
            omitted = self._omitted_pending_logs
            self._omitted_pending_logs = 0
            self._log_batch_scheduled = False
        if omitted:
            entries = (
                LogEntry(
                    source="LOG",
                    action="ui.log_entries_omitted",
                    severity="warning",
                    message=(
                        f"{omitted} older log entries were omitted from the UI buffer; "
                        "they remain in the session log file."
                    ),
                    details={"omitted_count": omitted},
                ),
                *entries,
            )
        if entries:
            self.entries_emitted.emit(entries)

    def close(self) -> None:
        self._log_timer.stop()
        with self._log_lock:
            self._pending_logs.clear()
            self._omitted_pending_logs = 0
            self._log_batch_scheduled = False
        super().close()

    @property
    def history(self):
        return self.backend.history

    def child(self, source: str):
        return self.backend.child(source)

    def log(self, source: str, message: str, severity: str = "info") -> None:
        self.backend.log(source, message, severity)

    def emit_entry(self, entry: LogEntry) -> None:
        self.backend.emit_entry(entry)

    def reemit_history(self) -> None:
        self.backend.reemit_history()

    def create_python_handler(self, source: str = "PYLOG"):
        return self.backend.create_python_handler(source)


class QtSettingsService(_QtAdapter):
    _POPUP_SAVE_DEBOUNCE_S = 0.12
    settings_changed = pyqtSignal(object)
    save_failed = pyqtSignal(str)

    def __init__(self, backend: SettingsService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._pending_changes: dict[tuple[str, ...], object] = {}
        self._last_submitted = backend.current.to_dict()
        self._save_lock = threading.Lock()
        self._save_worker_running = False
        self._subscribe(
            backend.changed,
            self.settings_changed.emit,
            latest_by=lambda _settings: ("settings.snapshot",),
        )

    @property
    def current(self) -> SettingsModel:
        return self.backend.current

    def save(self, settings: SettingsModel) -> None:
        self.backend.save(settings)
        with self._save_lock:
            self._last_submitted = self.backend.current.to_dict()

    def save_async(self, settings: SettingsModel) -> None:
        """Persist the latest popup change without blocking the Qt event loop.

        Coalescing avoids making a burst of popup edits execute every
        intermediate configuration.
        """

        snapshot = SettingsModel.from_dict(settings.to_dict()).to_dict()
        with self._save_lock:
            changes = self._diff_settings(self._last_submitted, snapshot)
            self._last_submitted = snapshot
            for path, value in changes.items():
                self._merge_pending_change(self._pending_changes, path, value)
            if not self._pending_changes or self._save_worker_running:
                return
            self._save_worker_running = True
        self._start_worker(self._drain_saves, name="QtSettingsSave")

    @classmethod
    def _diff_settings(
        cls,
        before: object,
        after: object,
        path: tuple[str, ...] = (),
    ) -> dict[tuple[str, ...], object]:
        if path[:1] == ("extensions",):
            return {}
        if isinstance(before, dict) and isinstance(after, dict):
            changes: dict[tuple[str, ...], object] = {}
            for key in sorted(before.keys() | after.keys()):
                child_path = (*path, str(key))
                if key not in after:
                    if child_path:
                        changes[child_path] = DELETE_SETTING
                elif key not in before:
                    value = after[key]
                    if isinstance(value, dict) and value:
                        changes.update(cls._diff_settings({}, value, child_path))
                    else:
                        changes[child_path] = value
                elif before[key] != after[key]:
                    changes.update(cls._diff_settings(before[key], after[key], child_path))
            return changes
        if path and before != after:
            return {path: after}
        return {}

    @staticmethod
    def _merge_pending_change(
        pending: dict[tuple[str, ...], object],
        path: tuple[str, ...],
        value: object,
    ) -> None:
        for existing_path, existing_value in tuple(pending.items()):
            if existing_path == path:
                pending.pop(existing_path)
                continue
            if path[: len(existing_path)] == existing_path:
                if isinstance(existing_value, dict):
                    nested = deepcopy(existing_value)
                    target = nested
                    for part in path[len(existing_path) : -1]:
                        child = target.get(part)
                        if not isinstance(child, dict):
                            child = {}
                            target[part] = child
                        target = child
                    key = path[-1]
                    if value is DELETE_SETTING:
                        target.pop(key, None)
                    else:
                        target[key] = deepcopy(value)
                    pending[existing_path] = nested
                    return
                pending.pop(existing_path)
            elif existing_path[: len(path)] == path:
                pending.pop(existing_path)
        pending[path] = deepcopy(value)

    def _drain_saves(self) -> None:
        while True:
            # Let a quick run of toggles settle into one persisted update.
            # This prevents every intermediate state from triggering the
            # complete settings/UI refresh path.
            time.sleep(self._POPUP_SAVE_DEBOUNCE_S)
            with self._save_lock:
                pending = self._pending_changes
                self._pending_changes = {}
            if not pending:
                with self._save_lock:
                    if not self._pending_changes:
                        self._save_worker_running = False
                        return
                continue
            try:
                self.backend.apply_changes(pending)
            except Exception as exc:
                self._post(self.save_failed.emit, str(exc))
                with self._save_lock:
                    failed_changes = pending
                    for path, value in self._pending_changes.items():
                        self._merge_pending_change(failed_changes, path, value)
                    self._pending_changes = failed_changes
                    self._save_worker_running = False
                return

    def patch(self, **changes: object) -> None:
        self.backend.patch(**changes)

    def set_value(self, path: str, value: object) -> None:
        self.backend.set_value(path, value)


class QtTelemetryService(_QtAdapter):
    telemetry_updated = pyqtSignal(object)
    flight_dynamics_updated = pyqtSignal(object)
    # The backend may ingest MAVLink considerably faster than the screen needs
    # to be laid out. The attitude widget interpolates at 60 Hz independently;
    # the remaining panels only need the freshest snapshot once per UI frame.
    _UI_FRAME_INTERVAL_MS = 33

    def __init__(
        self, backend: TelemetryService, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self._latest_lock = threading.Lock()
        self._latest_snapshot: TelemetrySnapshot | None = None
        self._latest_dynamics_snapshot: TelemetrySnapshot | None = None
        self._frame_scheduled = False
        self._dynamics_scheduled = False
        self._dynamics_signature: tuple[object, ...] | None = None
        self._subscriptions.append(
            backend.updated.subscribe(self._capture_latest_snapshot)
        )
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_timer.setInterval(self._UI_FRAME_INTERVAL_MS)
        self._frame_timer.timeout.connect(self._emit_latest_snapshot)

    def _capture_latest_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        """Keep backend throughput independent from the GUI render queue."""

        should_schedule = False
        should_schedule_dynamics = False
        dynamics_signature = (
            snapshot.telemetry_link,
            snapshot.autopilot_link,
            snapshot.attitude_received_at,
            snapshot.global_position_received_at,
            snapshot.hud_received_at,
            snapshot.roll,
            snapshot.pitch,
            snapshot.heading,
            snapshot.air_speed,
            snapshot.ground_speed,
            snapshot.altitude_agl,
        )
        with self._latest_lock:
            self._latest_snapshot = snapshot
            if not self._frame_scheduled:
                self._frame_scheduled = True
                should_schedule = True
            if dynamics_signature != self._dynamics_signature:
                self._dynamics_signature = dynamics_signature
                self._latest_dynamics_snapshot = snapshot
                if not self._dynamics_scheduled:
                    self._dynamics_scheduled = True
                    should_schedule_dynamics = True
        if should_schedule_dynamics:
            # Flight dynamics uses a separate latest-only path. It reaches the
            # next GUI event-loop turn without waiting for map/panel batching.
            self._post(self._emit_latest_dynamics_snapshot)
        if should_schedule:
            self._post(self._start_frame_timer)

    @pyqtSlot()
    def _emit_latest_dynamics_snapshot(self) -> None:
        with self._latest_lock:
            snapshot = self._latest_dynamics_snapshot
            self._latest_dynamics_snapshot = None
            self._dynamics_scheduled = False
        if snapshot is not None and not self._closed:
            self.flight_dynamics_updated.emit(snapshot)

    @pyqtSlot()
    def _start_frame_timer(self) -> None:
        if not self._closed and not self._frame_timer.isActive():
            self._frame_timer.start()

    @pyqtSlot()
    def _emit_latest_snapshot(self) -> None:
        with self._latest_lock:
            snapshot = self._latest_snapshot
            self._latest_snapshot = None
            self._frame_scheduled = False
        if snapshot is not None:
            self.telemetry_updated.emit(snapshot)
        should_schedule = False
        with self._latest_lock:
            if (
                not self._closed
                and self._latest_snapshot is not None
                and not self._frame_scheduled
            ):
                self._frame_scheduled = True
                should_schedule = True
        if should_schedule:
            self._post(self._start_frame_timer)

    def close(self) -> None:
        self._frame_timer.stop()
        with self._latest_lock:
            self._latest_snapshot = None
            self._latest_dynamics_snapshot = None
            self._frame_scheduled = False
            self._dynamics_scheduled = False
            self._dynamics_signature = None
        super().close()

    @property
    def current(self) -> TelemetrySnapshot:
        return self.backend.current

    def start(self) -> None:
        self.backend.start()

    def stop(self) -> None:
        self.backend.stop()

    def push(self, snapshot: TelemetrySnapshot) -> None:
        self.backend.push(snapshot)


class QtCommandService(_QtAdapter):
    command_dispatched = pyqtSignal(str, dict)
    command_completed = pyqtSignal(object)

    def __init__(self, backend: CommandService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.completed, self.command_completed.emit)

    @property
    def available_tasks(self) -> tuple[str, ...]:
        return self.backend.available_tasks

    @property
    def available_flight_modes(self) -> tuple[str, ...]:
        return self.backend.available_flight_modes

    @property
    def available_command_codes(self) -> tuple[str, ...]:
        return self.backend.available_command_codes

    def issue_command(
        self,
        command: str,
        params: dict | None = None,
        *,
        source: str = "qt-ui",
    ):
        normalized = command.strip().upper()
        parameters = dict(params or {})
        # Mark the control pending synchronously. This closes the small window
        # in which a delayed backend event allowed repeated ARM/mode clicks.
        self.command_dispatched.emit(normalized, parameters)
        return self._start_worker(
            lambda: self.backend.issue_command(normalized, parameters, source=source),
            name=f"QtCommand-{normalized}",
        )


class QtConnectionControlService(_QtAdapter):
    status_changed = pyqtSignal(object)
    operation_started = pyqtSignal(str)
    operation_finished = pyqtSignal(str, bool, str, object)

    def __init__(
        self,
        backend: ConnectionControlService,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self._operations_lock = threading.Lock()
        self._operations: dict[str, threading.Thread] = {}
        self._subscribe(
            backend.changed,
            self.status_changed.emit,
            latest_by=lambda status: ("connection.status", status.channel),
        )

    def status(self, channel: str) -> ConnectionStatus:
        return self.backend.status(channel)

    def reconnect(self, channel: str, options: dict[str, object]) -> threading.Thread:
        return self._run("reconnect", channel, options)

    def repair_serial_access(self, channel: str) -> threading.Thread:
        return self._run("repair_serial_access", channel, {})

    def disconnect(self, channel: str) -> threading.Thread:
        return self._run("disconnect", channel, {})

    def _run(
        self,
        action: str,
        channel: str,
        options: dict[str, object],
    ) -> threading.Thread:
        with self._operations_lock:
            active = self._operations.get(channel)
            if active is not None and active.is_alive():
                return active
        self.operation_started.emit(channel)

        def execute() -> None:
            try:
                self._execute(action, channel, dict(options))
            finally:
                with self._operations_lock:
                    self._operations.pop(channel, None)

        worker = self._start_worker(
            execute,
            name=f"QtConnection-{action}-{channel}",
        )
        with self._operations_lock:
            self._operations[channel] = worker
        return worker

    def _execute(self, action: str, channel: str, options: dict[str, object]) -> None:
        try:
            if action == "reconnect":
                status = self.backend.reconnect(channel, options)
                message = status.summary
            elif action == "repair_serial_access":
                status = self.backend.repair_serial_access(channel)
                message = "Seri port erişimi verildi; bağlantı yeniden başlatıldı"
            else:
                status = self.backend.disconnect(channel)
                message = "Bağlantı kapatıldı"
        except Exception as exc:
            self._post(
                self.operation_finished.emit,
                channel,
                False,
                str(exc),
                self.backend.status(channel),
            )
            return
        self._post(self.operation_finished.emit, channel, True, message, status)
