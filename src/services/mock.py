from __future__ import annotations

import math
import random
import uuid
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

from src.models.logs import LogEntry
from src.models.mission import MapOverlay, MissionState
from src.models.telemetry import TelemetrySnapshot
from src.services.base import CommandService, MissionService, TelemetryService, VideoService
from src.services.logging_service import ServiceLogger


class MockTelemetryService(TelemetryService):
    def __init__(self, logger: ServiceLogger | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = logger
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._phase = 0.0
        self._packet_count = 16000

    def start(self) -> None:
        self._timer.start(120)
        if self._logger:
            self._logger.success("Mock telemetry akisi baslatildi")

    def stop(self) -> None:
        self._timer.stop()
        if self._logger:
            self._logger.warning("Mock telemetry akisi durduruldu")

    def _tick(self) -> None:
        self._phase += 0.07
        lat = 39.8680 + math.sin(self._phase * 0.28) * 0.0031
        lon = 32.7485 + math.cos(self._phase * 0.31) * 0.0044
        self._packet_count += random.randint(18, 31)
        snapshot = TelemetrySnapshot(
            team_id=17,
            latitude=lat,
            longitude=lon,
            altitude_agl=137.0 + math.sin(self._phase) * 18.0,
            altitude_msl=1082.0 + math.sin(self._phase * 0.44) * 26.0,
            ground_speed=27.0 + math.sin(self._phase * 0.7) * 4.0,
            air_speed=29.5 + math.cos(self._phase * 0.66) * 3.2,
            roll=math.sin(self._phase * 0.9) * 28.0,
            pitch=math.cos(self._phase * 0.62) * 11.0,
            yaw=(self._phase * 17.0) % 360.0,
            heading=(self._phase * 16.0 + 25.0) % 360.0,
            climb_rate=math.sin(self._phase * 0.52) * 3.0,
            battery_percent=max(26, 94 - int(self._phase * 0.18)),
            battery_voltage=24.7 - self._phase * 0.006,
            link_quality=89 + int(math.sin(self._phase * 0.22) * 7),
            gps_satellites=18 + int((math.sin(self._phase * 0.34) + 1.0) * 2),
            gps_fix="RTK" if int(self._phase) % 11 else "3D FIX",
            flight_mode=["AUTO", "GUIDED", "LOITER", "AUTO"][int(self._phase // 12) % 4],
            mission_index=4 + int(self._phase // 10) % 3,
            mission_total=9,
            armed=True,
            telemetry_link=True,
            video_link=True,
            autopilot_link=True,
            server_link=True,
            target_distance=420.0 + math.cos(self._phase * 0.45) * 80.0,
            wind_speed=9.0 + math.sin(self._phase * 0.38) * 2.2,
            wind_heading=(self._phase * 9.0 + 240.0) % 360.0,
            cpu_load=42 + int(math.sin(self._phase * 0.29) * 8),
            packets_in=self._packet_count,
            packets_out=self._packet_count - 1040,
            bandwidth_kbps=96.0 + math.sin(self._phase * 0.57) * 22.0,
            throttle_percent=58.0 + math.sin(self._phase * 0.42) * 8.0,
            home_distance=180.0 + math.cos(self._phase * 0.35) * 25.0,
            home_heading=(self._phase * 11.0 + 210.0) % 360.0,
            wp_distance=95.0 + math.cos(self._phase * 0.61) * 20.0,
            nav_bearing=(self._phase * 13.0 + 35.0) % 360.0,
            rc_rssi=86 + int(math.sin(self._phase * 0.26) * 6),
            ekf_ok=True,
            system_status="ACTIVE",
            warnings=["VIDEO JITTER"] if self._phase % 27.0 > 24.0 else [],
            timestamp=datetime.utcnow(),
        )
        self.telemetry_updated.emit(snapshot)


class MockMissionService(MissionService):
    mission_log_generated = pyqtSignal(object)

    def __init__(self, logger: ServiceLogger | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = logger
        self._state = MissionState(
            overlays=[
                MapOverlay(
                    overlay_id=uuid.uuid4().hex,
                    overlay_type="polygon",
                    label="Operasyon Alani",
                    points=[
                        [39.8706, 32.7423],
                        [39.8697, 32.7541],
                        [39.8631, 32.7532],
                        [39.8627, 32.7430],
                    ],
                    color="#3BA7FF",
                ),
                MapOverlay(
                    overlay_id=uuid.uuid4().hex,
                    overlay_type="no_fly",
                    label="Yasak Bolge",
                    points=[
                        [39.8682, 32.7478],
                        [39.8677, 32.7496],
                        [39.8662, 32.7487],
                        [39.8666, 32.7469],
                    ],
                    color="#FF6363",
                ),
                MapOverlay(
                    overlay_id=uuid.uuid4().hex,
                    overlay_type="polyline",
                    label="Ana Rota",
                    points=[
                        [39.8641, 32.7446],
                        [39.8659, 32.7469],
                        [39.8677, 32.7481],
                        [39.8687, 32.7505],
                    ],
                    color="#5ECAA7",
                ),
            ],
        )

    def start(self) -> None:
        self.mission_state_changed.emit(self._state)
        if self._logger:
            self._logger.info("Mock gorev katmani yuklendi")

    @property
    def state(self) -> MissionState:
        return self._state

    def set_base_layer(self, layer: str) -> None:
        self._state = self._state.copy(base_layer=layer)
        self.mission_state_changed.emit(self._state)
        if self._logger:
            self._logger.info(f"Harita katmani degisti: {layer}")

    def set_active_tool(self, tool: str) -> None:
        self._state = self._state.copy(active_tool=tool)
        self.mission_state_changed.emit(self._state)

    def upsert_overlay(self, overlay: MapOverlay) -> None:
        overlays = [item for item in self._state.overlays if item.overlay_id != overlay.overlay_id]
        overlays.append(overlay)
        self._state = self._state.copy(overlays=overlays, selected_overlay_id=overlay.overlay_id)
        self.mission_state_changed.emit(self._state)
        self.mission_log_generated.emit(
            LogEntry(
                source="MAP",
                severity="info",
                message=f"{overlay.label} guncellendi",
            )
        )
        if self._logger:
            self._logger.info(f"{overlay.label} guncellendi")

    def remove_overlay(self, overlay_id: str) -> None:
        overlays = [item for item in self._state.overlays if item.overlay_id != overlay_id]
        self._state = self._state.copy(overlays=overlays, selected_overlay_id=None)
        self.mission_state_changed.emit(self._state)
        self.mission_log_generated.emit(
            LogEntry(
                source="MAP",
                severity="warning",
                message=f"Overlay silindi: {overlay_id[:6]}",
            )
        )
        if self._logger:
            self._logger.warning(f"Overlay silindi: {overlay_id[:6]}")

    def clear_dynamic_overlays(self) -> None:
        self._state = self._state.copy(overlays=[], selected_overlay_id=None)
        self.mission_state_changed.emit(self._state)
        self.mission_log_generated.emit(
            LogEntry(source="MAP", severity="warning", message="Mission plan temizlendi")
        )
        if self._logger:
            self._logger.warning("Mission plan temizlendi")

    def clear_all(self) -> None:
        self._state = self._state.copy(
            overlays=[],
            contacts=[],
            selected_overlay_id=None,
            active_tool="pan",
        )
        self.mission_state_changed.emit(self._state)
        self.mission_log_generated.emit(
            LogEntry(source="MAP", severity="warning", message="Tum harita ogeleri temizlendi")
        )
        if self._logger:
            self._logger.warning("Tum harita ogeleri temizlendi")

    def clear_contacts(self) -> None:
        self._state = self._state.copy(contacts=[])
        self.mission_state_changed.emit(self._state)

    def select_overlay(self, overlay_id: str | None) -> None:
        self._state = self._state.copy(selected_overlay_id=overlay_id)
        self.mission_state_changed.emit(self._state)


class MockCommandService(CommandService):
    def __init__(self, logger: ServiceLogger | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = logger

    def issue_command(self, command: str, params: dict | None = None) -> None:
        payload = params or {}
        self.command_dispatched.emit(command, payload)
        pretty_params = ", ".join(f"{key}={value}" for key, value in payload.items())
        suffix = f" ({pretty_params})" if pretty_params else ""
        severity = "danger" if command in {"DISARM", "LAND"} else "info"
        if command in {"ARM", "AUTO", "MISSION_START"}:
            severity = "success"
        self.log_generated.emit(
            LogEntry(
                source="CMD",
                severity=severity,
                message=f"{command} komutu gonderildi{suffix}",
            )
        )
        if self._logger:
            self._logger.emit(severity, f"{command} komutu gonderildi{suffix}")


class MockVideoService(VideoService):
    def __init__(self, logger: ServiceLogger | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = logger
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._emit_frame)
        self._last_snapshot: TelemetrySnapshot | None = None
        self._frame_index = 0

    def start(self) -> None:
        self._timer.start(66)
        if self._logger:
            self._logger.success("Mock video akisi baslatildi")

    def stop(self) -> None:
        self._timer.stop()
        if self._logger:
            self._logger.warning("Mock video akisi durduruldu")

    @pyqtSlot(object)
    def consume_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._last_snapshot = snapshot

    def _emit_frame(self) -> None:
        width, height = 1280, 720
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#05080c"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        horizon_shift = 0
        heading = 0.0
        mode = "LINK WAIT"
        battery = "--"
        if self._last_snapshot:
            horizon_shift = int(self._last_snapshot.pitch * 5.0)
            heading = self._last_snapshot.heading
            mode = self._last_snapshot.flight_mode
            battery = f"{self._last_snapshot.battery_percent}%"

        sky = QColor("#23384d")
        ground = QColor("#4a3624")
        painter.fillRect(0, 0, width, height // 2 + horizon_shift, sky)
        painter.fillRect(0, height // 2 + horizon_shift, width, height // 2 - horizon_shift, ground)

        painter.setOpacity(0.18)
        for idx in range(0, width, 80):
            painter.fillRect(idx, 0, 1, height, QColor("#94bde5"))
        painter.setOpacity(1.0)

        painter.setPen(QPen(QColor("#8cd2ff"), 2))
        crosshair = QPainterPath()
        crosshair.moveTo(width / 2 - 50, height / 2)
        crosshair.lineTo(width / 2 + 50, height / 2)
        crosshair.moveTo(width / 2, height / 2 - 50)
        crosshair.lineTo(width / 2, height / 2 + 50)
        painter.drawPath(crosshair)

        painter.setPen(QPen(QColor("#f4b942"), 2))
        for idx in range(-3, 4):
            y = height / 2 + horizon_shift + idx * 40
            painter.drawLine(int(width / 2 - 90), int(y), int(width / 2 - 40), int(y))
            painter.drawLine(int(width / 2 + 40), int(y), int(width / 2 + 90), int(y))

        painter.setPen(QPen(QColor("#dce8f4"), 1))
        painter.setFont(QFont("Noto Sans", 10, QFont.Weight.Bold))
        timestamp = datetime.utcnow().strftime("%H:%M:%S.%f")[:-4]
        painter.drawText(20, 28, f"Kuzey Takimi / CAM-A / {timestamp}")
        painter.drawText(20, 54, f"MODE {mode}")
        painter.drawText(20, 80, f"BAT {battery}")
        painter.drawText(20, 106, f"HDG {heading:05.1f}")

        painter.drawText(width - 150, 28, "LOW-LATENCY")
        painter.drawText(width - 150, 54, "H.265 / AUTO")
        painter.drawText(width - 150, 80, "STREAM OK")

        painter.setPen(QPen(QColor(255, 255, 255, 24), 1))
        noise_seed = self._frame_index % 12
        for row in range(noise_seed, height, 24):
            painter.drawLine(0, row, width, row)

        painter.end()
        self._frame_index += 1
        self.frame_ready.emit(image)
