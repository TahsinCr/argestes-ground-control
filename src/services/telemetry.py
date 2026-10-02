"""Protocol-neutral latest telemetry state."""

from __future__ import annotations

import threading
import logging
from collections.abc import Callable
from typing import Any

from src.core.events import EventBus
from src.models.telemetry import TelemetrySnapshot
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import ServiceLogger


def clone_snapshot(snapshot: TelemetrySnapshot) -> TelemetrySnapshot:
    return snapshot.copy()


class MutableTelemetryService(TelemetryService):
    """Central telemetry service writable by any protocol adapter."""

    def __init__(self, logger: ServiceLogger | None = None) -> None:
        self._current = TelemetrySnapshot.zero()
        self._raw = self._current
        self._processors: dict[
            str, Callable[[TelemetrySnapshot], TelemetrySnapshot]
        ] = {}
        self._processor_failures: set[str] = set()
        self._updated = EventBus[TelemetrySnapshot]()
        self._logger = logger
        self._lock = threading.RLock()

    @property
    def current(self) -> TelemetrySnapshot:
        with self._lock:
            return clone_snapshot(self._current)

    @property
    def updated(self) -> EventBus[TelemetrySnapshot]:
        return self._updated

    def start(self) -> None:
        self._updated.publish(self.current)
        if self._logger:
            self._logger.info("Telemetri servisi veri kaynağı bekliyor")

    def stop(self) -> None:
        return

    def push(self, snapshot: TelemetrySnapshot) -> None:
        value = clone_snapshot(snapshot)
        with self._lock:
            self._raw = value
            value = self._process(value)
            self._current = value
        self._updated.publish(clone_snapshot(value))

    def _process(self, value: TelemetrySnapshot) -> TelemetrySnapshot:
        for key, processor in self._processors.items():
            try:
                candidate = processor(value)
                if not isinstance(candidate, TelemetrySnapshot):
                    raise TypeError("Telemetry processors must return a snapshot")
                value = candidate
                self._processor_failures.discard(key)
            except Exception:
                if key not in self._processor_failures:
                    self._processor_failures.add(key)
                    logging.getLogger(f"extensions.{key.split('.')[0]}").exception(
                        "Normalized telemetry processor failed: %s", key
                    )
        return value

    def add_processor(
        self, key: str, processor: Callable[[TelemetrySnapshot], TelemetrySnapshot]
    ) -> None:
        with self._lock:
            if key in self._processors:
                raise ValueError(f"Telemetry processor already registered: {key}")
            self._processors[key] = processor
            self._current = self._process(self._raw)
            value = clone_snapshot(self._current)
        self._updated.publish(value)

    def remove_processor(self, key: str) -> None:
        with self._lock:
            self._processors.pop(key, None)
            self._processor_failures.discard(key)
            self._current = self._process(self._raw)
            value = clone_snapshot(self._current)
        self._updated.publish(value)

    def _current_for_decoder(self) -> TelemetrySnapshot:
        """Borrow the read-only snapshot for a decoder pass."""
        with self._lock:
            return self._current

    def patch(self, **changes: Any) -> None:
        with self._lock:
            self._raw = self._raw.copy(**changes)
            self._current = self._process(self._raw)
            value = clone_snapshot(self._current)
        self._updated.publish(value)
