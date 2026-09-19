from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import time
from types import MappingProxyType
from typing import Any

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class OrinLinkConfig(Model):
    enabled: bool = False
    system_id: int = 1
    component_id: int = 191
    network_id: int = 77
    message_type: int = 42_000
    heartbeat_timeout_s: float = 10.0
    command_timeout_s: float = 50.0

    def __post_init__(self) -> None:
        if not 1 <= int(self.system_id) <= 255:
            raise ValueError("Orin system ID 1..255 aralığında olmalı")
        if not 0 <= int(self.component_id) <= 255:
            raise ValueError("Orin component ID 0..255 aralığında olmalı")
        if not 0 <= int(self.network_id) <= 255:
            raise ValueError("Orin ağ ID 0..255 aralığında olmalı")
        if not 32_768 <= int(self.message_type) <= 65_535:
            raise ValueError("Orin yerel message type 32768..65535 aralığında olmalı")
        if float(self.heartbeat_timeout_s) <= 0 or float(self.command_timeout_s) <= 0:
            raise ValueError("Orin zaman aşımları pozitif olmalı")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OrinConnectionState(Model):
    alive: bool = False
    endpoint: str = ""
    system_id: int = 0
    component_id: int = 191
    health: str = "unknown"
    session_id: str = ""
    onboard_time_utc: datetime | None = None
    onboard_time_received_at: datetime | None = None
    active_task: str = ""
    active_tasks: tuple[str, ...] = field(default_factory=tuple)
    available_tasks: tuple[str, ...] = field(default_factory=tuple)
    queued_tasks: tuple[str, ...] = field(default_factory=tuple)
    active_missions: tuple[int, ...] = field(default_factory=tuple)
    queued_missions: tuple[int, ...] = field(default_factory=tuple)
    kamikaze_target_ready: bool = False
    kamikaze_target_revision: int = 0
    vehicle_mode: str = ""
    idle_vehicle_mode: str = ""
    mission_vehicle_mode: str = ""
    remote_log_running: bool = False
    remote_log_queued: int = 0
    remote_log_sent: int = 0
    remote_log_dropped: int = 0
    remote_log_error: str = ""
    version: str = ""
    uptime_s: float = 0.0
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    last_heartbeat_at: datetime | None = None
    last_packet_at: datetime | None = None
    round_trip_ms: float | None = None
    packets_in: int = 0
    packets_out: int = 0
    last_error: str = ""

    @property
    def device_id(self) -> str:
        return f"mavlink:{self.system_id}:{self.component_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OrinCommandReceipt(Model):
    request_id: int
    accepted: bool
    message: str
    response: dict[str, Any] = field(default_factory=dict)
    round_trip_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OrinHandlerResponse(Model):
    """Transport-neutral response produced for an onboard request."""

    payload: dict[str, Any] = field(default_factory=dict)
    message: str = "Command accepted"
    accepted: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "message", str(self.message))
        object.__setattr__(self, "accepted", bool(self.accepted))

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload": dict(self.payload),
            "message": self.message,
            "accepted": self.accepted,
        }


@dataclass(frozen=True, slots=True)
class OrinMessage(Model):
    packet_type: str
    payload: dict[str, Any]
    packet_id: int
    source_system: int = 0
    source_component: int = 0
    received_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_type", self.packet_type.strip().lower())
        object.__setattr__(self, "payload", dict(self.payload))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OrinLogRecord(Model):
    """Transport-neutral onboard log exposed to YKİ application services."""

    sequence: int
    source: str
    action: str
    message: str
    level: str
    emitted_at: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)
    device_id: str | None = None
    correlation_id: str | None = None
    session_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "sequence", int(self.sequence))
        object.__setattr__(self, "source", str(self.source).strip().upper())
        object.__setattr__(self, "action", str(self.action).strip().lower())
        object.__setattr__(self, "message", str(self.message).strip())
        object.__setattr__(self, "level", str(self.level).strip().lower())
        object.__setattr__(self, "emitted_at", float(self.emitted_at))
        object.__setattr__(self, "session_id", str(self.session_id).strip())
        object.__setattr__(
            self,
            "details",
            MappingProxyType(dict(self.details)),
        )

    @property
    def yki_severity(self) -> str:
        return "danger" if self.level in {"error", "critical"} else self.level

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "source": self.source,
            "action": self.action,
            "message": self.message,
            "level": self.level,
            "emitted_at": self.emitted_at,
            "details": dict(self.details),
            "device_id": self.device_id,
            "correlation_id": self.correlation_id,
            "session_id": self.session_id,
        }
