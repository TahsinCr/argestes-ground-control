from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from src.core.events import EventBus
from src.core.abstracts import Service
from src.models.parameters import Parameter


class ParameterSyncState(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    PARTIAL = "partial"
    COMPLETE = "complete"
    FAILED = "failed"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class ParameterSyncStatus:
    state: ParameterSyncState = ParameterSyncState.IDLE
    received_count: int = 0
    expected_count: int | None = None


class ParameterService(Service):
    __slots__ = ("_sync_status_changed_fallback",)

    @property
    @abstractmethod
    def current(self) -> tuple[Parameter, ...]: ...

    @property
    @abstractmethod
    def changed(self) -> EventBus[tuple[Parameter, ...]]: ...

    @property
    def sync_status(self) -> ParameterSyncStatus:
        """Return synchronization quality when the provider can report it."""

        return ParameterSyncStatus()

    @property
    def sync_status_changed(self) -> EventBus[ParameterSyncStatus]:
        """Expose optional sync updates without requiring legacy providers to change."""

        bus = getattr(self, "_sync_status_changed_fallback", None)
        if bus is None:
            bus = EventBus[ParameterSyncStatus]()
            self._sync_status_changed_fallback = bus
        return bus

    @abstractmethod
    def replace(self, parameters: Iterable[Parameter]) -> None: ...

    @abstractmethod
    def upsert(self, parameter: Parameter) -> None: ...

    @abstractmethod
    def update_values(self, changes: dict[str, object]) -> None: ...

    @abstractmethod
    def refresh(self) -> None: ...
