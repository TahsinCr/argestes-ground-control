from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.models.mission import MissionState


class MissionArchiveRepository(ABC):
    @abstractmethod
    def list_plans(self) -> list[Path]: ...

    @abstractmethod
    def save_plan(self, target: str | Path, state: MissionState) -> Path: ...

    @abstractmethod
    def load_plan(self, source: str | Path) -> MissionState: ...

    @abstractmethod
    def delete_plan(self, source: str | Path) -> None: ...
