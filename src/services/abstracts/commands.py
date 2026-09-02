from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.events import EventBus
from src.models.commands import CommandDefinition, CommandRequest, CommandResult


class CommandService(ABC):
    @property
    def command_definitions(self) -> tuple[CommandDefinition, ...]:
        """Transport-neutral commands the application understands."""

        return ()

    @property
    def available_commands(self) -> tuple[CommandDefinition, ...]:
        """Commands currently offered by the connected vehicle."""

        return self.command_definitions

    @property
    def available_flight_modes(self) -> tuple[str, ...]:
        """Modes advertised by the currently connected autopilot."""

        return ()

    @property
    def available_command_codes(self) -> tuple[str, ...]:
        return tuple(definition.code for definition in self.available_commands)

    @property
    def available_tasks(self) -> tuple[str, ...]:
        """Task and action codes supported by the backend."""

        return ()

    @property
    @abstractmethod
    def dispatched(self) -> EventBus[CommandRequest]: ...

    @property
    @abstractmethod
    def completed(self) -> EventBus[CommandResult]: ...

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
