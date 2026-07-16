from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage


class TelemetryService(QObject):
    telemetry_updated = pyqtSignal(object)

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class VideoService(QObject):
    frame_ready = pyqtSignal(QImage)

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class MissionService(QObject):
    mission_state_changed = pyqtSignal(object)

    def start(self) -> None:
        raise NotImplementedError


class CommandService(QObject):
    log_generated = pyqtSignal(object)
    command_dispatched = pyqtSignal(str, dict)

    def issue_command(self, command: str, params: dict | None = None) -> None:
        raise NotImplementedError
