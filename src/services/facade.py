from __future__ import annotations

from collections.abc import Callable

from src.models.commands import CommandRequest, CommandResult
from src.models.competition import CompetitionConnectionState, KamikazeEvent, LockEvent
from src.models.mission import MapContact, MapOverlay, MissionState
from src.models.orin import OrinMessage
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.parameters import Parameter
from src.services.contracts.commands import CommandService
from src.services.contracts.competition import CompetitionService
from src.services.planner import PlannerService
from src.services.contracts.orin import OrinMessageHandler, OrinService
from src.services.contracts.parameters import ParameterService
from src.services.contracts.telemetry import TelemetryService
from src.services.contracts.settings import SettingsRepository


class BackendFacade:
    """Stable backend interface shared by Qt, web, CLI and automation clients."""

    def __init__(
        self,
        *,
        telemetry: TelemetryService,
        competition: CompetitionService,
        planner: PlannerService,
        orin: OrinService,
        commands: CommandService,
        parameters: ParameterService,
        settings: SettingsRepository,
    ) -> None:
        self.telemetry = telemetry
        self.competition = competition
        self.planner = planner
        self.orin = orin
        self.commands = commands
        self.parameters = parameters
        self.settings = settings

    def telemetry_snapshot(self) -> TelemetrySnapshot:
        return self.telemetry.current

    def push_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self.telemetry.push(snapshot)

    def competition_snapshot(self) -> CompetitionConnectionState:
        return self.competition.state

    def refresh_competition_map_data(self) -> None:
        self.competition.refresh_reference_data()

    def submit_vehicle_lock_event(self, event: LockEvent) -> None:
        self.competition.submit_lock_event(event)

    def submit_vehicle_kamikaze_event(self, event: KamikazeEvent) -> None:
        self.competition.submit_kamikaze_event(event)

    def mission_snapshot(self) -> MissionState:
        return self.planner.state

    def orin_snapshot(self):
        return self.orin.state

    def issue_orin(self, packet_type: str, payload: dict | None = None):
        return self.orin.issue(packet_type, payload or {})

    def send_orin(self, packet_type: str, payload: dict | None = None):
        return self.orin.send(packet_type, payload or {})

    def subscribe_orin(self, callback: Callable[[OrinMessage], None]):
        return self.orin.received.subscribe(callback)

    def handle_orin(
        self,
        packet_type: str,
        handler: OrinMessageHandler,
    ):
        return self.orin.register_handler(packet_type, handler)

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
