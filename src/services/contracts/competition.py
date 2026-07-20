from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventStream
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
    """TEKNOFEST yarışma sunucusu için arayüzden bağımsız port."""

    @property
    @abstractmethod
    def state(self) -> CompetitionConnectionState: ...

    @property
    @abstractmethod
    def changed(self) -> EventStream[CompetitionConnectionState]: ...

    @property
    @abstractmethod
    def competitors_updated(self) -> EventStream[tuple[CompetitorTelemetry, ...]]: ...

    @property
    @abstractmethod
    def competitors(self) -> tuple[CompetitorTelemetry, ...]: ...

    @property
    @abstractmethod
    def hss_updated(self) -> EventStream[tuple[HssZone, ...]]: ...

    @property
    @abstractmethod
    def qr_updated(self) -> EventStream[QrCoordinate | None]: ...

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
    def submit_lock_event(self, event: LockEvent) -> None: ...

    @abstractmethod
    def submit_kamikaze_event(self, event: KamikazeEvent) -> None: ...
