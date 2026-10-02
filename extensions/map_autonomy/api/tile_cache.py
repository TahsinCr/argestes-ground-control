from __future__ import annotations

from abc import abstractmethod
from pathlib import Path

from src.core.abstracts import Service
from src.core.events import EventBus


class TileCacheService(Service):
    @property
    @abstractmethod
    def running(self) -> bool: ...

    @property
    @abstractmethod
    def base_url(self) -> str: ...

    @property
    @abstractmethod
    def cache_changed(self) -> EventBus[int]: ...

    @abstractmethod
    def configure(
        self, *, cache_path: str | Path, enabled: bool, limit_mb: int
    ) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def restart(self) -> None: ...
