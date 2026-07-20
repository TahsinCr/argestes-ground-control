from __future__ import annotations

from abc import abstractmethod
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventStream
from src.models.orin import OrinCommandReceipt, OrinConnectionState, OrinLinkConfig, OrinMessage


class OrinService(Service):
    """Araç üstü bilgisayarın UI ve MAVLink ayrıntılarından bağımsız sözleşmesi."""

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

    @abstractmethod
    def configure(self, config: OrinLinkConfig) -> None: ...

    @abstractmethod
    def issue(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> OrinCommandReceipt: ...
