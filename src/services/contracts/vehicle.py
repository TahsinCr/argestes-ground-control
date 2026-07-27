from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Iterable
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventStream, Subscription
from src.models.commands import CommandRequest
from src.models.mission import MapOverlay
from src.models.vehicle import (
    VehicleConnectionConfig,
    VehicleConnectionState,
    VehicleTransportStats,
)


class MissionTransferInterrupted(RuntimeError):
    """A background mission read yielded to an operator-initiated upload."""


class VehicleService(Service):
    """Abstract the flight controller from MAVLink, simulators or other transports."""

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    @abstractmethod
    def state(self) -> VehicleConnectionState: ...

    @property
    @abstractmethod
    def connection_changed(self) -> EventStream[VehicleConnectionState]: ...

    @property
    @abstractmethod
    def transport_stats(self) -> VehicleTransportStats: ...

    @abstractmethod
    def configure_connection(self, config: VehicleConnectionConfig) -> None: ...

    @abstractmethod
    def reconnect(self, config: VehicleConnectionConfig | None = None) -> None: ...

    @abstractmethod
    def subscribe(
        self,
        message_types: str | Iterable[str],
        callback: Callable[[Any], None],
    ) -> Subscription: ...

    @abstractmethod
    def request_parameters(self) -> None: ...

    @abstractmethod
    def set_parameter(self, name: str, value: object, value_type: int | None = None) -> None: ...

    @abstractmethod
    def mode_name(self, heartbeat: Any, fallback: str = "UNKNOWN") -> str: ...

    @abstractmethod
    def set_default_mission_altitude(self, altitude_m: float) -> None: ...

    @abstractmethod
    def execute(self, request: CommandRequest) -> bool:
        """Execute the operation and return True after protocol-level confirmation."""
        ...

    @abstractmethod
    def upload_mission(
        self,
        overlays: Iterable[MapOverlay],
        *,
        loop: bool = False,
    ) -> int: ...

    @abstractmethod
    def download_mission(self) -> list[MapOverlay]: ...
