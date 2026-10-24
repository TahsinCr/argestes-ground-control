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
    @property
    @abstractmethod
    def current(self) -> tuple[Parameter, ...]: ...

    @property
    @abstractmethod
    def changed(self) -> EventBus[tuple[Parameter, ...]]: ...

    @property
    @abstractmethod
    def sync_status(self) -> ParameterSyncStatus:
        """Report the provider's current parameter synchronization state."""

    @property
    @abstractmethod
    def sync_status_changed(self) -> EventBus[ParameterSyncStatus]:
        """Publish parameter synchronization state changes."""

    @abstractmethod
    def replace(self, parameters: Iterable[Parameter]) -> None: ...

    @abstractmethod
    def upsert(self, parameter: Parameter) -> None: ...

    @abstractmethod
    def update_values(self, changes: dict[str, object]) -> None: ...

    @abstractmethod
    def refresh(self) -> None: ...
