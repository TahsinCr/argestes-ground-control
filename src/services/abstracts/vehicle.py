from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.commands import CommandRequest
from src.models.mission import MapOverlay
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
    def synchronize_mission_on_connect(self) -> bool:
        """Whether the provider opts into automatic mission synchronization."""
        return False

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
    def set_default_mission_altitude(self, altitude_m: float) -> None: ...

    @abstractmethod
    def execute(self, request: CommandRequest) -> bool:
        """Execute the operation and return True after protocol-level confirmation."""
        ...

    @abstractmethod
    def send_rc_override(self, channels: Iterable[int | None]) -> None:
        """Send one latest RC override sample; ``None`` leaves a channel unchanged."""
        ...

    @abstractmethod
    def release_rc_override(self) -> None:
        """Return every overridden RC channel to the flight controller."""
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
