from __future__ import annotations

from abc import abstractmethod
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventStream
from src.models.orin import OrinCommandReceipt, OrinConnectionState, OrinLinkConfig, OrinMessage


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
    def changed(self) -> EventStream[OrinConnectionState]: ...

    @property
    @abstractmethod
    def received(self) -> EventStream[OrinMessage]: ...

    @property
    @abstractmethod
    def running(self) -> bool: ...

    @abstractmethod
    def configure(self, config: OrinLinkConfig) -> None: ...

    @abstractmethod
    def probe(self) -> int:
        """Send an immediate liveness probe over the shared MAVLink link."""

    @abstractmethod
    def issue(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> OrinCommandReceipt: ...
