from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, TypeVar, runtime_checkable


class Model(ABC):
    """Domain model contract independent from UI and storage technologies."""

    __slots__ = ()

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Return a representation safe for transport and persistence."""


class Service(ABC):
    """Universal contract for services with a lifecycle."""

    __slots__ = ()

    @abstractmethod
    def start(self) -> None:
        """Prepare the service for use."""

    @abstractmethod
    def stop(self) -> None:
        """Close resources owned by the service safely."""


T_co = TypeVar("T_co", covariant=True)


@runtime_checkable
class ReadableValue(Protocol[T_co]):
    """Generic read contract exposing the latest value."""

    @property
    def current(self) -> T_co: ...


@runtime_checkable
class Closable(Protocol):
    def close(self) -> None: ...
