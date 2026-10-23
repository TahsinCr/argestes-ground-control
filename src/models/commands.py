from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class CommandRequest(Model):
    code: str
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = "ui"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", self.code.strip().upper())
        object.__setattr__(self, "parameters", dict(self.parameters))
        if not self.code:
            raise ValueError("Komut kodu boş olamaz")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "parameters": dict(self.parameters),
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CommandResult(Model):
    """Dispatch outcome; acceptance does not confirm the resulting vehicle state."""

    request: CommandRequest
    accepted: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", dict(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "accepted": self.accepted,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class CommandDefinition(Model):
    """Transport-neutral description of an operator command.

    A definition describes *what* the GCS can ask a vehicle to do.  An
    adapter's :class:`VehicleCapabilities` decides whether that request is
    available for the currently connected vehicle/autopilot combination.
    Keeping this data outside Qt and MAVLink avoids a fixed-wing-only command
    panel becoming the de-facto backend API.
    """

    code: str
    category: str
    required_parameters: tuple[str, ...] = ()
    supersedes_navigation: bool = False
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        code = self.code.strip().upper()
        category = self.category.strip().lower()
        required = tuple(
            dict.fromkeys(parameter.strip() for parameter in self.required_parameters)
        )
        if not code:
            raise ValueError("Komut tanım kodu boş olamaz")
        if not category:
            raise ValueError("Komut tanım kategorisi boş olamaz")
        if any(not parameter for parameter in required):
            raise ValueError("Zorunlu komut parametresi boş olamaz")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "required_parameters", required)
