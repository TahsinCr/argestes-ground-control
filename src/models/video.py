from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re

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
class VideoShareConfig(Model):
    """One camera-feed rebroadcast target.

    The source is deliberately not configurable: every target consumes the
    decoded Video Link frames, so a desktop/screen source cannot be selected.
    """

    identifier: str
    name: str
    endpoint: str
    connection_type: str = "udp"
    codec: str = "h264"
    fps: int = 30
    enabled: bool = True
    timestamp_enabled: bool = True
    fixed_frame_rate: bool = True
    network_interface: str = ""

    def __post_init__(self) -> None:
        identifier = str(self.identifier).strip()
        name = str(self.name).strip()
        endpoint = str(self.endpoint).strip()
        connection_type = str(self.connection_type).strip().lower()
        codec = str(self.codec).strip().lower()
        network_interface = str(self.network_interface).strip()
        if not identifier or not name:
            raise ValueError("Video paylaşım adı ve kimliği boş olamaz")
        if not endpoint:
            raise ValueError("Video paylaşım bağlantı adresi boş olamaz")
        if connection_type not in {"udp", "srt", "rtmp", "rtsp"}:
            raise ValueError(f"Desteklenmeyen video paylaşım türü: {connection_type}")
        if codec not in {"h264", "h264-baseline", "h265"}:
            raise ValueError(f"Desteklenmeyen video paylaşım codec'i: {codec}")
        if not 15 <= int(self.fps) <= 120:
            raise ValueError("Video paylaşım FPS değeri 15..120 aralığında olmalı")
        if network_interface and not re.fullmatch(r"[A-Za-z0-9_.:-]+", network_interface):
            raise ValueError("Video paylaşım ağ arayüzü geçersiz")
        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "connection_type", connection_type)
        object.__setattr__(self, "codec", codec)
        object.__setattr__(self, "fps", int(self.fps))
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "timestamp_enabled", bool(self.timestamp_enabled))
        object.__setattr__(self, "fixed_frame_rate", bool(self.fixed_frame_rate))
        object.__setattr__(self, "network_interface", network_interface)


@dataclass(frozen=True, slots=True)
class VideoShareState(Model):
    identifier: str
    active: bool = False
    status: str = "inactive"
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"inactive", "waiting", "active", "error"}:
            raise ValueError(f"Geçersiz video paylaşım durumu: {self.status}")
