from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.kamikaze_approach import KamikazeApproachPlan
from src.models.onboard import OnboardMissionEvent, OnboardMissionState
from src.models.orin import OrinCommandReceipt
from src.models.geometry import Point


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
    def start_calibration(self) -> OrinCommandReceipt: ...

    @abstractmethod
    def start_course(self) -> OrinCommandReceipt: ...

    @abstractmethod
    def start_kamikaze_transit(
        self,
        plan: KamikazeApproachPlan,
        *,
        source: str = "ui",
    ) -> OrinCommandReceipt: ...

    @abstractmethod
    def start_hss_transit(
        self,
        destination: Point,
        *,
        source: str = "ui",
    ) -> OrinCommandReceipt: ...

    @abstractmethod
    def pause_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt: ...

    @abstractmethod
    def resume_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt: ...

    @abstractmethod
    def stop_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt: ...

    @abstractmethod
    def cancel_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt: ...

    @abstractmethod
    def set_video_recording(self, enabled: bool) -> OrinCommandReceipt:
        """Enable or disable only the Onboard camera-video recorder."""

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


__all__ = ["OnboardMissionServiceContract"]
