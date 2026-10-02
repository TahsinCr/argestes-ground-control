from __future__ import annotations

import math
from dataclasses import dataclass

from src.core.abstracts import Model


EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True, slots=True)
class Point(Model):
    latitude: float
    longitude: float
    altitude: float = 0.0

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError("Enlem -90..90 aralığında olmalı")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError("Boylam -180..180 aralığında olmalı")

    def distance_to(self, other: "Point", *, include_altitude: bool = False) -> float:
        lat1, lat2 = math.radians(self.latitude), math.radians(other.latitude)
        delta_lat = lat2 - lat1
        delta_lon = math.radians(other.longitude - self.longitude)
        haversine = (
            math.sin(delta_lat / 2.0) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
        )
        ground = 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(haversine)))
        if not include_altitude:
            return ground
        return math.hypot(ground, other.altitude - self.altitude)

    def bearing_to(self, other: "Point") -> float:
        lat1, lat2 = math.radians(self.latitude), math.radians(other.latitude)
        delta_lon = math.radians(other.longitude - self.longitude)
        y = math.sin(delta_lon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(
            lat2
        ) * math.cos(delta_lon)
        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    def to_list(self) -> list[float]:
        return [self.latitude, self.longitude, self.altitude]

    def to_dict(self) -> dict[str, float]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
        }
