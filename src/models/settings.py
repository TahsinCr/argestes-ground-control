from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SettingsModel:
    language: str = "tr"
    theme: str = "dark"
    map_layer: str = "vector"
    cache_enabled: bool = True
    cache_limit_mb: int = 2048
    cache_path: str = ""
    video_source_type: str = "udp"
    video_source_uri: str = ""
    mock_mode: bool = False
    default_radius_takeoff: int = 35
    default_radius_waypoint: int = 35
    default_radius_loiter: int = 75
    default_radius_land: int = 35
    default_radius_hss: int = 100
    map_center_min_zoom: int = 16
    vehicle_trail_limit: int = 80
    contact_trail_limit: int = 36

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SettingsModel":
        return cls(**payload)

    @classmethod
    def default(cls, base_dir: Path) -> "SettingsModel":
        cache_path = base_dir.joinpath("data", "map-cache")
        return cls(cache_path=str(cache_path))
