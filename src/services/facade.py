from __future__ import annotations

from src.models.commands import CommandRequest, CommandResult
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot


class BackendFacade:
    """Stable backend interface shared by Qt, web, CLI and automation clients."""

    def __init__(self, *, telemetry, commands, settings):
        self.telemetry = telemetry
        self.commands = commands
        self.settings = settings

    def telemetry_snapshot(self) -> TelemetrySnapshot:
        return self.telemetry.current

    def push_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self.telemetry.push(snapshot)

    def issue(
        self,
        code: str,
        parameters: dict | None = None,
        *,
        source: str = "external",
    ) -> CommandResult:
        return self.commands.dispatch(CommandRequest(code, parameters or {}, source))

    def settings_snapshot(self) -> SettingsModel:
        return self.settings.current

    def set_setting(self, path: str, value: object) -> None:
        self.settings.set_value(path, value)
