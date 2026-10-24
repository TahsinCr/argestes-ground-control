from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import replace

from src.core.events import EventBus
from src.models.parameters import Parameter
from src.extension.api.parameters import ParameterService, ParameterSyncStatus


class MutableParameterService(ParameterService):
    def __init__(self, parameters: Iterable[Parameter] = ()) -> None:
        self._parameters = {item.key: replace(item) for item in parameters}
        self._changed = EventBus[tuple[Parameter, ...]]()
        self._sync_status = ParameterSyncStatus()
        self._sync_status_changed = EventBus[ParameterSyncStatus]()
        self._lock = threading.RLock()

    @property
    def current(self) -> tuple[Parameter, ...]:
        with self._lock:
            return tuple(replace(item) for item in self._parameters.values())

    @property
    def changed(self) -> EventBus[tuple[Parameter, ...]]:
        return self._changed

    @property
    def sync_status(self) -> ParameterSyncStatus:
        with self._lock:
            return self._sync_status

    @property
    def sync_status_changed(self) -> EventBus[ParameterSyncStatus]:
        return self._sync_status_changed

    def _set_sync_status(self, status: ParameterSyncStatus) -> None:
        with self._lock:
            if self._sync_status == status:
                return
            self._sync_status = status
        self._sync_status_changed.publish(status)

    def start(self) -> None:
        self._changed.publish(self.current)

    def stop(self) -> None:
        return

    def refresh(self) -> None:
        self._changed.publish(self.current)

    def replace(self, parameters: Iterable[Parameter]) -> None:
        updated = {item.key: replace(item) for item in parameters}
        with self._lock:
            if self._parameters == updated:
                return
            self._parameters = updated
        self._changed.publish(self.current)

    def upsert(self, parameter: Parameter) -> None:
        with self._lock:
            if self._parameters.get(parameter.key) == parameter:
                return
            self._parameters[parameter.key] = replace(parameter)
        self._changed.publish(self.current)

    def upsert_many(self, parameters: Iterable[Parameter]) -> None:
        changed = False
        with self._lock:
            for parameter in parameters:
                if self._parameters.get(parameter.key) == parameter:
                    continue
                self._parameters[parameter.key] = replace(parameter)
                changed = True
        if not changed:
            return
        self._changed.publish(self.current)

    def update_values(self, changes: dict[str, object]) -> None:
        if not changes:
            return
        with self._lock:
            missing = set(changes) - set(self._parameters)
            if missing:
                raise KeyError(f"Bilinmeyen parametreler: {', '.join(sorted(missing))}")
            for key, value in changes.items():
                self._parameters[key].value = value
        self._changed.publish(self.current)

    def _find(self, key: str) -> Parameter | None:
        with self._lock:
            item = self._parameters.get(key)
            return replace(item) if item is not None else None
