from __future__ import annotations

import queue
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

from src.core.events import Subscription
from src.models.logs import LogEntry
from src.models.connections import ConnectionStatus
from src.models.mission import MapContact, MapOverlay, MissionState
from src.models.settings import SettingsModel
from src.models.target_selection import TargetSelectionResult
from src.models.telemetry import TelemetrySnapshot
from src.models.video import VideoFrame, VideoShareConfig
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.planner import PlannerService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.video import VideoService
from src.services.abstracts.onboard import OnboardMissionServiceContract
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer
from src.services.target_selection import TargetSelectionService


class _QtAdapter(QObject):
    # Bound work performed by a single queued callback so a burst of logs or
    # service events cannot monopolize the GUI event loop for a whole frame.
    _MAX_DISPATCH_BATCH = 32
    _dispatch_requested = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._subscriptions: list[Subscription] = []
        self._pending_events: queue.SimpleQueue[
            tuple[Callable[..., None], tuple[Any, ...]]
        ] = queue.SimpleQueue()
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

    def _subscribe(self, stream, callback: Callable[[Any], None]) -> None:
        """Move a backend event to the GUI queue without touching the Qt API."""

        self._subscriptions.append(
            stream.subscribe(lambda value: self._post(callback, value))
        )

    def _post(self, callback: Callable[..., None], *args: Any) -> None:
        """Pure-Python dispatch point safe for worker threads to call."""

        should_schedule = False
        with self._dispatch_lock:
            if self._closed:
                return
            self._pending_events.put((callback, args))
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
        for _index in range(self._MAX_DISPATCH_BATCH):
            try:
                callback, args = self._pending_events.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                # Never let a Python exception escape a Qt slot. PyQt may
                # abort the process when that happens, especially while the
                # widget tree is being destroyed.
                traceback.print_exc()
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
        self._cancel_subscriptions()
        while True:
            try:
                self._pending_events.get_nowait()
            except queue.Empty:
                break

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

    def __init__(self, backend: LoggingService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._log_lock = threading.Lock()
        self._pending_logs: list[LogEntry] = []
        self._log_batch_scheduled = False
        self._subscriptions.append(backend.entries.subscribe(self._capture_log))
        self._log_timer = QTimer(self)
        self._log_timer.setSingleShot(True)
        self._log_timer.setInterval(self._UI_BATCH_INTERVAL_MS)
        self._log_timer.timeout.connect(self._emit_log_batch)

    def _capture_log(self, entry: LogEntry) -> None:
        should_schedule = False
        with self._log_lock:
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
            self._log_batch_scheduled = False
        if entries:
            self.entries_emitted.emit(entries)

    def close(self) -> None:
        self._log_timer.stop()
        with self._log_lock:
            self._pending_logs.clear()
            self._log_batch_scheduled = False
        super().close()

    @property
    def history(self): return self.backend.history
    def child(self, source: str): return self.backend.child(source)
    def log(self, source: str, message: str, severity: str = "info") -> None:
        self.backend.log(source, message, severity)
    def emit_entry(self, entry: LogEntry) -> None: self.backend.emit_entry(entry)
    def reemit_history(self) -> None: self.backend.reemit_history()
    def create_python_handler(self, source: str = "PYLOG"):
        return self.backend.create_python_handler(source)


class QtSettingsService(_QtAdapter):
    _POPUP_SAVE_DEBOUNCE_S = 0.12
    settings_changed = pyqtSignal(object)
    cache_cleared = pyqtSignal()
    save_failed = pyqtSignal(str)

    def __init__(self, backend: SettingsService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._pending_save: SettingsModel | None = None
        self._save_lock = threading.Lock()
        self._save_worker_running = False
        self._subscribe(backend.changed, self.settings_changed.emit)
        self._subscribe(backend.cache_cleared, lambda _value: self.cache_cleared.emit())

    @property
    def current(self) -> SettingsModel: return self.backend.current
    def save(self, settings: SettingsModel) -> None: self.backend.save(settings)
    def save_async(self, settings: SettingsModel) -> None:
        """Persist the latest popup change without blocking the Qt event loop.

        Applying a video-share profile may stop GStreamer workers, which can
        wait briefly for a blocked transport sink.  Coalescing avoids making a
        burst of toggle clicks execute every intermediate configuration.
        """

        snapshot = SettingsModel.from_dict(settings.to_dict())
        with self._save_lock:
            self._pending_save = snapshot
            if self._save_worker_running:
                return
            self._save_worker_running = True
        self._start_worker(self._drain_saves, name="QtSettingsSave")

    def _drain_saves(self) -> None:
        while True:
            # Let a quick run of toggles settle into one persisted update.
            # This prevents every intermediate state from triggering the
            # complete settings/UI refresh path.
            time.sleep(self._POPUP_SAVE_DEBOUNCE_S)
            with self._save_lock:
                pending = self._pending_save
                self._pending_save = None
            if pending is None:
                with self._save_lock:
                    if self._pending_save is None:
                        self._save_worker_running = False
                        return
                continue
            try:
                self.backend.save(pending)
            except Exception as exc:
                self._post(self.save_failed.emit, str(exc))
    def patch(self, **changes: object) -> None: self.backend.patch(**changes)
    def set_value(self, path: str, value: object) -> None: self.backend.set_value(path, value)
    def clear_cache(self) -> None:
        self._start_worker(self.backend.clear_cache, name="SettingsCacheClear")


class QtTelemetryService(_QtAdapter):
    telemetry_updated = pyqtSignal(object)
    flight_dynamics_updated = pyqtSignal(object)
    # The backend may ingest MAVLink considerably faster than the screen needs
    # to be laid out. The attitude widget interpolates at 60 Hz independently;
    # the remaining panels only need the freshest snapshot once per UI frame.
    _UI_FRAME_INTERVAL_MS = 33

    def __init__(self, backend: TelemetryService, parent: QObject | None = None) -> None:
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
    def current(self) -> TelemetrySnapshot: return self.backend.current
    def start(self) -> None: self.backend.start()
    def stop(self) -> None: self.backend.stop()
    def push(self, snapshot: TelemetrySnapshot) -> None: self.backend.push(snapshot)


class QtPlannerService(_QtAdapter):
    planner_changed = pyqtSignal(object)

    def __init__(self, backend: PlannerService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.changed, self.planner_changed.emit)

    @property
    def state(self) -> MissionState: return self.backend.state
    @property
    def api(self): return getattr(self.backend, "api", None)
    def start(self) -> None: self.backend.start()
    def stop(self) -> None: self.backend.stop()
    def set_default_radii(self, values: dict) -> None: self.backend.set_default_radii(values)
    def set_base_layer(self, layer: str) -> None: self.backend.set_base_layer(layer)
    def set_active_tool(self, tool: str) -> None: self.backend.set_active_tool(tool)
    def set_mission_loop(self, enabled: bool) -> None: self.backend.set_mission_loop(enabled)
    def set_overlays(self, overlays) -> None: self.backend.set_overlays(overlays)
    def replace_source_overlays(self, source: str, overlays) -> None:
        self.backend.replace_source_overlays(source, overlays)
    def upsert_overlay(self, overlay: MapOverlay) -> None: self.backend.upsert_overlay(overlay)
    def remove_overlay(self, overlay_id: str) -> None: self.backend.remove_overlay(overlay_id)
    def clear_dynamic_overlays(self) -> None: self.backend.clear_dynamic_overlays()
    def clear_all(self) -> None: self.backend.clear_all()
    def select_overlay(self, overlay_id: str | None) -> None: self.backend.select_overlay(overlay_id)
    def load_state(self, state: MissionState) -> None: self.backend.load_state(state)
    def request_vehicle_sync(self) -> bool: return self.backend.request_vehicle_sync()
    def replace_local_plan(self, overlays, *, mission_loop: bool) -> None:
        self.backend.replace_local_plan(overlays, mission_loop=mission_loop)
    def upsert_contact(self, contact: MapContact) -> None: self.backend.upsert_contact(contact)
    def remove_contact(self, contact_id: str) -> None: self.backend.remove_contact(contact_id)
    def clear_contacts(self) -> None: self.backend.clear_contacts()


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


class QtTargetSelectionService(_QtAdapter):
    """Deliver selector updates to Qt on the GUI thread."""

    updated = pyqtSignal(object)

    def __init__(
        self,
        backend: TargetSelectionService,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.updated, self.updated.emit)

    @property
    def last_result(self) -> TargetSelectionResult:
        return self.backend.last_result

    def set_ranking_enabled(self, enabled: bool) -> None:
        self.backend.set_ranking_enabled(enabled)


class QtOnboardMissionService(_QtAdapter):
    state_changed = pyqtSignal(object)
    event_received = pyqtSignal(object)

    def __init__(
        self,
        backend: OnboardMissionServiceContract,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.changed, self.state_changed.emit)
        self._subscribe(backend.events, self.event_received.emit)

    @property
    def state(self):
        return self.backend.state


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
        self._subscribe(backend.changed, self.status_changed.emit)

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
        self.operation_started.emit(channel)
        return self._start_worker(
            self._execute,
            name=f"QtConnection-{action}-{channel}",
            args=(action, channel, dict(options)),
        )

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

class QtParameterService(_QtAdapter):
    parameters_changed = pyqtSignal(object)
    _UI_UPDATE_INTERVAL_MS = 100

    def __init__(self, backend: ParameterService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._parameter_lock = threading.Lock()
        self._latest_parameters = None
        self._parameter_update_scheduled = False
        self._subscriptions.append(backend.changed.subscribe(self._capture_parameters))
        self._parameter_timer = QTimer(self)
        self._parameter_timer.setSingleShot(True)
        self._parameter_timer.setInterval(self._UI_UPDATE_INTERVAL_MS)
        self._parameter_timer.timeout.connect(self._emit_latest_parameters)

    def _capture_parameters(self, parameters) -> None:
        should_schedule = False
        with self._parameter_lock:
            self._latest_parameters = parameters
            if not self._parameter_update_scheduled:
                self._parameter_update_scheduled = True
                should_schedule = True
        if should_schedule:
            self._post(self._start_parameter_timer)

    @pyqtSlot()
    def _start_parameter_timer(self) -> None:
        if not self._closed and not self._parameter_timer.isActive():
            self._parameter_timer.start()

    @pyqtSlot()
    def _emit_latest_parameters(self) -> None:
        with self._parameter_lock:
            parameters = self._latest_parameters
            self._latest_parameters = None
            self._parameter_update_scheduled = False
        if parameters is not None:
            self.parameters_changed.emit(parameters)

    def close(self) -> None:
        self._parameter_timer.stop()
        with self._parameter_lock:
            self._latest_parameters = None
            self._parameter_update_scheduled = False
        super().close()

    @property
    def current(self): return self.backend.current
    def replace(self, parameters) -> None: self.backend.replace(parameters)
    def upsert(self, parameter) -> None: self.backend.upsert(parameter)
    def update_values(self, changes: dict[str, object]) -> threading.Thread:
        return self._start_worker(
            self._update_values,
            name="QtParameterWrite",
            args=(changes,),
        )

    def refresh(self) -> threading.Thread:
        return self._start_worker(
            self._refresh,
            name="QtParameterRefresh",
        )

    def _update_values(self, changes: dict[str, object]) -> None:
        try:
            self.backend.update_values(changes)
        except Exception:
            # The domain service writes failures to the structured backend log.
            return

    def _refresh(self) -> None:
        try:
            self.backend.refresh()
        except Exception:
            return

class QtVideoService(_QtAdapter):
    frame_ready = pyqtSignal(QImage)
    stats_updated = pyqtSignal(object)
    share_states_changed = pyqtSignal(object)
    _UI_FRAME_INTERVAL_MS = 33

    def __init__(
        self,
        backend: VideoService,
        *,
        mock_mode: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self._mock_mode = mock_mode
        self._last_snapshot: TelemetrySnapshot | None = None
        self._frame_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._emit_mock_frame)
        self._latest_frame_lock = threading.Lock()
        self._latest_frame: VideoFrame | None = None
        self._video_frame_scheduled = False
        self._video_frame_timer = QTimer(self)
        self._video_frame_timer.setSingleShot(True)
        self._video_frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._video_frame_timer.setInterval(self._UI_FRAME_INTERVAL_MS)
        self._video_frame_timer.timeout.connect(self._emit_latest_frame)
        # Do not enqueue every camera frame in Qt. A camera can outpace the GUI
        # briefly; retaining only its newest frame keeps latency and memory
        # bounded without throttling the capture pipeline.
        self._subscriptions.append(
            backend.frames.subscribe(self._capture_latest_frame)
        )
        self._subscribe(backend.stats_changed, self.stats_updated.emit)
        share_states_changed = getattr(backend, "share_states_changed", None)
        if share_states_changed is not None:
            self._subscribe(share_states_changed, self.share_states_changed.emit)

    @property
    def connected(self) -> bool:
        return self.backend.connected

    @property
    def stats(self):
        return self.backend.stats

    @property
    def share_states(self):
        return getattr(self.backend, "share_states", ())

    def configure_shares(self, configs: tuple[VideoShareConfig, ...]) -> None:
        configure = getattr(self.backend, "configure_shares", None)
        if configure is not None:
            configure(configs)

    def start(self) -> None:
        if self._mock_mode:
            self._timer.start(66)

    def stop(self) -> None:
        self._timer.stop()
        self._video_frame_timer.stop()
        with self._latest_frame_lock:
            self._latest_frame = None
            self._video_frame_scheduled = False

    @pyqtSlot(object)
    def consume_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._last_snapshot = snapshot

    def _capture_latest_frame(self, frame: VideoFrame) -> None:
        should_schedule = False
        with self._latest_frame_lock:
            self._latest_frame = frame
            if not self._video_frame_scheduled:
                self._video_frame_scheduled = True
                should_schedule = True
        if should_schedule:
            self._post(self._start_video_frame_timer)

    @pyqtSlot()
    def _start_video_frame_timer(self) -> None:
        if not self._closed and not self._video_frame_timer.isActive():
            self._video_frame_timer.start()

    @pyqtSlot()
    def _emit_latest_frame(self) -> None:
        with self._latest_frame_lock:
            frame = self._latest_frame
            self._latest_frame = None
            self._video_frame_scheduled = False
        if frame is not None:
            self.frame_ready.emit(self._image_from_frame(frame))

        should_schedule = False
        with self._latest_frame_lock:
            if (
                not self._closed
                and self._latest_frame is not None
                and not self._video_frame_scheduled
            ):
                self._video_frame_scheduled = True
                should_schedule = True
        if should_schedule:
            self._post(self._start_video_frame_timer)

    @staticmethod
    def _image_from_frame(frame: VideoFrame) -> QImage:
        formats = {
            "rgba8888": QImage.Format.Format_RGBA8888,
            "rgb888": QImage.Format.Format_RGB888,
            "bgr888": QImage.Format.Format_BGR888,
            "gray8": QImage.Format.Format_Grayscale8,
        }
        bytes_per_pixel = {"rgba8888": 4, "rgb888": 3, "bgr888": 3, "gray8": 1}
        stride = frame.stride or frame.width * bytes_per_pixel[frame.pixel_format]
        return QImage(
            frame.data,
            frame.width,
            frame.height,
            stride,
            formats[frame.pixel_format],
        ).copy()

    def close(self) -> None:
        self._timer.stop()
        self._video_frame_timer.stop()
        with self._latest_frame_lock:
            self._latest_frame = None
            self._video_frame_scheduled = False
        super().close()

    def _emit_mock_frame(self) -> None:
        width, height = 1280, 720
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#05080c"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        snapshot = self._last_snapshot
        horizon_shift = int(snapshot.pitch * 5.0) if snapshot else 0
        heading = snapshot.heading if snapshot else 0.0
        mode = snapshot.flight_mode if snapshot else "LINK WAIT"
        battery = f"{snapshot.battery_percent}%" if snapshot else "--"
        painter.fillRect(0, 0, width, height // 2 + horizon_shift, QColor("#23384d"))
        painter.fillRect(0, height // 2 + horizon_shift, width, height // 2 - horizon_shift, QColor("#4a3624"))
        painter.setOpacity(0.18)
        for index in range(0, width, 80):
            painter.fillRect(index, 0, 1, height, QColor("#94bde5"))
        painter.setOpacity(1.0)
        painter.setPen(QPen(QColor("#8cd2ff"), 2))
        crosshair = QPainterPath()
        crosshair.moveTo(width / 2 - 50, height / 2)
        crosshair.lineTo(width / 2 + 50, height / 2)
        crosshair.moveTo(width / 2, height / 2 - 50)
        crosshair.lineTo(width / 2, height / 2 + 50)
        painter.drawPath(crosshair)
        painter.setPen(QPen(QColor("#f4b942"), 2))
        for index in range(-3, 4):
            y = height / 2 + horizon_shift + index * 40
            painter.drawLine(int(width / 2 - 90), int(y), int(width / 2 - 40), int(y))
            painter.drawLine(int(width / 2 + 40), int(y), int(width / 2 + 90), int(y))
        painter.setPen(QPen(QColor("#dce8f4"), 1))
        painter.setFont(QFont("Noto Sans", 10, QFont.Weight.Bold))
        timestamp = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-4]
        painter.drawText(20, 28, f"Kirlangic Team / CAM-A / {timestamp}")
        painter.drawText(20, 54, f"MODE {mode}")
        painter.drawText(20, 80, f"BAT {battery}")
        painter.drawText(20, 106, f"HDG {heading:05.1f}")
        painter.drawText(width - 150, 28, "LOW-LATENCY")
        painter.drawText(width - 150, 54, "H.265 / AUTO")
        painter.drawText(width - 150, 80, "STREAM OK")
        painter.setPen(QPen(QColor(255, 255, 255, 24), 1))
        for row in range(self._frame_index % 12, height, 24):
            painter.drawLine(0, row, width, row)
        painter.end()
        self._frame_index += 1
        self.frame_ready.emit(image)


class QtTileCacheService(_QtAdapter):
    cache_changed = pyqtSignal(int)

    def __init__(self, backend: TileCacheServer, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.cache_changed, self.cache_changed.emit)

    @property
    def base_url(self) -> str: return self.backend.base_url
    @property
    def index_path(self): return self.backend.index_path
    def configure(self, **kwargs) -> None: self.backend.configure(**kwargs)
    def start(self) -> None: self.backend.start()
    def stop(self) -> None: self.backend.stop()
    def clear(self) -> None: self.backend.clear()
    def count_tiles(self) -> int: return self.backend.count_tiles()
    def entries(self): return self.backend.entries()
