from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.services.base import CommandService, MissionService, TelemetryService, VideoService
from src.services.logging_service import LoggingService
from src.services.mock import (
    MockCommandService,
    MockMissionService,
    MockTelemetryService,
    MockVideoService,
)
from src.services.planning import (
    LocalCommandService,
    PassiveVideoService,
    PlanningMissionService,
    ZeroTelemetryService,
)
from src.services.settings import SettingsService


@dataclass(slots=True)
class RuntimeServices:
    log_service: LoggingService
    settings_service: SettingsService
    telemetry_service: TelemetryService
    mission_service: MissionService
    command_service: CommandService
    video_service: VideoService

    def start(self) -> None:
        self.telemetry_service.start()
        self.mission_service.start()
        self.video_service.start()

    def stop(self) -> None:
        self.telemetry_service.stop()
        self.video_service.stop()


def create_runtime_services(base_dir: Path) -> RuntimeServices:
    settings_service = SettingsService(base_dir=base_dir)
    log_service = LoggingService()

    if settings_service.current.mock_mode:
        telemetry_service = MockTelemetryService(log_service.child("TELEM"))
        mission_service = MockMissionService(log_service.child("MISSION"))
        command_service = MockCommandService(log_service.child("CMD"))
        video_service = MockVideoService(log_service.child("VIDEO"))
        telemetry_service.telemetry_updated.connect(video_service.consume_snapshot)
    else:
        telemetry_service = ZeroTelemetryService(log_service.child("TELEM"))
        mission_service = PlanningMissionService(log_service.child("MISSION"))
        command_service = LocalCommandService(log_service.child("CMD"))
        video_service = PassiveVideoService(log_service.child("VIDEO"))

    return RuntimeServices(
        log_service=log_service,
        settings_service=settings_service,
        telemetry_service=telemetry_service,
        mission_service=mission_service,
        command_service=command_service,
        video_service=video_service,
    )
