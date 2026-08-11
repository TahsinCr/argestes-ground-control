from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.competition import (
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

    @abstractmethod
    def configure(self, config: CompetitionServerConfig) -> None: ...

    @abstractmethod
    def reconnect(self, config: CompetitionServerConfig | None = None) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def refresh_reference_data(self) -> None: ...

    @abstractmethod
    def refresh_qr_coordinate(self) -> None: ...

    @abstractmethod
    def submit_lock_event(self, event: LockEvent) -> None: ...

    @abstractmethod
    def submit_kamikaze_event(self, event: KamikazeEvent) -> None: ...
