from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from src.core.abstracts import Model


TERMINAL_MISSION_PHASES = frozenset({"stopped", "succeeded", "failed", "cancelled"})
QR_EVENT_TYPES = frozenset({"QR_DECODED", "QR_DECODED_DURING_PULL_UP"})
CRITICAL_KAMIKAZE_EVENTS = frozenset({"SAFETY_FLOOR_BREACH", "FAULT", "GROUND_CONTACT"})


@dataclass(frozen=True, slots=True)
class OnboardMissionEvent(Model):
    event: str
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    time_utc: str = ""
    mission_id: int | None = None
    sequence: int | None = None
    source: str = "live"
    qr_text: str | None = None
    altitude_m: float | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event", str(self.event).strip().upper() or "UNKNOWN")
        object.__setattr__(self, "source", str(self.source).strip().lower() or "live")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        if self.qr_text is not None:
            # QR is inert application data. Keep it bounded and never interpret it.
            object.__setattr__(self, "qr_text", str(self.qr_text)[:4096])

    @property
    def critical(self) -> bool:
        return self.event in CRITICAL_KAMIKAZE_EVENTS

    @property
    def deduplication_key(self) -> tuple[object, ...]:
        if self.event in QR_EVENT_TYPES:
            return (
                self.mission_id,
                self.event,
                self.time_utc,
                self.qr_text,
                self.altitude_m,
            )
        if self.sequence is not None:
            return (self.mission_id, self.sequence)
        return (
            self.mission_id,
            self.event,
            self.time_utc,
            self.altitude_m,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "received_at": self.received_at,
            "time_utc": self.time_utc,
            "mission_id": self.mission_id,
            "sequence": self.sequence,
            "source": self.source,
            "qr_text": self.qr_text,
            "altitude_m": self.altitude_m,
            "fields": dict(self.fields),
        }


@dataclass(frozen=True, slots=True)
class OnboardMissionState(Model):
    connected: bool = False
    onboard_version: str = ""
    vehicle_mode: str = ""
    active_task: str = ""
    queued_tasks: tuple[str, ...] = field(default_factory=tuple)
    mission_id: int | None = None
    phase: str = "idle"
    waiting_for_guided: bool = False
    pending_command: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    result_received: bool = False
    qr_status: str = "pending"
    qr_text: str | None = None
    qr_event: str = ""
    qr_time_utc: str = ""
    qr_conflicts: tuple[str, ...] = field(default_factory=tuple)
    target_ready: bool = False
    target_revision: int = 0
    target: dict[str, Any] = field(default_factory=dict)
    critical_event: str = ""
    timeline: tuple[OnboardMissionEvent, ...] = field(default_factory=tuple)
    last_error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "result", MappingProxyType(dict(self.result)))
        object.__setattr__(self, "target", MappingProxyType(dict(self.target)))

    @property
    def terminal(self) -> bool:
        return self.phase in TERMINAL_MISSION_PHASES

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "onboard_version": self.onboard_version,
            "vehicle_mode": self.vehicle_mode,
            "active_task": self.active_task,
            "queued_tasks": list(self.queued_tasks),
            "mission_id": self.mission_id,
            "phase": self.phase,
            "waiting_for_guided": self.waiting_for_guided,
            "pending_command": self.pending_command,
            "result": dict(self.result),
            "result_received": self.result_received,
            "qr_status": self.qr_status,
            "qr_text": self.qr_text,
            "qr_event": self.qr_event,
            "qr_time_utc": self.qr_time_utc,
            "qr_conflicts": list(self.qr_conflicts),
            "target_ready": self.target_ready,
            "target_revision": self.target_revision,
            "target": dict(self.target),
            "critical_event": self.critical_event,
            "timeline": [event.to_dict() for event in self.timeline],
            "last_error": self.last_error,
        }
