"""Inert backend contracts kept only while the legacy UI is being redesigned.

These adapters deliberately perform no network or onboard action.  They keep
the current visual shell constructible without retaining SIHA automation,
competition, or Orin transport infrastructure in the common backend.
"""

from __future__ import annotations

from typing import Any

from src.core.events import EventBus, Subscription
from src.models.connections import ConnectionStatus
from src.models.onboard import OnboardMissionEvent, OnboardMissionState
from src.models.orin import OrinCommandReceipt
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.onboard import OnboardMissionServiceContract


class DormantOnboardMissionService(OnboardMissionServiceContract):
    """Non-operational UI compatibility surface for the removed Onboard layer."""

    def __init__(self) -> None:
        self._state = OnboardMissionState(last_error="SIHA otomasyon katmanı etkin değil")
        self._changed = EventBus[OnboardMissionState]()
        self._events = EventBus[OnboardMissionEvent]()

    @property
    def state(self) -> OnboardMissionState:
        return self._state

    @property
    def changed(self) -> EventBus[OnboardMissionState]:
        return self._changed

    @property
    def events(self) -> EventBus[OnboardMissionEvent]:
        return self._events

    @staticmethod
    def _unavailable() -> OrinCommandReceipt:
        return OrinCommandReceipt(0, False, "SIHA otomasyon katmanı etkin değil")

    def start_task(self, task: str, *, source: str = "ui") -> OrinCommandReceipt:
        return self._unavailable()

    def start_calibration(self) -> OrinCommandReceipt:
        return self._unavailable()

    def start_course(self) -> OrinCommandReceipt:
        return self._unavailable()

    def start_kamikaze_transit(self, plan: Any, *, source: str = "ui") -> OrinCommandReceipt:
        return self._unavailable()

    def start_hss_transit(self, destination: Any, *, source: str = "ui") -> OrinCommandReceipt:
        return self._unavailable()

    def pause_task(self, task: str = "") -> OrinCommandReceipt:
        return self._unavailable()

    def resume_task(self, task: str = "") -> OrinCommandReceipt:
        return self._unavailable()

    def stop_task(self, task: str = "") -> OrinCommandReceipt:
        return self._unavailable()

    def cancel_task(self, task: str = "") -> OrinCommandReceipt:
        return self._unavailable()

    def set_video_recording(self, enabled: bool) -> OrinCommandReceipt:
        return self._unavailable()

    def refresh(self) -> OrinCommandReceipt:
        return self._unavailable()

    def mission_status(
        self, *, mission_id: int | None = None, task: str | None = None
    ) -> OrinCommandReceipt:
        return self._unavailable()

    def mission_events(
        self, mission_id: int, *, after_sequence: int | None = None, limit: int = 200
    ) -> OrinCommandReceipt:
        return self._unavailable()

    def start_chain(self, chain: str) -> OrinCommandReceipt:
        return self._unavailable()

    def start(self) -> None:
        return

    def stop(self) -> None:
        return


class DormantConnectionControlService(ConnectionControlService):
    """Reports disabled legacy channels without opening or controlling them."""

    def __init__(self) -> None:
        self._changed = EventBus[ConnectionStatus]()

    @property
    def changed(self) -> EventBus[ConnectionStatus]:
        return self._changed

    @staticmethod
    def _status(channel: str) -> ConnectionStatus:
        return ConnectionStatus(
            channel=channel,
            connected=False,
            summary="Bağlantı katmanı yeniden tasarlanıyor",
            detail="SIHA bağlantı otomasyonu common backend'den çıkarıldı.",
            can_disconnect=False,
        )

    def status(self, channel: str) -> ConnectionStatus:
        return self._status(channel)

    def reconnect(self, channel: str, options: dict[str, Any]) -> ConnectionStatus:
        raise RuntimeError("Bu bağlantı katmanı etkin değil")

    def repair_serial_access(self, channel: str) -> ConnectionStatus:
        raise RuntimeError("Bu bağlantı katmanı etkin değil")

    def disconnect(self, channel: str) -> ConnectionStatus:
        return self._status(channel)

    def start(self) -> None:
        return

    def stop(self) -> None:
        return


__all__ = [
    "DormantConnectionControlService",
    "DormantOnboardMissionService",
]
