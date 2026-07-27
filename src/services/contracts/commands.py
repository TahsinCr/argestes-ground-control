from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.events import EventStream
from src.models.commands import CommandRequest, CommandResult


class CommandService(ABC):
    @property
    def available_tasks(self) -> tuple[str, ...]:
        """Task and action codes supported by the backend."""

        return ()

    @property
    @abstractmethod
    def dispatched(self) -> EventStream[CommandRequest]: ...

    @property
    @abstractmethod
    def completed(self) -> EventStream[CommandResult]: ...

    @abstractmethod
    def dispatch(self, request: CommandRequest) -> CommandResult: ...

    def issue_command(
        self,
        command: str,
        params: dict | None = None,
        *,
        source: str = "ui",
    ) -> CommandResult:
        return self.dispatch(CommandRequest(command, params or {}, source))
