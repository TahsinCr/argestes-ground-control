"""Camera-only settings and capture/output lifecycle."""

from __future__ import annotations

import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.extension.api import ClockQuality, ClockReferenceRegistry
from src.core.events import Subscription
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService

from .capture import MultiVideoService
from .models import VideoShareConfig, VideoSourceConfig, VideoSourceStats


@dataclass(slots=True)
class _ConnectionAttempt:
    ready: threading.Event
    cancelled: bool = False
    started: bool = False
    cancel_requested: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True, slots=True)
class CameraConfiguration:
    sources: tuple[VideoSourceConfig, ...]
    active_source_id: str
    outputs: tuple[VideoShareConfig, ...]
    clock_reference_id: str = "system.local"

    def __post_init__(self) -> None:
        identifier = str(self.clock_reference_id).strip() or "system.local"
        object.__setattr__(self, "clock_reference_id", identifier)


class CameraSettingsStore:
    def __init__(self, base_dir: Path, settings: SettingsService | None = None) -> None:
        self._settings = settings or SettingsService(base_dir)
        self.path = base_dir / "data" / "settings.json"
        self._lock = threading.RLock()
        self._current = self._read()

    @property
    def current(self) -> CameraConfiguration:
        with self._lock:
            return self._current

    def _read(self) -> CameraConfiguration:
        payload = self._settings.extension_config("camera")
        sources = tuple(
            VideoSourceConfig(**dict(item))
            for item in payload.get("video_sources", ())
        )
        if not sources:
            sources = (
                VideoSourceConfig(
                    identifier="video-primary",
                    name="Kamera 1",
                    source_type="usb",
                    source_uri="/dev/video0",
                    codec="mjpeg",
                    auto_connect=False,
                ),
            )
        outputs = tuple(
            VideoShareConfig(**dict(item)) for item in payload.get("video_shares", ())
        )
        identifiers = [item.identifier for item in sources]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate camera source identifiers")
        output_ids = [item.identifier for item in outputs]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("Duplicate camera output identifiers")
        selected = str(payload.get("active_video_source_id", sources[0].identifier))
        if selected not in identifiers:
            selected = sources[0].identifier
        return CameraConfiguration(
            sources,
            selected,
            outputs,
            str(payload.get("clock_reference_id", "system.local")),
        )

    def save(self, configuration: CameraConfiguration) -> None:
        payload = {
            "video_sources": [asdict(item) for item in configuration.sources],
            "active_video_source_id": configuration.active_source_id,
            "video_shares": [asdict(item) for item in configuration.outputs],
            "clock_reference_id": configuration.clock_reference_id,
        }
        # Validate before touching the last known-good file.
        checked = CameraConfiguration(
            tuple(VideoSourceConfig(**item) for item in payload["video_sources"]),
            str(payload["active_video_source_id"]),
            tuple(VideoShareConfig(**item) for item in payload["video_shares"]),
            str(payload["clock_reference_id"]),
        )
        if not checked.sources or checked.active_source_id not in {
            item.identifier for item in checked.sources
        }:
            raise ValueError("Active camera must be one of the configured sources")
        with self._lock:
            self._settings.update_extension("camera", config=payload)
            self._current = checked


class CameraRuntime:
    """Serialize capture reconfiguration away from the Qt thread."""

    def __init__(
        self,
        base_dir: Path,
        logging: LoggingService,
        settings: SettingsService | None = None,
        clock_references: ClockReferenceRegistry | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.settings = CameraSettingsStore(base_dir, settings)
        self.clock_references = clock_references
        config = self.settings.current
        self._clock_reference_id = config.clock_reference_id
        self.video = MultiVideoService(
            config.sources, config.active_source_id, logging.child("VIDEO")
        )
        self.video.set_clock_provider(self._current_clock_time)
        self.video.configure_shares(config.outputs)
        self._worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="CameraConfig"
        )
        self._connection_worker = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="CameraConnect"
        )
        self._connection_attempts: dict[str, _ConnectionAttempt] = {}
        self._video_subscription: Subscription = self.video.source_stats_changed.subscribe(
            self._source_stats_changed
        )
        self._started = False
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            self._started = True
        config = self.settings.current
        if any(item.auto_connect for item in config.sources) or any(
            item.enabled for item in config.outputs
        ):
            self._worker.submit(self.video.start)

    def stop(self) -> None:
        with self._lock:
            self._started = False
            for attempt in self._connection_attempts.values():
                attempt.cancelled = True
                attempt.cancel_requested.set()
                attempt.ready.set()
        self._connection_worker.shutdown(wait=True, cancel_futures=True)
        self._worker.shutdown(wait=True, cancel_futures=True)
        self._video_subscription.cancel()
        if not self.video.close():
            raise RuntimeError("One or more camera output workers did not shut down")

    def save_sources(
        self, sources: tuple[VideoSourceConfig, ...], active_id: str
    ) -> Future[None]:
        if not sources or active_id not in {item.identifier for item in sources}:
            raise ValueError("Active camera must be one of the configured sources")
        previous = self.settings.current
        config = CameraConfiguration(
            sources,
            active_id,
            previous.outputs,
            previous.clock_reference_id,
        )
        self.settings.save(config)
        return self._worker.submit(self._apply_sources, config)

    def _apply_sources(self, config: CameraConfiguration) -> None:
        self.video.configure_sources(config.sources, config.active_source_id)
        if self._started and any(item.auto_connect for item in config.sources):
            self.video.start()

    def save_outputs(self, outputs: tuple[VideoShareConfig, ...]) -> None:
        previous = self.settings.current
        config = CameraConfiguration(
            previous.sources,
            previous.active_source_id,
            outputs,
            previous.clock_reference_id,
        )
        for output in config.outputs:
            output.validate_output_compatibility()
        self.settings.save(config)
        self._worker.submit(self._apply_outputs, config)

    def save_clock_reference(self, identifier: str) -> None:
        previous = self.settings.current
        reference_id = str(identifier).strip()
        if not reference_id:
            raise ValueError("A camera clock reference must be selected")
        config = CameraConfiguration(
            previous.sources,
            previous.active_source_id,
            previous.outputs,
            reference_id,
        )
        self.settings.save(config)
        self._clock_reference_id = reference_id
        self.video.set_clock_provider(self._current_clock_time)

    def _current_clock_time(self) -> datetime | None:
        if self.clock_references is None:
            return datetime.now().astimezone()
        status = self.clock_references.status(self._clock_reference_id)
        if status.quality in {ClockQuality.UNAVAILABLE, ClockQuality.STALE}:
            return None
        return status.value

    def _apply_outputs(self, config: CameraConfiguration) -> None:
        self.video.configure_shares(config.outputs)
        if self._started and any(item.enabled for item in config.outputs):
            self.video.start()

    def record_all(self, enabled: bool) -> None:
        from dataclasses import replace

        config = self.settings.current
        outputs = list(config.outputs)
        known = {item.source_id for item in outputs if item.output_type == "record"}
        if enabled:
            for source in config.sources:
                if source.identifier not in known:
                    outputs.append(
                        VideoShareConfig(
                            identifier=uuid4().hex,
                            name=f"{source.name} Kaydı",
                            endpoint=str(self.base_dir / "data" / "recordings"),
                            connection_type="udp",
                            codec="h264",
                            fps=30,
                            enabled=True,
                            timestamp_enabled=False,
                            fixed_frame_rate=True,
                            source_id=source.identifier,
                            output_type="record",
                        )
                    )
        outputs = [
            replace(item, enabled=enabled) if item.output_type == "record" else item
            for item in outputs
        ]
        self.save_outputs(tuple(outputs))

    def connect_source(self, identifier: str) -> Future[None]:
        config = self.settings.current
        source = next(
            (item for item in config.sources if item.identifier == identifier), None
        )
        if source is None:
            raise ValueError("Unknown camera source")
        if source.source_type != "mock" and not source.source_uri:
            raise ValueError(f"{source.name}: video source address is empty")
        with self._lock:
            if identifier in self._connection_attempts:
                raise RuntimeError("Camera connection is already in progress")
            attempt = _ConnectionAttempt(threading.Event())
            self._connection_attempts[identifier] = attempt
        try:
            apply_future = (
                self.save_sources(config.sources, identifier)
                if identifier != config.active_source_id
                else None
            )
            return self._connection_worker.submit(
                self._connect_source, identifier, source, attempt, apply_future
            )
        except Exception:
            self._finish_connection_attempt(identifier, attempt)
            raise

    def cancel_source_connection(self, identifier: str) -> bool:
        with self._lock:
            attempt = self._connection_attempts.get(identifier)
            connected = any(
                item.source_id == identifier and item.stats.connected
                for item in self.video.source_stats
            )
            connecting = any(
                item.source_id == identifier and item.stats.connecting
                for item in self.video.source_stats
            )
            if attempt is not None:
                attempt.cancelled = True
                attempt.cancel_requested.set()
                attempt.ready.set()
            elif not connected and not connecting:
                return False
        # Queue capture changes behind pending source configuration; never block
        # the UI on GStreamer state transitions or a configuration lock.
        try:
            self._worker.submit(self.video.disconnect_source, identifier)
        except RuntimeError:
            # The camera extension is already shutting down; its lifecycle
            # stop owns final pipeline cleanup.
            return attempt is not None
        return True

    def _source_stats_changed(self, update: VideoSourceStats) -> None:
        with self._lock:
            attempt = self._connection_attempts.get(update.source_id)
            if attempt is None:
                return
            if update.stats.connected or update.stats.connecting:
                attempt.started = True
            elif attempt.started:
                attempt.ready.set()

    def _finish_connection_attempt(
        self, identifier: str, attempt: _ConnectionAttempt
    ) -> None:
        with self._lock:
            if self._connection_attempts.get(identifier) is attempt:
                self._connection_attempts.pop(identifier, None)

    def _connect_source(
        self,
        identifier: str,
        source: VideoSourceConfig,
        attempt: _ConnectionAttempt,
        apply_future: Future[None] | None,
    ) -> None:
        try:
            if apply_future is not None:
                while not apply_future.done():
                    if attempt.cancel_requested.wait(0.05):
                        raise CancelledError()
                if attempt.cancel_requested.is_set():
                    raise CancelledError()
                apply_future.result()
            if attempt.cancelled:
                raise CancelledError()
            self.video.reconnect_source(identifier)
            if attempt.cancelled or attempt.cancel_requested.is_set():
                raise CancelledError()
            if any(
                item.source_id == identifier and item.stats.connected
                for item in self.video.source_stats
            ):
                return
            if not attempt.ready.wait(source.connection_timeout_s):
                self.video.disconnect_source(identifier)
                raise TimeoutError(
                    f"{source.name}: connection timed out after "
                    f"{source.connection_timeout_s:g} s"
                )
            if attempt.cancelled:
                raise CancelledError()
            if not any(
                item.source_id == identifier and item.stats.connected
                for item in self.video.source_stats
            ):
                raise TimeoutError(f"{source.name}: video source did not connect")
        except CancelledError:
            self.video.disconnect_source(identifier)
            raise
        finally:
            self._finish_connection_attempt(identifier, attempt)
