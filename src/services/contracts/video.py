from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventStream
from src.models.settings import PhoneCameraSettings
from src.models.video import (
    PhoneCameraBridgeState,
    QrDetectionSnapshot,
    VideoFrame,
    VideoStreamStats,
)


class VideoService(Service):
    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    @abstractmethod
    def connection_changed(self) -> EventStream[bool]: ...

    @property
    @abstractmethod
    def frames(self) -> EventStream[VideoFrame]: ...

    @property
    @abstractmethod
    def stats(self) -> VideoStreamStats: ...

    @property
    @abstractmethod
    def stats_changed(self) -> EventStream[VideoStreamStats]: ...

    @abstractmethod
    def configure(
        self,
        *,
        source_type: str,
        source_uri: str,
        latency_ms: int,
        codec: str,
        frame_timeout_s: float,
    ) -> None: ...

    @abstractmethod
    def reconnect(self) -> None: ...

    @abstractmethod
    def push(self, frame: VideoFrame) -> None: ...


class QrCodeService(Service):
    """QR algılama sonuçlarını UI teknolojisinden bağımsız sunar."""

    @property
    @abstractmethod
    def qr_snapshot(self) -> QrDetectionSnapshot: ...

    @property
    @abstractmethod
    def qr_changed(self) -> EventStream[QrDetectionSnapshot]: ...

    @property
    @abstractmethod
    def qr_active(self) -> bool: ...

    @abstractmethod
    def set_qr_active(self, active: bool) -> None: ...

    @abstractmethod
    def mark_qr_submitted(self, data: str) -> None:
        """Sunucu tarafından kabul edilen QR'ı mevcut görev oturumunda işaretler."""
        ...


class PhoneCameraBridgeService(Service):
    @property
    @abstractmethod
    def state(self) -> PhoneCameraBridgeState: ...

    @property
    @abstractmethod
    def changed(self) -> EventStream[PhoneCameraBridgeState]: ...

    @abstractmethod
    def configure(self, config: PhoneCameraSettings) -> None: ...
