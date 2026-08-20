from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.onboard import OnboardMissionEvent, OnboardMissionState
from src.models.orin import OrinCommandReceipt


class OnboardMissionServiceContract(Service):
    """Task protocol exposed without MAVLink details."""

    @property
    @abstractmethod
    def state(self) -> OnboardMissionState: ...

    @property
    @abstractmethod
    def changed(self) -> EventBus[OnboardMissionState]: ...

    @property
    @abstractmethod
    def events(self) -> EventBus[OnboardMissionEvent]: ...

    @abstractmethod
    def start_task(self, task: str, *, source: str = "ui") -> OrinCommandReceipt: ...

    @abstractmethod
    def pause_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt: ...

    @abstractmethod
    def resume_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt: ...

    @abstractmethod
    def stop_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt: ...

    @abstractmethod
    def cancel_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt: ...

    @abstractmethod
    def refresh(self) -> OrinCommandReceipt: ...

    @abstractmethod
    def mission_status(
        self,
        *,
        mission_id: int | None = None,
        task: str | None = None,
    ) -> OrinCommandReceipt: ...

    @abstractmethod
    def mission_events(
        self,
        mission_id: int,
        *,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> OrinCommandReceipt: ...

    @abstractmethod
    def start_chain(self, chain: str) -> OrinCommandReceipt: ...

    @abstractmethod
    def select_combat_target(self, team_number: int) -> OrinCommandReceipt: ...

    @abstractmethod
    def clear_combat_target(self) -> OrinCommandReceipt | None: ...


__all__ = ["OnboardMissionServiceContract"]
