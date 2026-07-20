from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable

from src.core.events import EventStream
from src.core.abstracts import Service
from src.models.parameters import Parameter


class ParameterService(Service):
    @property
    @abstractmethod
    def current(self) -> tuple[Parameter, ...]: ...

    @property
    @abstractmethod
    def changed(self) -> EventStream[tuple[Parameter, ...]]: ...

    @abstractmethod
    def replace(self, parameters: Iterable[Parameter]) -> None: ...

    @abstractmethod
    def upsert(self, parameter: Parameter) -> None: ...

    @abstractmethod
    def update_values(self, changes: dict[str, object]) -> None: ...

    @abstractmethod
    def refresh(self) -> None: ...
