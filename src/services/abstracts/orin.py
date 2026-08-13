from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Mapping
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventBus, Subscription
from src.models.orin import (
    OrinCommandReceipt,
    OrinConnectionState,
    OrinLinkConfig,
    OrinLogRecord,
    OrinMessage,
)


OrinMessageHandler = Callable[
    [OrinMessage],
    Mapping[str, Any] | None,
]


class OrinService(Service):
    """Onboard-computer contract independent from UI and MAVLink details."""

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    @abstractmethod
    def state(self) -> OrinConnectionState: ...

    @property
    @abstractmethod
    def changed(self) -> EventBus[OrinConnectionState]: ...

    @property
    @abstractmethod
    def received(self) -> EventBus[OrinMessage]: ...

    @property
    @abstractmethod
    def remote_logs(self) -> EventBus[OrinLogRecord]: ...

    @property
    @abstractmethod
    def remote_log_batches(self) -> EventBus[tuple[OrinLogRecord, ...]]: ...

    @property
    @abstractmethod
    def running(self) -> bool: ...

    @abstractmethod
    def configure(self, config: OrinLinkConfig) -> None: ...

    @abstractmethod
    def probe(self) -> int:
        """Send an immediate liveness probe over the shared MAVLink link."""

    @abstractmethod
    def send(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
    ) -> OrinMessage:
        """Send a notification to the onboard application."""

    @abstractmethod
    def issue(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> OrinCommandReceipt: ...

    @abstractmethod
    def register_handler(
        self,
        packet_type: str,
        handler: OrinMessageHandler,
    ) -> Subscription:
        """Register a handler for requests initiated by the onboard side."""
