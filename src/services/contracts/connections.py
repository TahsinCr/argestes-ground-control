from __future__ import annotations

from abc import abstractmethod
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventStream
from src.models.connections import ConnectionStatus


class ConnectionControlService(Service):
    """Backend bağlantılarını ortak bir kullanım yüzeyinden yönetir."""

    @property
    @abstractmethod
    def changed(self) -> EventStream[ConnectionStatus]: ...

    @abstractmethod
    def status(self, channel: str) -> ConnectionStatus: ...

    @abstractmethod
    def reconnect(self, channel: str, options: dict[str, Any]) -> ConnectionStatus: ...

    @abstractmethod
    def repair_serial_access(self, channel: str) -> ConnectionStatus: ...

    @abstractmethod
    def disconnect(self, channel: str) -> ConnectionStatus: ...
