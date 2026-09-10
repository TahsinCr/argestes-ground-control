from __future__ import annotations

from abc import abstractmethod

from src.core.abstracts import Service
from src.core.events import EventBus
from src.models.joystick import JoystickDevice, JoystickState


class JoystickService(Service):
    @property
    @abstractmethod
    def changed(self) -> EventBus[JoystickState]: ...

    @property
    @abstractmethod
    def state(self) -> JoystickState: ...

    @abstractmethod
    def devices(self) -> tuple[JoystickDevice, ...]: ...

    @abstractmethod
    def reconnect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...
