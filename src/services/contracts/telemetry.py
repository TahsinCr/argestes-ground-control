from __future__ import annotations

from abc import abstractmethod
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventStream
from src.models.telemetry import TelemetrySnapshot


class TelemetryService(Service):
    @property
    @abstractmethod
    def current(self) -> TelemetrySnapshot: ...

    @property
    @abstractmethod
    def updated(self) -> EventStream[TelemetrySnapshot]: ...

    @abstractmethod
    def push(self, snapshot: TelemetrySnapshot) -> None:
        """UI, test, ağ veya başka bir tetikleyiciden telemetri kabul eder."""

    @abstractmethod
    def patch(self, **changes: Any) -> None:
        """Bir veri kaynağının sahip olduğu alanları atomik olarak günceller."""
