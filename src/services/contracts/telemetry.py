from __future__ import annotations

from abc import abstractmethod
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.telemetry import TelemetrySnapshot


class TelemetryService(Service):
    @property
    @abstractmethod
    def current(self) -> TelemetrySnapshot: ...

    @property
    @abstractmethod
    def updated(self) -> EventBus[TelemetrySnapshot]: ...

    @abstractmethod
    def push(self, snapshot: TelemetrySnapshot) -> None:
        """Accept telemetry from UI, tests, network or another trigger."""

    @abstractmethod
    def patch(self, **changes: Any) -> None:
        """Atomically update fields owned by one data source."""
