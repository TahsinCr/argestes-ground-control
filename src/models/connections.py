from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class ConnectionStatus(Model):
    """Bir bağlantı kanalının arayüzden bağımsız anlık durumu."""

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
