from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

from src.models.logs import LogEntry
from src.models.mission import MapContact, MapOverlay, MissionState
from src.models.telemetry import TelemetrySnapshot
from src.services.base import CommandService, MissionService, TelemetryService, VideoService
from src.services.logging_service import ServiceLogger


class MissionMapApi:
    def __init__(self, mission_service: "PlanningMissionService") -> None:
        self._mission_service = mission_service

    def set_mission_point(
        self,
        point_type: str,
        *,
        latitude: float,
        longitude: float,
        overlay_id: str | None = None,
        label: str | None = None,
        color: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        overlay = MapOverlay(
            overlay_id=overlay_id or self._mission_service.next_overlay_id(point_type),
            overlay_type=point_type,
            label=label or point_type.upper(),
            points=[[latitude, longitude]],
            radius_m=self._mission_service.default_radius(point_type),
            color=color or self._mission_service.default_color(point_type),
            metadata=metadata or {},
        )
        self._mission_service.upsert_overlay(overlay)
        return overlay.overlay_id

    def set_mission_circle(
        self,
        circle_type: str,
        *,
        latitude: float,
        longitude: float,
        radius_m: float,
        overlay_id: str | None = None,
        label: str | None = None,
        color: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        overlay = MapOverlay(
            overlay_id=overlay_id or self._mission_service.next_overlay_id(circle_type),
            overlay_type=circle_type,
            label=label or circle_type.upper(),
            points=[[latitude, longitude]],
            radius_m=radius_m,
            color=color or self._mission_service.default_color(circle_type),
            metadata=metadata or {},
        )
        self._mission_service.upsert_overlay(overlay)
        return overlay.overlay_id

    def set_mission_polyline(
        self,
        *,
        points: list[list[float]],
        overlay_id: str | None = None,
        label: str = "ROUTE",
        color: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        overlay = MapOverlay(
            overlay_id=overlay_id or self._mission_service.next_overlay_id("route"),
            overlay_type="route",
            label=label,
            points=points,
            radius_m=None,
            color=color or self._mission_service.default_color("route"),
            metadata=metadata or {},
        )
        self._mission_service.upsert_overlay(overlay)
        return overlay.overlay_id

    def set_mission_polygon(
        self,
        polygon_type: str,
        *,
        points: list[list[float]],
        overlay_id: str | None = None,
        label: str | None = None,
        color: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        overlay = MapOverlay(
            overlay_id=overlay_id or self._mission_service.next_overlay_id(polygon_type),
            overlay_type=polygon_type,
            label=label or polygon_type.upper(),
            points=points,
            radius_m=None,
            color=color or self._mission_service.default_color(polygon_type),
            metadata=metadata or {},
        )
        self._mission_service.upsert_overlay(overlay)
        return overlay.overlay_id

    def replace_overlays(self, overlays: Iterable[MapOverlay]) -> None:
        self._mission_service.set_overlays(overlays)

    def remove_overlay(self, overlay_id: str) -> None:
        self._mission_service.remove_overlay(overlay_id)

    def clear_overlays(self) -> None:
        self._mission_service.clear_dynamic_overlays()

    def update_contact(
        self,
        *,
        contact_id: str,
        label: str,
        latitude: float,
        longitude: float,
        heading: float = 0.0,
        speed: float = 0.0,
        color: str = "#ff6b6b",
        source: str = "external",
        metadata: dict | None = None,
    ) -> None:
        self._mission_service.upsert_contact(
            MapContact(
                contact_id=contact_id,
                label=label,
                latitude=latitude,
                longitude=longitude,
                heading=heading,
                speed=speed,
                color=color,
                source=source,
                metadata=metadata or {},
            )
        )

    def remove_contact(self, contact_id: str) -> None:
        self._mission_service.remove_contact(contact_id)

    def clear_contacts(self) -> None:
        self._mission_service.clear_contacts()

    def clear_all(self) -> None:
        self._mission_service.clear_all()

    def load_state(self, state: MissionState) -> None:
        self._mission_service.load_state(state)


class PlanningMissionService(MissionService):
    mission_log_generated = pyqtSignal(object)

    DEFAULT_COLORS = {
        "takeoff": "#78b7ff",
        "waypoint": "#74d99f",
        "loiter": "#3f9f63",
        "no_fly": "#eb6464",
        "safe_zone": "#73a95b",
        "hss": "#eb6464",
        "land": "#2f7ed8",
        "goto": "#61a8ff",
    }

    DEFAULT_RADII = {
        "takeoff": 35.0,
        "waypoint": 35.0,
        "loiter": 75.0,
        "land": 35.0,
        "hss": 100.0,
    }

    def __init__(
        self,
        logger: ServiceLogger | None = None,
        initial_state: MissionState | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = logger
        self._state = initial_state or self._initial_state()
        self._default_radii = dict(self.DEFAULT_RADII)
        self.api = MissionMapApi(self)

    @property
    def state(self) -> MissionState:
        return self._state

    def start(self) -> None:
        self.mission_state_changed.emit(self._state)
        if self._logger:
            self._logger.info("Mission planning servisi hazir")

    def default_color(self, overlay_type: str) -> str:
        return self.DEFAULT_COLORS.get(overlay_type, "#61a8ff")

    def default_radius(self, overlay_type: str) -> float:
        return self._default_radii.get(overlay_type, 35.0)

    def set_default_radii(self, values: dict[str, int | float]) -> None:
        self._default_radii.update(
            {key: float(value) for key, value in values.items() if key in self.DEFAULT_RADII}
        )

    def _initial_state(self) -> MissionState:
        return MissionState(
            contacts=[
                MapContact(
                    contact_id="own-siha",
                    label="Ana SIHA",
                    latitude=39.8672,
                    longitude=32.7481,
                    heading=38.0,
                    speed=0.0,
                    color="#58a6ff",
                    source="ownship",
                ),
                MapContact(
                    contact_id="rakip-1",
                    label="Rakip 1",
                    latitude=39.8686,
                    longitude=32.7512,
                    heading=218.0,
                    speed=0.0,
                    color="#e05d5d",
                    source="backend",
                ),
                MapContact(
                    contact_id="rakip-2",
                    label="Rakip 2",
                    latitude=39.8655,
                    longitude=32.7448,
                    heading=72.0,
                    speed=0.0,
                    color="#e05d5d",
                    source="backend",
                ),
            ]
        )

    def next_overlay_id(self, overlay_type: str) -> str:
        next_index = (
            sum(1 for item in self._state.overlays if item.overlay_type == overlay_type) + 1
        )
        return f"{overlay_type}-{next_index}"

    def set_base_layer(self, layer: str) -> None:
        self._state = self._state.copy(base_layer=layer)
        self.mission_state_changed.emit(self._state)

    def set_active_tool(self, tool: str) -> None:
        self._state = self._state.copy(active_tool=tool)
        self.mission_state_changed.emit(self._state)

    def set_overlays(self, overlays: Iterable[MapOverlay]) -> None:
        self._state = self._state.copy(overlays=list(overlays), selected_overlay_id=None)
        self.mission_state_changed.emit(self._state)
        self._emit_log("info", "Mission plan harici kaynaktan guncellendi")

    def upsert_overlay(self, overlay: MapOverlay) -> None:
        overlays: list[MapOverlay] = []
        replaced = False
        for item in self._state.overlays:
            if item.overlay_id == overlay.overlay_id:
                overlays.append(overlay)
                replaced = True
            else:
                overlays.append(item)
        if not replaced:
            overlays.append(overlay)
        self._state = self._state.copy(overlays=overlays, selected_overlay_id=overlay.overlay_id)
        self.mission_state_changed.emit(self._state)
        self._emit_log("info", f"{overlay.label} kaydedildi")

    def remove_overlay(self, overlay_id: str) -> None:
        overlays = [item for item in self._state.overlays if item.overlay_id != overlay_id]
        self._state = self._state.copy(overlays=overlays, selected_overlay_id=None)
        self.mission_state_changed.emit(self._state)
        self._emit_log("warning", f"Mission oge silindi: {overlay_id[:8]}")

    def clear_dynamic_overlays(self) -> None:
        self._state = self._state.copy(overlays=[], selected_overlay_id=None)
        self.mission_state_changed.emit(self._state)
        self._emit_log("warning", "Mission plan temizlendi")

    def clear_all(self) -> None:
        self._state = self._state.copy(
            overlays=[],
            contacts=[],
            selected_overlay_id=None,
            active_tool="pan",
        )
        self.mission_state_changed.emit(self._state)
        self._emit_log("warning", "Harita plan verisi temizlendi")

    def select_overlay(self, overlay_id: str | None) -> None:
        self._state = self._state.copy(selected_overlay_id=overlay_id)
        self.mission_state_changed.emit(self._state)

    def load_state(self, state: MissionState) -> None:
        self._state = state.copy(
            contacts=list(self._state.contacts),
            selected_overlay_id=None,
            active_tool="pan",
            base_layer=state.base_layer or self._state.base_layer,
        )
        self.mission_state_changed.emit(self._state)
        self._emit_log("success", f"Gorev plani yuklendi ({len(self._state.overlays)} oge)")

    def upsert_contact(self, contact: MapContact) -> None:
        contacts: list[MapContact] = []
        replaced = False
        updated_trail = list(contact.trail_points)
        existing_contact = next(
            (item for item in self._state.contacts if item.contact_id == contact.contact_id),
            None,
        )
        if not updated_trail:
            if existing_contact:
                updated_trail = list(existing_contact.trail_points)
            updated_trail.append([contact.latitude, contact.longitude])
        else:
            updated_trail.append([contact.latitude, contact.longitude])
        normalized_trail: list[list[float]] = []
        for point in updated_trail:
            if not normalized_trail or normalized_trail[-1] != point:
                normalized_trail.append(point)
        contact = MapContact(
            contact_id=contact.contact_id,
            label=contact.label,
            latitude=contact.latitude,
            longitude=contact.longitude,
            heading=contact.heading,
            speed=contact.speed,
            color=contact.color,
            trail_points=normalized_trail[-40:],
            source=contact.source,
            metadata=contact.metadata,
        )
        for item in self._state.contacts:
            if item.contact_id == contact.contact_id:
                contacts.append(contact)
                replaced = True
            else:
                contacts.append(item)
        if not replaced:
            contacts.append(contact)
        self._state = self._state.copy(contacts=contacts)
        self.mission_state_changed.emit(self._state)

    def remove_contact(self, contact_id: str) -> None:
        contacts = [item for item in self._state.contacts if item.contact_id != contact_id]
        self._state = self._state.copy(contacts=contacts)
        self.mission_state_changed.emit(self._state)

    def clear_contacts(self) -> None:
        self._state = self._state.copy(contacts=[])
        self.mission_state_changed.emit(self._state)

    def _emit_log(self, severity: str, message: str) -> None:
        entry = LogEntry(source="MISSION", severity=severity, message=message)
        self.mission_log_generated.emit(entry)
        if self._logger:
            self._logger.emit(severity, message)


class ZeroTelemetryService(TelemetryService):
    def __init__(self, logger: ServiceLogger | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = logger
        self._last = TelemetrySnapshot.zero()

    @property
    def current(self) -> TelemetrySnapshot:
        return self._last

    def start(self) -> None:
        self.telemetry_updated.emit(self._last)
        if self._logger:
            self._logger.info("Telemetry servisi baglanti bekliyor")

    def stop(self) -> None:
        if self._logger:
            self._logger.warning("Telemetry servisi durduruldu")

    def push_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._last = snapshot
        self.telemetry_updated.emit(snapshot)


class PassiveVideoService(VideoService):
    def __init__(self, logger: ServiceLogger | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = logger

    def start(self) -> None:
        if self._logger:
            self._logger.info("Video servisi baglanti bekliyor")

    def stop(self) -> None:
        if self._logger:
            self._logger.warning("Video servisi durduruldu")

    @pyqtSlot(QImage)
    def push_frame(self, image: QImage) -> None:
        self.frame_ready.emit(image)


class LocalCommandService(CommandService):
    def __init__(self, logger: ServiceLogger | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = logger

    def issue_command(self, command: str, params: dict | None = None) -> None:
        payload = params or {}
        self.command_dispatched.emit(command, payload)
        details = ", ".join(f"{key}={value}" for key, value in payload.items())
        suffix = f" ({details})" if details else ""
        entry = LogEntry(
            source="CMD",
            severity="warning",
            message=f"{command} komutu hazirlandi{suffix} / backend bagli degil",
        )
        self.log_generated.emit(entry)
        if self._logger:
            self._logger.warning(entry.message)
