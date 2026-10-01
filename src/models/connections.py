from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class ConnectionProfile:
    identifier: str
    name: str
    endpoint: str
    baud: int = 115200
    heartbeat_timeout: int = 10
    auto_connect: bool = False

    def to_dict(self):
        from dataclasses import asdict
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConnectionStatus(Model):
    """UI-independent current state of a connection channel."""

    channel: str
    connected: bool
    summary: str
    detail: str = ""
    endpoint: str = ""
    timeout_s: float = 0.0
    options: dict[str, Any] = field(default_factory=dict)
    can_disconnect: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "connected": self.connected,
            "summary": self.summary,
            "detail": self.detail,
            "endpoint": self.endpoint,
            "timeout_s": self.timeout_s,
            "options": dict(self.options),
            "can_disconnect": self.can_disconnect,
        }
