from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class TelemetrySnapshot:
    team_id: int
    latitude: float
    longitude: float
    altitude_agl: float
    altitude_msl: float
    ground_speed: float
    air_speed: float
    roll: float
    pitch: float
    yaw: float
    heading: float
    climb_rate: float
    battery_percent: int
    battery_voltage: float
    link_quality: int
    gps_satellites: int
    gps_fix: str
    flight_mode: str
    mission_index: int
    mission_total: int
    armed: bool
    telemetry_link: bool
    video_link: bool
    autopilot_link: bool
    server_link: bool
    target_distance: float
    wind_speed: float
    wind_heading: float
    cpu_load: int
    packets_in: int
    packets_out: int
    bandwidth_kbps: float
    throttle_percent: float = 0.0
    home_distance: float = 0.0
    home_heading: float = 0.0
    wp_distance: float = 0.0
    nav_bearing: float = 0.0
    rc_rssi: int = 0
    ekf_ok: bool = False
    system_status: str = "DISCONNECTED"
    warnings: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    server_time: datetime | str | None = None
    uav_time: datetime | str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        if isinstance(self.server_time, datetime):
            payload["server_time"] = self.server_time.isoformat()
        if isinstance(self.uav_time, datetime):
            payload["uav_time"] = self.uav_time.isoformat()
        return payload

    @classmethod
    def zero(cls) -> "TelemetrySnapshot":
        return cls(
            team_id=0,
            latitude=0.0,
            longitude=0.0,
            altitude_agl=0.0,
            altitude_msl=0.0,
            ground_speed=0.0,
            air_speed=0.0,
            roll=0.0,
            pitch=0.0,
            yaw=0.0,
            heading=0.0,
            climb_rate=0.0,
            battery_percent=0,
            battery_voltage=0.0,
            link_quality=0,
            gps_satellites=0,
            gps_fix="NO FIX",
            flight_mode="DISCONNECTED",
            mission_index=0,
            mission_total=0,
            armed=False,
            telemetry_link=False,
            video_link=False,
            autopilot_link=False,
            server_link=False,
            target_distance=0.0,
            wind_speed=0.0,
            wind_heading=0.0,
            cpu_load=0,
            packets_in=0,
            packets_out=0,
            bandwidth_kbps=0.0,
            throttle_percent=0.0,
            home_distance=0.0,
            home_heading=0.0,
            wp_distance=0.0,
            nav_bearing=0.0,
            rc_rssi=0,
            ekf_ok=False,
            system_status="DISCONNECTED",
            warnings=[],
        )
