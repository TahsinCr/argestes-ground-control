from __future__ import annotations

import threading
from collections.abc import Iterable
from collections.abc import Callable
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventBus, Subscription
from src.models.mission import MapContact, MapOverlay, MissionState
from src.services.abstracts.vehicle import MissionTransferInterrupted
from src.extension.api.vehicle import MissionControl
from ..api.planner import PlannerMapApi
from src.services.logging_service import ServiceLogger


class PlannerService(Service):
    """Manage GCS planning and map references without executing missions."""

    DEFAULT_COLORS = {
        "takeoff": "#78b7ff",
        "waypoint": "#74d99f",
        "loiter": "#3f9f63",
        "rtl": "#f4b942",
        "land": "#2f7ed8",
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
        vehicle: MissionControl | None = None,
        sync_from_vehicle_on_connect: bool = True,
    ) -> None:
        self._logger = logger
        self._changed = EventBus[MissionState]()
        self._lock = threading.RLock()
        self._state = initial_state or self._initial_state()
        self._issued_indexes: dict[str, int] = {}
        self._vehicle = vehicle
        self._sync_from_vehicle_on_connect = sync_from_vehicle_on_connect
        self._vehicle_subscription: Subscription | None = None
        self._sync_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._sync_generation = 0
        self._default_radii = {
            "takeoff": 35.0,
            "waypoint": 35.0,
            "loiter": 75.0,
            "rtl": 75.0,
            "land": 35.0,
        }
        self.set_default_radii(default_radii or {})
        self.api = PlannerMapApi(self)

    @property
    def changed(self) -> EventBus[MissionState]:
        return self._changed

    @property
    def state(self) -> MissionState:
        with self._lock:
            return MissionState.from_dict(self._state.to_dict())

    def start(self) -> None:
        with self._lock:
            self._sync_generation += 1
            self._stop_event.clear()
        self._changed.publish(self.state)
        self._log("info", "mission.planner_started", "YKİ planlayıcısı hazır")
        if self._vehicle is not None and self._vehicle_subscription is None:
            self._vehicle_subscription = self._vehicle.connection_changed.subscribe(
                self._on_vehicle_connection
            )
            if self._vehicle.connected and self._sync_from_vehicle_on_connect:
                self._schedule_vehicle_sync()

    def stop(self) -> None:
        with self._lock:
            self._sync_generation += 1
            self._stop_event.set()
            subscription = self._vehicle_subscription
            self._vehicle_subscription = None
            thread = self._sync_thread
        if subscription is not None:
            subscription.cancel()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if thread is None or not thread.is_alive():
                if self._sync_thread is thread:
                    self._sync_thread = None

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

    def set_mission_loop(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self.state.mission_loop == normalized:
            return
        self._mutate(mission_loop=normalized)
        self._log(
            "info",
            "mission.loop_enabled" if normalized else "mission.loop_disabled",
            "Görev tekrar modu döngü olarak ayarlandı"
            if normalized
            else "Görev tekrar modu tek sefer olarak ayarlandı",
        )

    def select_overlay(self, overlay_id: str | None) -> None:
        self._mutate(selected_overlay_id=overlay_id)

    def set_overlays(self, overlays: Iterable[MapOverlay]) -> None:
        replacement = [MapOverlay.from_dict(item.to_dict()) for item in overlays]
        identifiers = [item.overlay_id for item in replacement]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Görev öğesi kimlikleri benzersiz olmalı")
        if [item.to_dict() for item in self.state.overlays] == [
            item.to_dict() for item in replacement
        ]:
            return
        self._mutate(overlays=replacement, selected_overlay_id=None)
        self._log(
            "info",
            "mission.external_updated",
            "Görev planı harici kaynaktan güncellendi",
        )

    def upsert_overlay(self, overlay: MapOverlay) -> None:
        snapshot = MapOverlay.from_dict(overlay.to_dict())
        current = next(
            (
                item
                for item in self.state.overlays
                if item.overlay_id == snapshot.overlay_id
            ),
            None,
        )
        if current is not None and current.to_dict() == snapshot.to_dict():
            return
        if current is not None and current.is_source_managed:
            self._log(
                "warning",
                "mission.item_update_rejected",
                f"Harici kaynak tarafindan yonetilen harita ogesi degistirilemez: {snapshot.overlay_id}",
                {"overlay_id": snapshot.overlay_id},
            )
            return

        def update(state: MissionState) -> dict[str, Any]:
            overlays = [
                snapshot if item.overlay_id == snapshot.overlay_id else item
                for item in state.overlays
            ]
            if not any(
                item.overlay_id == snapshot.overlay_id for item in state.overlays
            ):
                overlays.append(snapshot)
            return {"overlays": overlays, "selected_overlay_id": snapshot.overlay_id}

        self._transform(update)
        self._log(
            "info",
            "mission.item_saved",
            f"{overlay.label} kaydedildi",
            {"label": overlay.label, "overlay_id": overlay.overlay_id},
        )

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
                "mission.item_delete_rejected",
                f"Harici kaynak tarafindan yonetilen harita ogesi silinemez: {overlay_id}",
                {"overlay_id": overlay_id},
            )
            return
        self._transform(
            lambda state: {
                "overlays": [
                    item for item in state.overlays if item.overlay_id != overlay_id
                ],
                "selected_overlay_id": None,
            }
        )
        self._log(
            "warning",
            "mission.item_deleted",
            f"Görev öğesi silindi: {overlay_id}",
            {"overlay_id": overlay_id},
        )

    def clear_dynamic_overlays(self) -> None:
        state = self.state
        retained = [item for item in state.overlays if item.is_source_managed]
        if len(retained) == len(state.overlays) and not state.mission_loop:
            return
        selected = state.selected_overlay_id
        if selected and not any(item.overlay_id == selected for item in retained):
            selected = None
        self._mutate(
            overlays=retained,
            selected_overlay_id=selected,
            mission_loop=False,
        )
        self._log(
            "warning",
            "mission.local_cleared",
            "Kullanici görev planındaki harita öğeleri temizlendi",
        )

    def clear_all(self) -> None:
        state = self.state
        if (
            not state.overlays
            and not state.contacts
            and state.active_tool == "pan"
            and not state.mission_loop
        ):
            return
        self._mutate(
            overlays=[],
            contacts=[],
            selected_overlay_id=None,
            active_tool="pan",
            mission_loop=False,
        )
        self._log(
            "warning",
            "mission.all_cleared",
            "Görev ve temas durumu temizlendi",
        )

    def upsert_contact(self, contact: MapContact) -> None:
        snapshot = MapContact.from_dict(contact.to_dict())

        def update(state: MissionState) -> dict[str, Any]:
            existing = next(
                (
                    item
                    for item in state.contacts
                    if item.contact_id == snapshot.contact_id
                ),
                None,
            )
            trail = list(
                snapshot.trail_points or (existing.trail_points if existing else [])
            )
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
                replacement.extend(
                    contact for contact in state.contacts if contact.source != source
                )
            for snapshot in snapshots:
                existing = existing_by_id.get(snapshot.contact_id)
                trail = list(
                    snapshot.trail_points or (existing.trail_points if existing else [])
                )
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
                "contacts": [
                    item for item in state.contacts if item.contact_id != contact_id
                ]
            }
        )

    def clear_contacts(self) -> None:
        self._mutate(contacts=[])

    def load_state(self, state: MissionState) -> None:
        restorable = [
            MapOverlay.from_dict(item.to_dict())
            for item in state.overlays
            if item.is_archivable
        ]
        self._transform(
            lambda current: {
                "overlays": [
                    *[item for item in current.overlays if item.is_source_managed],
                    *restorable,
                ],
                "base_layer": state.base_layer,
                "active_tool": "pan",
                "selected_overlay_id": None,
                "mission_loop": state.mission_loop,
            }
        )

    def replace_local_plan(
        self,
        overlays: Iterable[MapOverlay],
        *,
        mission_loop: bool,
    ) -> None:
        """Restore operator-owned map items while preserving live source layers."""

        local = [MapOverlay.from_dict(item.to_dict()) for item in overlays]
        if any(item.is_source_managed for item in local):
            raise ValueError(
                "Harici kaynak katmanları yerel plan olarak geri yüklenemez"
            )
        identifiers = [item.overlay_id for item in local]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Görev öğesi kimlikleri benzersiz olmalı")

        def restore(current: MissionState) -> dict[str, Any]:
            sources = [item for item in current.overlays if item.is_source_managed]
            selected = current.selected_overlay_id
            restored_ids = {item.overlay_id for item in local}
            if selected not in restored_ids:
                selected = None
            return {
                "overlays": [*local, *sources],
                "selected_overlay_id": selected,
                "mission_loop": bool(mission_loop),
            }

        self._transform(restore)

    def _mutate(self, **changes: Any) -> None:
        self._transform(lambda _state: changes)

    def _transform(self, transform: Callable[[MissionState], dict[str, Any]]) -> None:
        with self._lock:
            changes = transform(self._state)
            changes["revision"] = self._state.revision + 1
            self._state = self._state.copy(**changes)
            snapshot = MissionState.from_dict(self._state.to_dict())
        self._changed.publish(snapshot)

    def _log(
        self,
        severity: str,
        action: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._logger:
            self._logger.event(
                action,
                message,
                severity=severity,
                details=details,
            )

    def _on_vehicle_connection(self, state) -> None:
        if state.connected and self._sync_from_vehicle_on_connect:
            self._schedule_vehicle_sync()

    def request_vehicle_sync(self) -> bool:
        """Fetch the vehicle-controller mission into the shared planner state."""

        if self._vehicle is None or not self._vehicle.connected:
            self._log(
                "warning",
                "mission.download_unavailable",
                "Araç denetleyicisine bağlı değilken görev planı alınamaz",
            )
            return False
        return self._schedule_vehicle_sync()

    def _schedule_vehicle_sync(self) -> bool:
        if self._vehicle is None:
            return False
        with self._lock:
            if self._stop_event.is_set():
                return False
            if self._sync_thread is not None and self._sync_thread.is_alive():
                return False
            generation = self._sync_generation
            self._sync_thread = threading.Thread(
                target=self._sync_from_vehicle,
                args=(generation,),
                name="VehicleMissionSync",
                daemon=True,
            )
            self._sync_thread.start()
            return True

    def _sync_from_vehicle(self, generation: int | None = None) -> None:
        if self._vehicle is None:
            return
        with self._lock:
            generation = (
                self._sync_generation if generation is None else generation
            )
            if self._stop_event.is_set() or generation != self._sync_generation:
                return
            expected_overlays = tuple(
                item.to_dict() for item in self._state.overlays
            )
            expected_loop = self._state.mission_loop
        try:
            overlays = self._vehicle.download_mission()
            with self._lock:
                if (
                    self._stop_event.is_set()
                    or generation != self._sync_generation
                ):
                    return
                applied = self._apply_vehicle_mission(
                    overlays,
                    expected_overlays=expected_overlays,
                    expected_loop=expected_loop,
                )
            if not applied:
                self._log(
                    "warning",
                    "mission.download_conflict",
                    "Yerel görev indirme sırasında değişti; araç planı uygulanmadı",
                    {"downloaded_overlay_count": len(overlays)},
                )
                return
        except MissionTransferInterrupted:
            # A manual upload has priority; a future reconnect can refresh the plan.
            return
        except Exception as exc:
            if self._logger and self._sync_is_current(generation):
                self._logger.event(
                    "mission.download_failed",
                    f"Araç denetleyicisinden görev planı alınamadı: {exc}",
                    severity="danger",
                    device_id=self._vehicle.state.device_id
                    if self._vehicle.connected
                    else None,
                )
        else:
            if self._logger and self._sync_is_current(generation):
                self._logger.event(
                    "mission.state_synchronized",
                    f"Backend görev durumu {len(overlays)} araç öğesiyle eşitlendi",
                    severity="success",
                    details={"overlay_count": len(overlays)},
                    device_id=self._vehicle.state.device_id
                    if self._vehicle.connected
                    else None,
                )
        finally:
            reschedule = False
            with self._lock:
                if self._sync_thread is threading.current_thread():
                    self._sync_thread = None
                    reschedule = (
                        generation != self._sync_generation
                        and not self._stop_event.is_set()
                    )
            if reschedule:
                self._schedule_vehicle_sync()

    def _sync_is_current(self, generation: int) -> bool:
        with self._lock:
            return (
                not self._stop_event.is_set()
                and generation == self._sync_generation
            )

    def _apply_vehicle_mission(
        self,
        overlays: Iterable[MapOverlay],
        *,
        expected_overlays: tuple[dict[str, Any], ...],
        expected_loop: bool,
    ) -> bool:
        replacement = [MapOverlay.from_dict(item.to_dict()) for item in overlays]
        vehicle_loop = any(
            bool(item.metadata.get("mission_loop", False)) for item in replacement
        )
        with self._lock:
            current_overlays = tuple(
                item.to_dict() for item in self._state.overlays
            )
            if (
                self._stop_event.is_set()
                or current_overlays != expected_overlays
                or self._state.mission_loop != expected_loop
            ):
                return False
            retained = [
                item
                for item in self._state.overlays
                if item.is_source_managed or not item.is_vehicle_mission
            ]
            combined = [*retained, *replacement]
            identifiers = [item.overlay_id for item in combined]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("Görev öğesi kimlikleri benzersiz olmalı")
            if (
                [item.to_dict() for item in self._state.overlays]
                == [item.to_dict() for item in combined]
                and self._state.mission_loop == vehicle_loop
            ):
                return True
            self._state = self._state.copy(
                overlays=combined,
                selected_overlay_id=None,
                mission_loop=vehicle_loop,
                revision=self._state.revision + 1,
            )
            snapshot = MissionState.from_dict(self._state.to_dict())
        self._changed.publish(snapshot)
        return True

    @staticmethod
    def _initial_state() -> MissionState:
        return MissionState(contacts=[])
