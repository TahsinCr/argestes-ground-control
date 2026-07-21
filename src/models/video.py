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
