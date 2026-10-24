from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventBus
from collections.abc import Callable
from datetime import datetime

from extensions.camera.models import (
    VideoFrame,
    VideoShareConfig,
    VideoShareState,
    VideoSourceConfig,
    VideoSourceFrame,
    VideoSourceStats,
    VideoStreamStats,
)


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

    @property
    @abstractmethod
    def source_frames(self) -> EventBus[VideoSourceFrame]: ...

    @property
    @abstractmethod
    def source_stats_changed(self) -> EventBus[VideoSourceStats]: ...

    @property
    @abstractmethod
    def source_stats(self) -> tuple[VideoSourceStats, ...]: ...

    @property
    @abstractmethod
    def share_states_changed(self) -> EventBus[tuple[VideoShareState, ...]]: ...

    @property
    @abstractmethod
    def share_states(self) -> tuple[VideoShareState, ...]: ...

    @abstractmethod
    def configure_shares(self, configs: tuple[VideoShareConfig, ...]) -> None: ...

    @abstractmethod
    def configure_sources(
        self,
        configs: tuple[VideoSourceConfig, ...],
        active_identifier: str,
    ) -> None: ...

    @abstractmethod
    def set_active_source(self, identifier: str) -> None: ...

    @abstractmethod
    def set_clock_provider(self, provider: Callable[[], datetime | None]) -> None: ...

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
    def reconnect_source(self, identifier: str) -> None: ...

    @abstractmethod
    def disconnect_source(self, identifier: str) -> None: ...

    @abstractmethod
    def push(self, frame: VideoFrame) -> None: ...
