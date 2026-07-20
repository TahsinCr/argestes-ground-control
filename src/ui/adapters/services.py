from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

from src.core.events import Subscription
from src.models.logs import LogEntry
from src.models.connections import ConnectionStatus
from src.models.mission import MapContact, MapOverlay, MissionState
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.video import VideoFrame
from src.services.contracts.commands import CommandService
from src.services.contracts.connections import ConnectionControlService
from src.services.contracts.mission import MissionService
from src.services.contracts.parameters import ParameterService
from src.services.contracts.telemetry import TelemetryService
from src.services.contracts.video import VideoService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer


class _QtAdapter(QObject):
    _DISPATCH_INTERVAL_MS = 8
    _MAX_DISPATCH_BATCH = 512

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._subscriptions: list[Subscription] = []
        self._pending_events: queue.SimpleQueue[
            tuple[Callable[..., None], tuple[Any, ...]]
        ] = queue.SimpleQueue()
        self._dispatch_timer = QTimer(self)
        self._dispatch_timer.setInterval(self._DISPATCH_INTERVAL_MS)
        self._dispatch_timer.timeout.connect(self._drain_events)
        self._dispatch_timer.start()
        self.destroyed.connect(self._cancel_subscriptions)

    def _subscribe(self, stream, callback: Callable[[Any], None]) -> None:
        """Backend olayını Qt API'sine dokunmadan GUI kuyruğuna aktarır."""

        self._subscriptions.append(
            stream.subscribe(lambda value: self._post(callback, value))
        )

    def _post(self, callback: Callable[..., None], *args: Any) -> None:
        """Worker thread'lerin çağırabileceği saf Python aktarım noktası."""

        self._pending_events.put((callback, args))

    @pyqtSlot()
    def _drain_events(self) -> None:
        """Bekleyen backend olaylarını yalnızca Qt ana thread'inde çalıştırır."""

        for _index in range(self._MAX_DISPATCH_BATCH):
            try:
                callback, args = self._pending_events.get_nowait()
            except queue.Empty:
                break
            callback(*args)

    def close(self) -> None:
        self._cancel_subscriptions()
        self._dispatch_timer.stop()
        while True:
            try:
                self._pending_events.get_nowait()
            except queue.Empty:
                break

    def _cancel_subscriptions(self, *_args: object) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()


class QtLoggingService(_QtAdapter):
    entry_emitted = pyqtSignal(object)

    def __init__(self, backend: LoggingService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.entries, self.entry_emitted.emit)

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
    settings_changed = pyqtSignal(object)
    cache_cleared = pyqtSignal()

    def __init__(self, backend: SettingsService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.changed, self.settings_changed.emit)
        self._subscribe(backend.cache_cleared, lambda _value: self.cache_cleared.emit())

    @property
    def current(self) -> SettingsModel: return self.backend.current
    def save(self, settings: SettingsModel) -> None: self.backend.save(settings)
    def patch(self, **changes: object) -> None: self.backend.patch(**changes)
    def set_value(self, path: str, value: object) -> None: self.backend.set_value(path, value)
    def clear_cache(self) -> None: self.backend.clear_cache()


class QtTelemetryService(_QtAdapter):
    telemetry_updated = pyqtSignal(object)

    def __init__(self, backend: TelemetryService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.updated, self.telemetry_updated.emit)

    @property
    def current(self) -> TelemetrySnapshot: return self.backend.current
    def start(self) -> None: self.backend.start()
    def stop(self) -> None: self.backend.stop()
    def push(self, snapshot: TelemetrySnapshot) -> None: self.backend.push(snapshot)


class QtMissionService(_QtAdapter):
    mission_state_changed = pyqtSignal(object)

    def __init__(self, backend: MissionService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.changed, self.mission_state_changed.emit)

    @property
    def state(self) -> MissionState: return self.backend.state
    @property
    def api(self): return getattr(self.backend, "api", None)
    def start(self) -> None: self.backend.start()
    def stop(self) -> None: self.backend.stop()
    def set_default_radii(self, values: dict) -> None: self.backend.set_default_radii(values)
    def set_base_layer(self, layer: str) -> None: self.backend.set_base_layer(layer)
    def set_active_tool(self, tool: str) -> None: self.backend.set_active_tool(tool)
    def set_overlays(self, overlays) -> None: self.backend.set_overlays(overlays)
    def replace_source_overlays(self, source: str, overlays) -> None:
        self.backend.replace_source_overlays(source, overlays)
    def upsert_overlay(self, overlay: MapOverlay) -> None: self.backend.upsert_overlay(overlay)
    def remove_overlay(self, overlay_id: str) -> None: self.backend.remove_overlay(overlay_id)
    def clear_dynamic_overlays(self) -> None: self.backend.clear_dynamic_overlays()
    def clear_all(self) -> None: self.backend.clear_all()
    def select_overlay(self, overlay_id: str | None) -> None: self.backend.select_overlay(overlay_id)
    def load_state(self, state: MissionState) -> None: self.backend.load_state(state)
    def upsert_contact(self, contact: MapContact) -> None: self.backend.upsert_contact(contact)
    def remove_contact(self, contact_id: str) -> None: self.backend.remove_contact(contact_id)
    def clear_contacts(self) -> None: self.backend.clear_contacts()


class QtCommandService(_QtAdapter):
    command_dispatched = pyqtSignal(str, dict)
    command_completed = pyqtSignal(object)

    def __init__(self, backend: CommandService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(
            backend.dispatched,
            lambda request: self.command_dispatched.emit(request.code, request.parameters),
        )
        self._subscribe(backend.completed, self.command_completed.emit)

    @property
    def available_tasks(self) -> tuple[str, ...]:
        return self.backend.available_tasks

    def issue_command(self, command: str, params: dict | None = None):
        thread = threading.Thread(
            target=self.backend.issue_command,
            args=(command, params),
            kwargs={"source": "qt-ui"},
            name=f"QtCommand-{command}",
            daemon=True,
        )
        thread.start()
        return thread


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
        thread = threading.Thread(
            target=self._execute,
            args=(action, channel, dict(options)),
            name=f"QtConnection-{action}-{channel}",
            daemon=True,
        )
        thread.start()
        return thread

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

    def __init__(self, backend: ParameterService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.changed, self.parameters_changed.emit)

    @property
    def current(self): return self.backend.current
    def replace(self, parameters) -> None: self.backend.replace(parameters)
    def upsert(self, parameter) -> None: self.backend.upsert(parameter)
    def update_values(self, changes: dict[str, object]) -> threading.Thread:
        thread = threading.Thread(
            target=self._update_values,
            args=(changes,),
            name="QtParameterWrite",
            daemon=True,
        )
        thread.start()
        return thread
    def refresh(self) -> threading.Thread:
        thread = threading.Thread(
            target=self._refresh,
            name="QtParameterRefresh",
            daemon=True,
        )
        thread.start()
        return thread

    def _update_values(self, changes: dict[str, object]) -> None:
        try:
            self.backend.update_values(changes)
        except Exception:
            # Alan servisi başarısızlığı yapılandırılmış backend loguna yazar.
            return

    def _refresh(self) -> None:
        try:
            self.backend.refresh()
        except Exception:
            return

class QtVideoService(_QtAdapter):
    frame_ready = pyqtSignal(QImage)

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
        self._subscribe(backend.frames, self._on_frame)

    def start(self) -> None:
        if self._mock_mode:
            self._timer.start(66)

    def stop(self) -> None:
        self._timer.stop()

    @pyqtSlot(object)
    def consume_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._last_snapshot = snapshot

    def _on_frame(self, frame: VideoFrame) -> None:
        formats = {
            "rgba8888": QImage.Format.Format_RGBA8888,
            "rgb888": QImage.Format.Format_RGB888,
            "bgr888": QImage.Format.Format_BGR888,
            "gray8": QImage.Format.Format_Grayscale8,
        }
        bytes_per_pixel = {"rgba8888": 4, "rgb888": 3, "bgr888": 3, "gray8": 1}
        stride = frame.stride or frame.width * bytes_per_pixel[frame.pixel_format]
        image = QImage(frame.data, frame.width, frame.height, stride, formats[frame.pixel_format]).copy()
        self.frame_ready.emit(image)

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
