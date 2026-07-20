from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class Subscription:
    """Bir olay aboneliğinin sahipliğini ve iptalini temsil eder."""

    _cancel: Callable[[], None]
    _active: bool = True

    @property
    def active(self) -> bool:
        return self._active

    def cancel(self) -> None:
        if not self._active:
            return
        self._active = False
        self._cancel()

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *_args: object) -> None:
        self.cancel()


class EventStream(Generic[T]):
    """Qt, asyncio veya başka bir arayüze bağlı olmayan thread-safe olay akışı."""

    def __init__(self) -> None:
        self._subscribers: dict[int, Callable[[T], None]] = {}
        self._next_id = 0
        self._lock = threading.RLock()

    def subscribe(self, callback: Callable[[T], None]) -> Subscription:
        with self._lock:
            subscriber_id = self._next_id
            self._next_id += 1
            self._subscribers[subscriber_id] = callback

        def cancel() -> None:
            with self._lock:
                self._subscribers.pop(subscriber_id, None)

        return Subscription(cancel)

    def publish(self, value: T) -> None:
        with self._lock:
            callbacks = tuple(self._subscribers.values())
        for callback in callbacks:
            callback(value)

    def publish_isolated(self, value: T) -> tuple[Exception, ...]:
        """Bir hatalı tüketicinin diğer aboneleri engellemediği yayın biçimi."""
        with self._lock:
            callbacks = tuple(self._subscribers.values())
        errors: list[Exception] = []
        for callback in callbacks:
            try:
                callback(value)
            except Exception as exc:
                errors.append(exc)
        return tuple(errors)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._subscribers)
