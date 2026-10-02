from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.commands import CommandRequest
from src.models.vehicle import (
    VehicleCapabilities,
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
    def connection_changed(self) -> EventBus[VehicleConnectionState]: ...

    @property
    @abstractmethod
    def transport_stats(self) -> VehicleTransportStats: ...

    @property
    @abstractmethod
    def capabilities(self) -> VehicleCapabilities:
        """Commands and modes usable by the current vehicle connection."""
        ...

    @abstractmethod
    def configure_connection(self, config: VehicleConnectionConfig) -> None: ...

    @abstractmethod
    def reconnect(self, config: VehicleConnectionConfig | None = None) -> None: ...
    @abstractmethod
    def execute(self, request: CommandRequest) -> bool:
        """Execute the operation and return True after protocol-level confirmation."""
        ...
