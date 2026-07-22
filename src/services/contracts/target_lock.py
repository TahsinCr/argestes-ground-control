from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventStream
from src.models.target_lock import (
    TargetLockAcquisition,
    TargetLockVisionState,
    TargetLockWorkflowState,
)


class TargetLockVisionService(Service):
    """Hedef tespiti, takibi ve şartname geometrisi için backend portu."""

    @property
    @abstractmethod
    def target_lock_state(self) -> TargetLockVisionState: ...

    @property
    @abstractmethod
    def target_lock_changed(self) -> EventStream[TargetLockVisionState]: ...

    @property
    @abstractmethod
    def target_acquired(self) -> EventStream[TargetLockAcquisition]: ...

    @abstractmethod
    def set_target_lock_active(self, active: bool) -> None: ...


class TargetLockService(Service):
    """Görev komutu ile yarışma sunucusu teslimini birleştiren port."""

    @property
    @abstractmethod
    def state(self) -> TargetLockWorkflowState: ...

    @property
    @abstractmethod
    def changed(self) -> EventStream[TargetLockWorkflowState]: ...

    @abstractmethod
    def activate(self) -> None: ...

    @abstractmethod
    def deactivate(self) -> None: ...
