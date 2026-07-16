from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

from src.core.dependency import (
    BaseDependencyContainer,
    DependencyContainer,
)

from src.services.vehicle_communication import BaseVehicleComunication, ArduPilotMavlinkComunication

class DependencyPriority(IntEnum):
    CONFIG = 0
    IO = 10
    MISSIONS = 20
    APPLICATION = 30

class AppContainer(BaseDependencyContainer):
    def configure(self) -> None:
        self.singleton(ConfigManager, factory=load_config, priority=DependencyPriority.CONFIG)
        self.singleton(
            abstract=BaseVehicleComunication,
            concrete=ArduPilotMavlinkComunication,
            dependencies={"config": VeicleConfig},
            priority=DependencyPriority.IO,
        )
        self.singleton(
            abstract=BaseCameraConnector,
            concrete=CameraConnector,
            dependencies={"config": AppConfig},
            priority=DependencyPriority.IO,
        )
        self.singleton(
            abstract=BaseGCSCommunication,
            concrete=GcsCommunication,
            dependencies={"config": AppConfig},
            priority=DependencyPriority.IO,
        )
        self.transient(
            concrete=SafeMission,
            dependencies={"vehicle": BaseMavlinkCommunication},
            priority=DependencyPriority.MISSIONS,
        )
        self.transient(
            concrete=SafeMission,
            dependencies={"vehicle": BaseMavlinkCommunication},
            priority=DependencyPriority.MISSIONS,
        )
        self.transient(
            concrete=LoiterMission,
            dependencies={"vehicle": BaseVehicleCommunication},
            priority=DependencyPriority.MISSIONS,
        )
        self.transient(
            concrete=TargetLockMission,
            dependencies={
                "vehicle": BaseVehicleCommunication,
                "camera": BaseCameraConnector,
            },
            priority=DependencyPriority.MISSIONS,
        )
        self.singleton(
            concrete=MissionManager,
            dependencies={
                "vehicle": BaseVehicleCommunication,
                "camera": BaseCameraConnector,
                "gcs": BaseGcsCommunication,
                "missions": MissionRegistry,
            },
            priority=DependencyPriority.APPLICATION,
        )
        self


def create_container(*, warmup: bool = False) -> AppContainer:
    container = AppContainer()
    if warmup:
        container.warmup()
    return container


__all__ = [
    "AppContainer",
    "create_container",
]
