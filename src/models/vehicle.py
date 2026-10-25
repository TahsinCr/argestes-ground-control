from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class VehicleConnectionConfig:
    endpoint: str
    heartbeat_timeout_s: float
    baud: int = 115200
    options: dict[str, Any] = field(default_factory=dict)

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
    round_trip_ms: float | None = None

    def __post_init__(self) -> None:
        counts = (
            self.received_packets,
            self.sent_packets,
            self.estimated_dropped_packets,
        )
        if any(int(value) < 0 for value in counts):
            raise ValueError("Taşıyıcı paket sayaçları negatif olamaz")
        if (
            self.delivery_quality is not None
            and not 0 <= int(self.delivery_quality) <= 100
        ):
            raise ValueError("Paket teslim kalitesi 0..100 aralığında olmalı")
        if self.round_trip_ms is not None and (
            not math.isfinite(float(self.round_trip_ms)) or self.round_trip_ms < 0.0
        ):
            raise ValueError(
                "Bağlantı gidiş-dönüş süresi negatif veya sonlu olmayan değer olamaz"
            )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VehicleCapabilities(Model):
    """Capabilities advertised by the active vehicle adapter.

    ``command_codes`` intentionally represents protocol operations, while
    ``vehicle_modes`` is populated from the connected autopilot.  Consumers
    must treat an empty capability set as "not yet known", rather than
    guessing from a vehicle type number.
    """

    command_codes: tuple[str, ...] = ()
    vehicle_modes: tuple[str, ...] = ()
    vehicle_type: int | None = None
    autopilot_type: int | None = None

    def __post_init__(self) -> None:
        commands = tuple(
            dict.fromkeys(
                str(code).strip().upper()
                for code in self.command_codes
                if str(code).strip()
            )
        )
        modes = tuple(
            dict.fromkeys(
                str(mode).strip().upper()
                for mode in self.vehicle_modes
                if str(mode).strip()
            )
        )
        object.__setattr__(self, "command_codes", commands)
        object.__setattr__(self, "vehicle_modes", modes)

    def supports(self, code: str) -> bool:
        return code.strip().upper() in self.command_codes


@dataclass(frozen=True, slots=True)
class VehicleConnectionState(Model):
    connected: bool
    endpoint: str
    system_id: int = 0
    component_id: int = 0
    vehicle_type: int | None = None
    autopilot_type: int | None = None
    protocol: str = ""
    identity: str = ""

    @property
    def device_id(self) -> str:
        if self.identity:
            return self.identity
        if self.protocol and (self.system_id or self.component_id):
            return f"{self.protocol}:{self.system_id}:{self.component_id}"
        if not self.connected:
            return "disconnected"
        return f"{self.protocol or 'vehicle'}:{self.endpoint}"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
