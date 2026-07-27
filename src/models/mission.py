from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
import math
from typing import Any

from src.core.abstracts import Model
from src.models.geometry import Point


class MissionItemType(StrEnum):
    TAKEOFF = "takeoff"
    WAYPOINT = "waypoint"
    LOITER = "loiter"
    RTL = "rtl"
    LAND = "land"
    HSS = "hss"
    SAFE_ZONE = "safe_zone"
    NO_FLY = "no_fly"
    ROUTE = "route"
    GOTO = "goto"
    POLYGON = "polygon"
    POLYLINE = "polyline"


@dataclass(slots=True)
class MapOverlay(Model):
    """Mission item transferred between the map and backend."""

    overlay_id: str
    overlay_type: str
    label: str
    points: list[list[float]]
    color: str
    radius_m: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.overlay_id = self.overlay_id.strip()
        self.overlay_type = self.overlay_type.strip().lower()
        self.label = self.label.strip() or self.overlay_type.upper()
        if not self.overlay_id:
            raise ValueError("Görev öğesi kimliği boş olamaz")
        normalized: list[list[float]] = []
        for raw in self.points:
            if len(raw) < 2:
                raise ValueError("Görev noktası enlem ve boylam içermeli")
            point = Point(float(raw[0]), float(raw[1]), float(raw[2]) if len(raw) > 2 else 0.0)
            normalized.append([point.latitude, point.longitude, *([point.altitude] if len(raw) > 2 else [])])
        self.points = normalized
        if self.radius_m is not None:
            self.radius_m = float(self.radius_m)
            if not math.isfinite(self.radius_m) or self.radius_m <= 0:
                raise ValueError("Görev öğesi çapı pozitif olmalı")
        self.metadata = dict(self.metadata)

    @property
    def point_models(self) -> tuple[Point, ...]:
        return tuple(
            Point(point[0], point[1], point[2] if len(point) > 2 else 0.0)
            for point in self.points
        )

    @property
    def route_order(self) -> int | None:
        value = self.metadata.get("route_order")
        return int(value) if value is not None else None

    @property
    def source(self) -> str:
        return str(self.metadata.get("source", "")).strip().lower()

    @property
    def is_source_managed(self) -> bool:
        """True when an external service owns this overlay's lifecycle."""

        return bool(
            self.metadata.get("read_only", False)
            or self.source.startswith("teknofest-")
            or self.overlay_type in {"hss", "target"}
        )

    @property
    def is_archivable(self) -> bool:
        return bool(self.metadata.get("persistent", True)) and not self.is_source_managed

    @property
    def is_vehicle_mission(self) -> bool:
        if self.metadata.get("mission_upload", True) is False:
            return False
        return self.overlay_type in {
            "takeoff",
            "waypoint",
            "loiter",
            "rtl",
            "land",
            "route",
        }

    def with_changes(self, **changes: Any) -> "MapOverlay":
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MapOverlay":
        return cls(**payload)


@dataclass(slots=True)
class MapContact(Model):
    contact_id: str
    label: str
    latitude: float
    longitude: float
    color: str = "#ff6b6b"
    heading: float = 0.0
    speed: float = 0.0
    trail_points: list[list[float]] = field(default_factory=list)
    source: str = "external"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Point(float(self.latitude), float(self.longitude))
        self.heading = float(self.heading) % 360.0
        self.speed = float(self.speed)
        self.trail_points = [
            [Point(float(point[0]), float(point[1])).latitude, float(point[1])]
            for point in self.trail_points
            if len(point) >= 2
        ]
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MapContact":
        return cls(**payload)


@dataclass(slots=True)
class MissionState(Model):
    """Central mission state shared by multiple read/write interfaces."""

    overlays: list[MapOverlay] = field(default_factory=list)
    contacts: list[MapContact] = field(default_factory=list)
    active_tool: str = "pan"
    base_layer: str = "vector"
    selected_overlay_id: str | None = None
    revision: int = 0
    mission_loop: bool = False

    def copy(self, **changes: Any) -> "MissionState":
        changes.setdefault("overlays", list(self.overlays))
        changes.setdefault("contacts", list(self.contacts))
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overlays": [item.to_dict() for item in self.overlays],
            "contacts": [item.to_dict() for item in self.contacts],
            "active_tool": self.active_tool,
            "base_layer": self.base_layer,
            "selected_overlay_id": self.selected_overlay_id,
            "revision": self.revision,
            "mission_loop": self.mission_loop,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MissionState":
        return cls(
            overlays=[MapOverlay.from_dict(item) for item in payload.get("overlays", [])],
            contacts=[MapContact.from_dict(item) for item in payload.get("contacts", [])],
            active_tool=str(payload.get("active_tool", "pan")),
            base_layer=str(payload.get("base_layer", "vector")),
            selected_overlay_id=payload.get("selected_overlay_id"),
            revision=int(payload.get("revision", 0)),
            mission_loop=bool(payload.get("mission_loop", False)),
        )
