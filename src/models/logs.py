from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class LogEntry:
    source: str
    message: str
    severity: str = "info"
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_row(self) -> tuple[str, str, str, str]:
        return (
            self.timestamp.strftime("%H:%M:%S"),
            self.severity.upper(),
            self.source,
            self.message,
        )
