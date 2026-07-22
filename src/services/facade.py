from __future__ import annotations

from collections.abc import Callable

from src.models.commands import CommandRequest, CommandResult
from src.models.competition import CompetitionConnectionState, KamikazeEvent, LockEvent
from src.models.mission import MapContact, MapOverlay, MissionState
from src.models.orin import OrinMessage
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.target_lock import TargetLockVisionState, TargetLockWorkflowState
from src.models.parameters import Parameter
from src.services.contracts.commands import CommandService
from src.services.contracts.competition import CompetitionService
from src.services.contracts.mission import MissionService
from src.services.contracts.orin import OrinService
from src.services.contracts.parameters import ParameterService
from src.services.contracts.telemetry import TelemetryService
from src.services.contracts.settings import SettingsRepository
from src.services.contracts.target_lock import TargetLockService, TargetLockVisionService


class BackendFacade:
    """Qt, web, CLI ve otomasyonların paylaştığı kararlı backend yüzeyi."""

    def __init__(
        self,
        *,
        telemetry: TelemetryService,
        competition: CompetitionService,
        mission: MissionService,
        orin: OrinService,
        commands: CommandService,
        parameters: ParameterService,
        settings: SettingsRepository,
        target_lock: TargetLockService,
        target_lock_vision: TargetLockVisionService,
    ) -> None:
        self.telemetry = telemetry
        self.competition = competition
        self.mission = mission
        self.orin = orin
        self.commands = commands
        self.parameters = parameters
        self.settings = settings
        self.target_lock = target_lock
        self.target_lock_vision = target_lock_vision

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

    def target_lock_snapshot(self) -> TargetLockWorkflowState:
        return self.target_lock.state

    def target_lock_vision_snapshot(self) -> TargetLockVisionState:
        return self.target_lock_vision.target_lock_state

    def subscribe_target_lock(
        self,
        callback: Callable[[TargetLockWorkflowState], None],
    ):
        return self.target_lock.changed.subscribe(callback)

    def subscribe_target_lock_vision(
        self,
        callback: Callable[[TargetLockVisionState], None],
    ):
        return self.target_lock_vision.target_lock_changed.subscribe(callback)

    def mission_snapshot(self) -> MissionState:
        return self.mission.state

    def orin_snapshot(self):
        return self.orin.state

    def issue_orin(self, packet_type: str, payload: dict | None = None):
        return self.orin.issue(packet_type, payload or {})

    def subscribe_orin(self, callback: Callable[[OrinMessage], None]):
        return self.orin.received.subscribe(callback)

    def upsert_mission_item(self, item: MapOverlay) -> None:
        self.mission.upsert_overlay(item)

    def remove_mission_item(self, item_id: str) -> None:
        self.mission.remove_overlay(item_id)

    def upsert_contact(self, contact: MapContact) -> None:
        self.mission.upsert_contact(contact)

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
        return self.issue(
            "MISSION_UPLOAD",
            {"overlays": [item.to_dict() for item in self.mission.state.overlays]},
            source=source,
        )
