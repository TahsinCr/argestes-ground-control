from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.events import EventBus
from src.models.logs import LogEntry


class LogService(ABC):
    @property
    @abstractmethod
    def entries(self) -> EventBus[LogEntry]: ...

    @property
    @abstractmethod
    def history(self) -> list[LogEntry]: ...

    @abstractmethod
    def log(self, source: str, message: str, severity: str = "info") -> None: ...
