from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.events import EventBus
from src.models.settings import SettingsModel


class SettingsRepository(ABC):
    @property
    @abstractmethod
    def current(self) -> SettingsModel: ...

    @property
    @abstractmethod
    def changed(self) -> EventBus[SettingsModel]: ...

    @abstractmethod
    def save(self, settings: SettingsModel, *, notify: bool = True) -> None: ...

    @abstractmethod
    def set_value(self, path: str, value: object) -> None: ...
