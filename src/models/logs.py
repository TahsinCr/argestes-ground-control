from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.core.abstracts import Model


@dataclass(slots=True)
class LogEntry(Model):
    source: str
    message: str
    severity: str = "info"
    action: str = "service.event"
    details: dict[str, Any] = field(default_factory=dict)
    device_id: str | None = None
    correlation_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    extension_id: str | None = None

    def __post_init__(self) -> None:
        self.source = self.source.strip().upper() or "SYSTEM"
        self.message = self.message.strip()
        self.severity = self.severity.strip().lower() or "info"
        self.action = self.action.strip().lower() or "service.event"
        self.details = dict(self.details)

    def to_row(self) -> tuple[str, str, str, str]:
        return (
            self.timestamp.strftime("%H:%M:%S"),
            self.severity.upper(),
            self.source,
            self.message,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "message": self.message,
            "severity": self.severity,
            "action": self.action,
            "details": self.details,
            "device_id": self.device_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
            "extension_id": self.extension_id,
        }
