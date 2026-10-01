"""Selected protocol identity and per-extension adapter selection."""
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ActiveProtocol:
    identifier: str

    def adapter(self, factories: dict[str, Callable[[], T]]) -> T:
        try:
            factory = factories[self.identifier]
        except KeyError as error:
            raise RuntimeError(f"Unsupported protocol: {self.identifier}") from error
        return factory()
