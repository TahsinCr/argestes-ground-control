from __future__ import annotations

from abc import abstractmethod
from datetime import datetime

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.competition import (
    CompetitionClockSample,
    CompetitionConnectionState,
    CompetitionServerConfig,
    CompetitorTelemetry,
    HssZone,
    KamikazeEvent,
    LockEvent,
    QrCoordinate,
)


class CompetitionService(Service):
    """UI-independent port for the TEKNOFEST competition server."""

    @property
    @abstractmethod
    def state(self) -> CompetitionConnectionState: ...

    @property
    @abstractmethod
    def changed(self) -> EventBus[CompetitionConnectionState]: ...

    @property
    @abstractmethod
    def config_updated(self) -> EventBus[CompetitionServerConfig]: ...

    @property
    @abstractmethod
    def clock_updated(self) -> EventBus[CompetitionClockSample]: ...

    @property
    @abstractmethod
    def competitors_updated(self) -> EventBus[tuple[CompetitorTelemetry, ...]]: ...

    @property
    @abstractmethod
    def competitors(self) -> tuple[CompetitorTelemetry, ...]: ...

    @property
    @abstractmethod
    def hss_updated(self) -> EventBus[tuple[HssZone, ...]]: ...

    @property
    @abstractmethod
    def qr_updated(self) -> EventBus[QrCoordinate | None]: ...

    @property
    @abstractmethod
    def hss_zones(self) -> tuple[HssZone, ...]: ...

    @property
    @abstractmethod
    def qr_coordinate(self) -> QrCoordinate | None: ...

    @property
    @abstractmethod
    def config(self) -> CompetitionServerConfig: ...

    @abstractmethod
    def configure(self, config: CompetitionServerConfig) -> None: ...

    @abstractmethod
    def configure_servers(self, configs: tuple[CompetitionServerConfig, ...]) -> None: ...

    @abstractmethod
    def reconnect(self, config: CompetitionServerConfig | None = None) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def refresh_reference_data(self) -> None: ...

    @abstractmethod
    def refresh_qr_coordinate(self) -> None: ...

    @abstractmethod
    def refresh_server_time(self) -> datetime:
        """Fetch and cache a new competition-server clock sample."""

    @abstractmethod
    def estimated_server_time(self) -> datetime | None: ...

    @abstractmethod
    def submit_lock_event(self, event: LockEvent) -> None: ...

    @abstractmethod
    def submit_kamikaze_event(self, event: KamikazeEvent) -> None: ...
