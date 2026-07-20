from __future__ import annotations

import threading
from collections.abc import Iterable
from collections.abc import Callable
from typing import Any

from src.core.events import EventStream, Subscription
from src.models.mission import MapContact, MapOverlay, MissionState
from src.services.contracts.mission import MissionService
from src.services.contracts.vehicle import VehicleService
from src.services.logging_service import ServiceLogger


class MissionMapApi:
    """UI, REST, görev motoru veya testlerin kullandığı ortak görev yazma API'si."""

    def __init__(self, service: "MissionStateService") -> None:
        self._service = service

    def set_point(
        self,
        item_type: str,
        *,
        latitude: float,
        longitude: float,
        altitude: float | None = None,
        radius_m: float | None = None,
        overlay_id: str | None = None,
        label: str | None = None,
        color: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        point = [latitude, longitude]
        if altitude is not None:
            point.append(altitude)
        overlay = MapOverlay(
            overlay_id=overlay_id or self._service.next_overlay_id(item_type),
            overlay_type=item_type,
            label=label or item_type.upper(),
            points=[point],
            radius_m=radius_m or self._service.default_radius(item_type),
            color=color or self._service.default_color(item_type),
            metadata=metadata or {},
        )
        self._service.upsert_overlay(overlay)
        return overlay.overlay_id

    def set_area(
        self,
        area_type: str,
        *,
        points: list[list[float]],
        overlay_id: str | None = None,
        label: str | None = None,
        color: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        overlay = MapOverlay(
            overlay_id=overlay_id or self._service.next_overlay_id(area_type),
            overlay_type=area_type,
            label=label or area_type.upper(),
            points=points,
            color=color or self._service.default_color(area_type),
            metadata=metadata or {},
        )
        self._service.upsert_overlay(overlay)
        return overlay.overlay_id

    def set_route(self, points: list[list[float]], **kwargs: Any) -> str:
        return self.set_area("route", points=points, **kwargs)

    def remove(self, overlay_id: str) -> None:
        self._service.remove_overlay(overlay_id)

    def replace(self, overlays: Iterable[MapOverlay]) -> None:
        self._service.set_overlays(overlays)

    def snapshot(self) -> MissionState:
        return self._service.state


class MissionStateService(MissionService):
    DEFAULT_COLORS = {
        "takeoff": "#78b7ff",
        "waypoint": "#74d99f",
        "loiter": "#3f9f63",
        "rtl": "#f4b942",
        "land": "#2f7ed8",
        "hss": "#eb6464",
        "safe_zone": "#73a95b",
        "no_fly": "#eb6464",
        "route": "#61a8ff",
        "goto": "#61a8ff",
    }

    def __init__(
        self,
        logger: ServiceLogger | None = None,
        initial_state: MissionState | None = None,
        default_radii: dict[str, int | float] | None = None,
        vehicle: VehicleService | None = None,
        sync_from_vehicle_on_connect: bool = True,
    ) -> None:
        self._logger = logger
        self._changed = EventStream[MissionState]()
        self._lock = threading.RLock()
        self._state = initial_state or self._initial_state()
        self._issued_indexes: dict[str, int] = {}
        self._vehicle = vehicle
        self._sync_from_vehicle_on_connect = sync_from_vehicle_on_connect
        self._vehicle_subscription: Subscription | None = None
        self._sync_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._default_radii = {
            "takeoff": 35.0,
            "waypoint": 35.0,
            "loiter": 75.0,
            "rtl": 75.0,
            "land": 35.0,
            "hss": 100.0,
        }
        self.set_default_radii(default_radii or {})
        self.api = MissionMapApi(self)

    @property
    def changed(self) -> EventStream[MissionState]:
        return self._changed

    @property
    def state(self) -> MissionState:
        with self._lock:
            return MissionState.from_dict(self._state.to_dict())

    def start(self) -> None:
        self._stop_event.clear()
        self._changed.publish(self.state)
        self._log("info", "Görev durum servisi hazır")
        if self._vehicle is not None and self._vehicle_subscription is None:
            self._vehicle_subscription = self._vehicle.connection_changed.subscribe(
                self._on_vehicle_connection
            )
            if self._vehicle.connected and self._sync_from_vehicle_on_connect:
                self._schedule_vehicle_sync()

    def stop(self) -> None:
        self._stop_event.set()
        if self._vehicle_subscription is not None:
            self._vehicle_subscription.cancel()
            self._vehicle_subscription = None

    def default_color(self, item_type: str) -> str:
        return self.DEFAULT_COLORS.get(item_type, "#61a8ff")

    def default_radius(self, item_type: str) -> float:
        with self._lock:
            return self._default_radii.get(item_type, 35.0)

    def set_default_radii(self, values: dict[str, int | float]) -> None:
        normalized: dict[str, float] = {}
        for key, value in values.items():
            numeric = float(value)
            if numeric <= 0:
                raise ValueError(f"{key} çapı pozitif olmalı")
            normalized[key] = numeric
        with self._lock:
            self._default_radii.update(normalized)

    def next_overlay_id(self, overlay_type: str) -> str:
        with self._lock:
            indexes = {
                int(item.overlay_id.rsplit("-", 1)[-1])
                for item in self._state.overlays
                if item.overlay_id.startswith(f"{overlay_type}-")
                and item.overlay_id.rsplit("-", 1)[-1].isdigit()
            }
            next_index = max(self._issued_indexes.get(overlay_type, 0) + 1, 1)
            while next_index in indexes:
                next_index += 1
            self._issued_indexes[overlay_type] = next_index
            return f"{overlay_type}-{next_index}"

    def set_base_layer(self, layer: str) -> None:
        if layer not in {"vector", "satellite"}:
            raise ValueError(f"Bilinmeyen harita katmanı: {layer}")
        self._mutate(base_layer=layer)

    def set_active_tool(self, tool: str) -> None:
        self._mutate(active_tool=tool)

    def select_overlay(self, overlay_id: str | None) -> None:
        self._mutate(selected_overlay_id=overlay_id)

    def set_overlays(self, overlays: Iterable[MapOverlay]) -> None:
        replacement = [MapOverlay.from_dict(item.to_dict()) for item in overlays]
        identifiers = [item.overlay_id for item in replacement]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Görev öğesi kimlikleri benzersiz olmalı")
        if [item.to_dict() for item in self.state.overlays] == [item.to_dict() for item in replacement]:
            return
        self._mutate(overlays=replacement, selected_overlay_id=None)
        self._log("info", "Görev planı harici kaynaktan güncellendi")

    def replace_source_overlays(
        self,
        source: str,
        overlays: Iterable[MapOverlay],
    ) -> None:
        normalized_source = source.strip().lower()
        if not normalized_source:
            raise ValueError("Harita öğesi kaynağı boş olamaz")
        snapshots = [MapOverlay.from_dict(item.to_dict()) for item in overlays]
        identifiers = [item.overlay_id for item in snapshots]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Kaynak harita öğesi kimlikleri benzersiz olmalı")
        if any(str(item.metadata.get("source", "")).lower() != normalized_source for item in snapshots):
            raise ValueError("Harita öğesi metadata kaynağı ile servis kaynağı eşleşmiyor")

        state = self.state
        retained = [
            item
            for item in state.overlays
            if str(item.metadata.get("source", "")).lower() != normalized_source
        ]
        replacement = [*retained, *snapshots]
        if [item.to_dict() for item in state.overlays] == [item.to_dict() for item in replacement]:
            return
        selected = state.selected_overlay_id
        if selected and not any(item.overlay_id == selected for item in replacement):
            selected = None
        self._mutate(overlays=replacement, selected_overlay_id=selected)
        self._log("info", f"{normalized_source} harita verisi güncellendi ({len(snapshots)})")

    def upsert_overlay(self, overlay: MapOverlay) -> None:
        snapshot = MapOverlay.from_dict(overlay.to_dict())
        current = next(
            (item for item in self.state.overlays if item.overlay_id == snapshot.overlay_id),
            None,
        )
        if current is not None and current.to_dict() == snapshot.to_dict():
            return
        if current is not None and current.is_source_managed:
            self._log(
                "warning",
                f"Harici kaynak tarafindan yonetilen harita ogesi degistirilemez: {snapshot.overlay_id}",
            )
            return

        def update(state: MissionState) -> dict[str, Any]:
            overlays = [
                snapshot if item.overlay_id == snapshot.overlay_id else item
                for item in state.overlays
            ]
            if not any(item.overlay_id == snapshot.overlay_id for item in state.overlays):
                overlays.append(snapshot)
            return {"overlays": overlays, "selected_overlay_id": snapshot.overlay_id}

        self._transform(update)
        self._log("info", f"{overlay.label} kaydedildi")

    def remove_overlay(self, overlay_id: str) -> None:
        overlay = next(
            (item for item in self.state.overlays if item.overlay_id == overlay_id),
            None,
        )
        if overlay is None:
            return
        if overlay.is_source_managed:
            self._log(
                "warning",
                f"Harici kaynak tarafindan yonetilen harita ogesi silinemez: {overlay_id}",
            )
            return
        self._transform(
            lambda state: {
                "overlays": [item for item in state.overlays if item.overlay_id != overlay_id],
                "selected_overlay_id": None,
            }
        )
        self._log("warning", f"Görev öğesi silindi: {overlay_id}")

    def clear_dynamic_overlays(self) -> None:
        state = self.state
        retained = [item for item in state.overlays if item.is_source_managed]
        if len(retained) == len(state.overlays):
            return
        selected = state.selected_overlay_id
        if selected and not any(item.overlay_id == selected for item in retained):
            selected = None
        self._mutate(overlays=retained, selected_overlay_id=selected)
        self._log("warning", "Kullanici görev planındaki harita öğeleri temizlendi")

    def clear_all(self) -> None:
        state = self.state
        if not state.overlays and not state.contacts and state.active_tool == "pan":
            return
        self._mutate(overlays=[], contacts=[], selected_overlay_id=None, active_tool="pan")
        self._log("warning", "Görev ve temas durumu temizlendi")

    def upsert_contact(self, contact: MapContact) -> None:
        snapshot = MapContact.from_dict(contact.to_dict())

        def update(state: MissionState) -> dict[str, Any]:
            existing = next(
                (item for item in state.contacts if item.contact_id == snapshot.contact_id),
                None,
            )
            trail = list(snapshot.trail_points or (existing.trail_points if existing else []))
            point = [snapshot.latitude, snapshot.longitude]
            if not trail or trail[-1] != point:
                trail.append(point)
            snapshot.trail_points = trail[-160:]
            contacts = [
                snapshot if item.contact_id == snapshot.contact_id else item
                for item in state.contacts
            ]
            if existing is None:
                contacts.append(snapshot)
            return {"contacts": contacts}

        self._transform(update)

    def replace_contacts(
        self,
        contacts: Iterable[MapContact],
        *,
        source: str | None = None,
    ) -> None:
        snapshots = [MapContact.from_dict(contact.to_dict()) for contact in contacts]
        identifiers = [contact.contact_id for contact in snapshots]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Temas kimlikleri benzersiz olmalı")

        def update(state: MissionState) -> dict[str, Any]:
            existing_by_id = {contact.contact_id: contact for contact in state.contacts}
            replacement: list[MapContact] = []
            if source is not None:
                replacement.extend(contact for contact in state.contacts if contact.source != source)
            for snapshot in snapshots:
                existing = existing_by_id.get(snapshot.contact_id)
                trail = list(snapshot.trail_points or (existing.trail_points if existing else []))
                point = [snapshot.latitude, snapshot.longitude]
                if not trail or trail[-1] != point:
                    trail.append(point)
                snapshot.trail_points = trail[-160:]
                replacement.append(snapshot)
            return {"contacts": replacement}

        self._transform(update)

    def remove_contact(self, contact_id: str) -> None:
        self._transform(
            lambda state: {
                "contacts": [item for item in state.contacts if item.contact_id != contact_id]
            }
        )

    def clear_contacts(self) -> None:
        self._mutate(contacts=[])

    def load_state(self, state: MissionState) -> None:
        archived = [
            MapOverlay.from_dict(item.to_dict())
            for item in state.overlays
            if item.is_archivable
        ]
        self._transform(
            lambda current: {
                "overlays": [
                    *[item for item in current.overlays if item.is_source_managed],
                    *archived,
                ],
                "base_layer": state.base_layer,
                "active_tool": "pan",
                "selected_overlay_id": None,
            }
        )

    def _mutate(self, **changes: Any) -> None:
        self._transform(lambda _state: changes)

    def _transform(self, transform: Callable[[MissionState], dict[str, Any]]) -> None:
        with self._lock:
            changes = transform(self._state)
            changes["revision"] = self._state.revision + 1
            self._state = self._state.copy(**changes)
            snapshot = MissionState.from_dict(self._state.to_dict())
        self._changed.publish(snapshot)

    def _log(self, severity: str, message: str) -> None:
        if self._logger:
            self._logger.emit(severity, message)

    def _on_vehicle_connection(self, state) -> None:
        if state.connected and self._sync_from_vehicle_on_connect:
            self._schedule_vehicle_sync()

    def _schedule_vehicle_sync(self) -> None:
        if self._vehicle is None:
            return
        with self._lock:
            if self._sync_thread is not None and self._sync_thread.is_alive():
                return
            self._sync_thread = threading.Thread(
                target=self._sync_from_vehicle,
                name="VehicleMissionSync",
                daemon=True,
            )
            self._sync_thread.start()

    def _sync_from_vehicle(self) -> None:
        if self._vehicle is None:
            return
        try:
            overlays = self._vehicle.download_mission()
            if self._stop_event.is_set():
                return
            current = self.state
            retained = [
                item
                for item in current.overlays
                if item.is_source_managed or not item.is_vehicle_mission
            ]
            self.set_overlays([*retained, *overlays])
        except Exception as exc:
            if self._logger and not self._stop_event.is_set():
                self._logger.event(
                    "mission.download_failed",
                    f"Uçuş cihazı görev planı alınamadı: {exc}",
                    severity="danger",
                    device_id=self._vehicle.state.device_id if self._vehicle.connected else None,
                )
        else:
            if self._logger:
                self._logger.event(
                    "mission.state_synchronized",
                    f"Backend görev durumu {len(overlays)} araç öğesiyle eşitlendi",
                    severity="success",
                    details={"overlay_count": len(overlays)},
                    device_id=self._vehicle.state.device_id if self._vehicle.connected else None,
                )

    @staticmethod
    def _initial_state() -> MissionState:
        return MissionState(
            contacts=[
                MapContact("own-siha", "Ana SIHA", 39.8672, 32.7481, "#58a6ff", 38.0, source="ownship"),
                MapContact("rakip-1", "Rakip 1", 39.8686, 32.7512, "#e05d5d", 218.0, source="backend"),
                MapContact("rakip-2", "Rakip 2", 39.8655, 32.7448, "#e05d5d", 72.0, source="backend"),
            ]
        )
