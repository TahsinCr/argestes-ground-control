from __future__ import annotations
from typing import Any, TYPE_CHECKING
from collections.abc import Iterable
from src.models.mission import MapOverlay, MissionState

if TYPE_CHECKING:
    from ..services.planner import PlannerService


class PlannerMapApi:
    """Shared planner write API used by the UI, REST clients and tests."""

    def __init__(self, service: "PlannerService") -> None:
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

    def set_mission_loop(self, enabled: bool) -> None:
        self._service.set_mission_loop(enabled)

    def remove(self, overlay_id: str) -> None:
        self._service.remove_overlay(overlay_id)

    def replace(self, overlays: Iterable[MapOverlay]) -> None:
        self._service.set_overlays(overlays)

    def snapshot(self) -> MissionState:
        return self._service.state
