from __future__ import annotations

from src.models.commands import CommandRequest, CommandResult
from src.models.mission import MapContact, MapOverlay, MissionState
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.parameters import Parameter
from src.services.abstracts.commands import CommandService
from src.services.planner import PlannerService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.settings import SettingsRepository


class BackendFacade:
    """Stable backend interface shared by Qt, web, CLI and automation clients."""

    def __init__(
        self,
        *,
        telemetry: TelemetryService,
        planner: PlannerService,
        commands: CommandService,
        parameters: ParameterService,
        settings: SettingsRepository,
    ) -> None:
        self.telemetry = telemetry
        self.planner = planner
        self.commands = commands
        self.parameters = parameters
        self.settings = settings

    def telemetry_snapshot(self) -> TelemetrySnapshot:
        return self.telemetry.current

    def push_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self.telemetry.push(snapshot)

    def mission_snapshot(self) -> MissionState:
        return self.planner.state

    def upsert_mission_item(self, item: MapOverlay) -> None:
        self.planner.upsert_overlay(item)

    def remove_mission_item(self, item_id: str) -> None:
        self.planner.remove_overlay(item_id)

    def set_mission_loop(self, enabled: bool) -> None:
        self.planner.set_mission_loop(enabled)

    def upsert_contact(self, contact: MapContact) -> None:
        self.planner.upsert_contact(contact)

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

    def parameter_snapshot(self) -> tuple[Parameter, ...]:
        return self.parameters.current

    def update_parameters(self, changes: dict[str, object]) -> None:
        self.parameters.update_values(changes)

    def refresh_parameters(self) -> None:
        self.parameters.refresh()

    def upload_current_mission(self, *, source: str = "external") -> CommandResult:
        state = self.planner.state
        return self.issue(
            "MISSION_UPLOAD",
            {
                "overlays": [item.to_dict() for item in state.overlays],
                "loop": state.mission_loop,
            },
            source=source,
        )
