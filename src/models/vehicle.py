from __future__ import annotations

from dataclasses import asdict, dataclass

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class VehicleConnectionConfig:
    endpoint: str
    heartbeat_timeout_s: float
    baud: int = 115200

    def __post_init__(self) -> None:
        if not self.endpoint.strip():
            raise ValueError("Araç bağlantı adresi boş olamaz")
        if self.heartbeat_timeout_s <= 0:
            raise ValueError("Heartbeat zaman aşımı pozitif olmalı")
        if self.baud <= 0:
            raise ValueError("Baud değeri pozitif olmalı")


@dataclass(frozen=True, slots=True)
class VehicleTransportStats(Model):
    """UI-independent packet health for the active vehicle transport."""

    received_packets: int = 0
    sent_packets: int = 0
    estimated_dropped_packets: int = 0
    delivery_quality: int | None = None

    def __post_init__(self) -> None:
        counts = (
            self.received_packets,
            self.sent_packets,
            self.estimated_dropped_packets,
        )
        if any(int(value) < 0 for value in counts):
            raise ValueError("Taşıyıcı paket sayaçları negatif olamaz")
        if self.delivery_quality is not None and not 0 <= int(self.delivery_quality) <= 100:
            raise ValueError("Paket teslim kalitesi 0..100 aralığında olmalı")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VehicleConnectionState(Model):
    connected: bool
    endpoint: str
    system_id: int = 0
    component_id: int = 0
    vehicle_type: int | None = None
    autopilot_type: int | None = None

    @property
    def device_id(self) -> str:
        if self.system_id or self.component_id:
            return f"mavlink:{self.system_id}:{self.component_id}"
        return "disconnected"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
