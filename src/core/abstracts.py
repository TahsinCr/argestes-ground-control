from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, TypeVar, runtime_checkable


class Model(ABC):
    """UI ve depolama teknolojisinden bağımsız domain modeli sözleşmesi."""

    __slots__ = ()

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Modelin taşıma/depolama için güvenli temsilini döndürür."""


class Service(ABC):
    """Yaşam döngüsü olan servislerin evrensel sözleşmesi."""

    __slots__ = ()

    @abstractmethod
    def start(self) -> None:
        """Servisi kullanıma hazır hâle getirir."""

    @abstractmethod
    def stop(self) -> None:
        """Servisin sahip olduğu kaynakları güvenli biçimde kapatır."""


T_co = TypeVar("T_co", covariant=True)


@runtime_checkable
class ReadableValue(Protocol[T_co]):
    """Son değeri sunan genel okuma sözleşmesi."""

    @property
    def current(self) -> T_co: ...


@runtime_checkable
class Closable(Protocol):
    def close(self) -> None: ...
