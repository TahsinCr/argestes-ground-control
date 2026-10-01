"""Wire-level operations private to the MAVLink package."""

from abc import abstractmethod
from collections.abc import Callable, Iterable
from typing import Any

from src.core.events import Subscription
from src.services.abstracts.vehicle import VehicleService


class MavlinkVehiclePort(VehicleService):
    @abstractmethod
    def subscribe(self, message_types: str | Iterable[str],
                  callback: Callable[[Any], None]) -> Subscription: ...

    @abstractmethod
    def request_parameters(self) -> None: ...

    @abstractmethod
    def set_parameter(self, name: str, value: object, value_type: int | None = None) -> None: ...

    @abstractmethod
    def mode_name(self, heartbeat: Any, fallback: str = "UNKNOWN") -> str: ...
