"""Optional, structural vehicle capabilities. Providers implement only what they offer."""

from collections.abc import Iterable
from typing import Protocol, runtime_checkable
from src.models.mission import MapOverlay
from src.core.events import EventBus
from src.models.vehicle import VehicleConnectionState


@runtime_checkable
class RcControl(Protocol):
    def send_rc_override(self, channels: Iterable[int | None]) -> None: ...
    def release_rc_override(self) -> None: ...


@runtime_checkable
class MissionControl(Protocol):
    @property
    def connected(self) -> bool: ...
    @property
    def connection_changed(self) -> EventBus[VehicleConnectionState]: ...
    @property
    def synchronize_mission_on_connect(self) -> bool: ...
    def set_default_mission_altitude(self, altitude_m: float) -> None: ...
    def upload_mission(
        self, overlays: Iterable[MapOverlay], *, loop: bool = False
    ) -> int: ...
    def download_mission(self) -> list[MapOverlay]: ...
