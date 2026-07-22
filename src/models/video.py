from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class VideoStreamStats(Model):
    connected: bool = False
    quality_percent: int = 0
    frames_received: int = 0
    fps: float = 0.0
    bitrate_kbps: float = 0.0
    width: int = 0
    height: int = 0
    frame_age_s: float | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.quality_percent <= 100:
            raise ValueError("Video kalite yüzdesi 0..100 aralığında olmalı")
        if self.frames_received < 0 or self.fps < 0.0 or self.bitrate_kbps < 0.0:
            raise ValueError("Video akış sayaçları negatif olamaz")
        if self.width < 0 or self.height < 0:
            raise ValueError("Video akış boyutları negatif olamaz")

    def to_dict(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "quality_percent": self.quality_percent,
            "frames_received": self.frames_received,
            "fps": self.fps,
            "bitrate_kbps": self.bitrate_kbps,
            "width": self.width,
            "height": self.height,
            "frame_age_s": self.frame_age_s,
        }


@dataclass(frozen=True, slots=True)
class VideoFrame(Model):
    width: int
    height: int
    data: bytes
    pixel_format: str = "rgba8888"
    stride: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Video frame boyutları pozitif olmalı")
        if self.pixel_format not in {"rgba8888", "rgb888", "bgr888", "gray8"}:
            raise ValueError(f"Desteklenmeyen pixel formatı: {self.pixel_format}")

    def to_dict(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "pixel_format": self.pixel_format,
            "stride": self.stride,
            "timestamp": self.timestamp.isoformat(),
            "data_size": len(self.data),
        }


@dataclass(frozen=True, slots=True)
class QrDetection(Model):
    """Bir video karesinde takip edilen QR alanı ve çözülmüş içeriği."""

    track_id: str
    points: tuple[tuple[float, float], ...]
    decoded: bool = False
    data: str = ""
    submitted: bool = False

    def __post_init__(self) -> None:
        if not self.track_id.strip():
            raise ValueError("QR takip kimliği boş olamaz")
        if len(self.points) < 4:
            raise ValueError("QR alanı en az dört köşe içermeli")
        if self.decoded and not self.data:
            raise ValueError("Okunmuş QR verisi boş olamaz")
        if self.submitted and not self.decoded:
            raise ValueError("Gönderilmiş QR önce okunmuş olmalı")

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "points": [list(point) for point in self.points],
            "decoded": self.decoded,
            "data": self.data,
            "submitted": self.submitted,
        }


@dataclass(frozen=True, slots=True)
class QrDetectionSnapshot(Model):
    detections: tuple[QrDetection, ...] = ()
    processed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def decoded(self) -> tuple[QrDetection, ...]:
        return tuple(detection for detection in self.detections if detection.decoded)

    def to_dict(self) -> dict[str, object]:
        return {
            "detections": [detection.to_dict() for detection in self.detections],
            "processed_at": self.processed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class PhoneCameraBridgeState(Model):
    enabled: bool = False
    running: bool = False
    adb_connected: bool = False
    video_device_ready: bool = False
    serial: str = ""
    video_device: str = "/dev/video20"
    process_id: int | None = None
    last_error: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def ready(self) -> bool:
        return bool(
            self.enabled
            and self.running
            and self.adb_connected
            and self.video_device_ready
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "adb_connected": self.adb_connected,
            "video_device_ready": self.video_device_ready,
            "serial": self.serial,
            "video_device": self.video_device,
            "process_id": self.process_id,
            "last_error": self.last_error,
            "ready": self.ready,
            "updated_at": self.updated_at.isoformat(),
        }
