from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from uuid import uuid4

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
class VideoSourceFrame(Model):
    """A decoded frame tagged with the input profile that produced it."""

    source_id: str
    frame: VideoFrame


@dataclass(frozen=True, slots=True)
class VideoSourceStats(Model):
    """Per-input stream state used by multi-camera UI and routing."""

    source_id: str
    stats: VideoStreamStats


@dataclass(frozen=True, slots=True)
class VideoSourceConfig(Model):
    """One selectable camera/video-link input profile."""

    name: str = "Kamera 1"
    source_type: str = "udp"
    source_uri: str = ""
    codec: str = "h265"
    latency_ms: int = 100
    frame_timeout_s: float = 3.0
    auto_connect: bool = False
    aim_enabled: bool = False
    aim_x: float = 0.5
    aim_y: float = 0.5
    identifier: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        identifier = str(self.identifier).strip()
        name = str(self.name).strip()
        source_type = str(self.source_type).strip().lower()
        source_uri = str(self.source_uri).strip()
        codec = str(self.codec).strip().lower()
        if not identifier or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identifier):
            raise ValueError("Video bağlantı kimliği geçersiz")
        if not name:
            raise ValueError("Video bağlantı adı boş olamaz")
        if source_type not in {"mock", "http", "rtsp", "udp", "usb", "file", "pipeline"}:
            raise ValueError(f"Desteklenmeyen video kaynağı: {source_type}")
        if codec not in {"h264", "h265", "mjpeg"}:
            raise ValueError(f"Desteklenmeyen video codec: {codec}")
        if not 0 <= int(self.latency_ms) <= 10_000:
            raise ValueError("Video gecikmesi 0..10000 ms aralığında olmalı")
        if not 1 <= float(self.frame_timeout_s) <= 60:
            raise ValueError("Video kare zaman aşımı 1..60 saniye aralığında olmalı")
        if not 0.0 <= float(self.aim_x) <= 1.0 or not 0.0 <= float(self.aim_y) <= 1.0:
            raise ValueError("Video nişangâh koordinatları 0..1 aralığında olmalı")
        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "source_uri", source_uri)
        object.__setattr__(self, "codec", codec)
        object.__setattr__(self, "latency_ms", int(self.latency_ms))
        object.__setattr__(self, "frame_timeout_s", float(self.frame_timeout_s))
        object.__setattr__(self, "auto_connect", bool(self.auto_connect))
        object.__setattr__(self, "aim_enabled", bool(self.aim_enabled))
        object.__setattr__(self, "aim_x", float(self.aim_x))
        object.__setattr__(self, "aim_y", float(self.aim_y))


@dataclass(frozen=True, slots=True)
class VideoShareConfig(Model):
    """One camera-feed stream or local recording target.

    ``source_id`` selects a decoded camera profile. ``panel`` follows the
    currently focused panel source. Desktop/screen capture is never accepted.
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
    source_id: str = "panel"
    output_type: str = "stream"

    def __post_init__(self) -> None:
        identifier = str(self.identifier).strip()
        name = str(self.name).strip()
        endpoint = str(self.endpoint).strip()
        connection_type = str(self.connection_type).strip().lower()
        codec = str(self.codec).strip().lower()
        network_interface = str(self.network_interface).strip()
        source_id = str(self.source_id).strip() or "panel"
        output_type = str(self.output_type).strip().lower()
        if not identifier or not name:
            raise ValueError("Video paylaşım adı ve kimliği boş olamaz")
        if output_type not in {"stream", "record"}:
            raise ValueError(f"Desteklenmeyen video çıkış türü: {output_type}")
        if output_type == "stream" and not endpoint:
            raise ValueError("Video paylaşım bağlantı adresi boş olamaz")
        if connection_type not in {"udp", "srt", "rtmp", "rtsp"}:
            raise ValueError(f"Desteklenmeyen video paylaşım türü: {connection_type}")
        if codec not in {"h264", "h264-baseline", "h265"}:
            raise ValueError(f"Desteklenmeyen video paylaşım codec'i: {codec}")
        if not 15 <= int(self.fps) <= 120:
            raise ValueError("Video paylaşım FPS değeri 15..120 aralığında olmalı")
        if output_type == "record" and source_id == "panel":
            raise ValueError("Kayıt çıkışı belirli bir kameraya bağlanmalı")
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
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "output_type", output_type)
        if output_type == "record":
            object.__setattr__(self, "fps", 30)
            object.__setattr__(self, "fixed_frame_rate", True)


@dataclass(frozen=True, slots=True)
class VideoShareState(Model):
    identifier: str
    active: bool = False
    status: str = "inactive"
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"inactive", "waiting", "active", "error"}:
            raise ValueError(f"Geçersiz video paylaşım durumu: {self.status}")
