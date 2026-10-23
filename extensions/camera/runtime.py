"""Camera-only settings and capture/output lifecycle."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.extension.api import ClockReferenceRegistry
from src.services.logging_service import LoggingService
from src.services.storage import load_json_file
from src.services.settings import SettingsService

from .capture import MultiVideoService
from .models import VideoShareConfig, VideoSourceConfig


@dataclass(frozen=True, slots=True)
class CameraConfiguration:
    sources: tuple[VideoSourceConfig, ...]
    active_source_id: str
    outputs: tuple[VideoShareConfig, ...]
    clock_reference_id: str = "system.utc"

    def __post_init__(self) -> None:
        identifier = str(self.clock_reference_id).strip() or "system.utc"
        object.__setattr__(self, "clock_reference_id", identifier)


class CameraSettingsStore:
    def __init__(self, base_dir: Path, settings: SettingsService | None = None) -> None:
        self._settings = settings or SettingsService(base_dir)
        self.path = base_dir / "data" / "settings.json"
        self._legacy_path = base_dir / "data" / "settings.json"
        self._lock = threading.RLock()
        self._current = self._read()

    @property
    def current(self) -> CameraConfiguration:
        with self._lock:
            return self._current

    def _read(self) -> CameraConfiguration:
        payload = self._settings.extension_config("camera")
        if not payload:
            legacy = load_json_file(self._legacy_path, {})
            payload = dict(legacy.get("backend", {}))
        raw_sources = payload.get("video_sources", ())
        sources = tuple(VideoSourceConfig(**dict(item)) for item in raw_sources)
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
            str(payload.get("clock_reference_id", "system.utc")),
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
        self._worker.shutdown(wait=True, cancel_futures=True)
        if not self.video.close():
            raise RuntimeError("One or more camera output workers did not shut down")

    def save_sources(
        self, sources: tuple[VideoSourceConfig, ...], active_id: str
    ) -> None:
        if not sources:
            raise ValueError("At least one camera source is required")
        previous = self.settings.current
        config = CameraConfiguration(
            sources,
            active_id,
            previous.outputs,
            previous.clock_reference_id,
        )
        self.settings.save(config)
        self._worker.submit(self._apply_sources, config)

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
            return datetime.now(UTC)
        return self.clock_references.current_time(self._clock_reference_id)

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
        if identifier not in {item.identifier for item in config.sources}:
            raise ValueError("Unknown camera source")
        if identifier != config.active_source_id:
            self.save_sources(config.sources, identifier)
        return self._worker.submit(self._connect_source, identifier)

    def _connect_source(self, identifier: str) -> None:
        self.video.set_active_source(identifier)
        self.video.reconnect()
