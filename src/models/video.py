from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.core.abstracts import Model


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
