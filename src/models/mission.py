from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(slots=True)
class MapOverlay:
    overlay_id: str
    overlay_type: str
    label: str
    points: list[list[float]]
    color: str
    radius_m: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MapContact:
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MissionState:
    overlays: list[MapOverlay] = field(default_factory=list)
    contacts: list[MapContact] = field(default_factory=list)
    active_tool: str = "pan"
    base_layer: str = "vector"
    selected_overlay_id: str | None = None

    def copy(self, **changes: Any) -> "MissionState":
        return replace(self, **changes)
