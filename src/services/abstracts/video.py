from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.video import VideoFrame, VideoStreamStats


class VideoService(Service):
    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    @abstractmethod
    def connection_changed(self) -> EventBus[bool]: ...

    @property
    @abstractmethod
    def frames(self) -> EventBus[VideoFrame]: ...

    @property
    @abstractmethod
    def stats(self) -> VideoStreamStats: ...

    @property
    @abstractmethod
    def stats_changed(self) -> EventBus[VideoStreamStats]: ...

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
