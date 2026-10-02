from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from src.core.abstracts import Model


@dataclass(slots=True)
class TelemetrySnapshot(Model):
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
    link_quality_source: str
    gps_satellites: int | None
    gps_fix: str
    gps_hdop: float | None
    flight_mode: str
    mission_index: int
    mission_total: int
    armed: bool
    telemetry_link: bool
    autopilot_link: bool
    wind_speed: float
    wind_heading: float
    cpu_load: int
    packets_in: int
    packets_out: int
    bandwidth_kbps: float
    position_valid: bool = False
    throttle_percent: float = 0.0
    home_distance: float = 0.0
    home_heading: float = 0.0
    wp_distance: float = 0.0
    nav_bearing: float = 0.0
    rc_rssi: int = 0
    ekf_ok: bool = False
    system_status: str = "DISCONNECTED"
    warnings: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    uav_time: datetime | str | None = None
    position_received_at: datetime | None = None
    uav_time_received_at: datetime | None = None
    link_quality_received_at: datetime | None = None
    attitude_received_at: datetime | None = None
    global_position_received_at: datetime | None = None
    hud_received_at: datetime | None = None
    gps_received_at: datetime | None = None
    wind_received_at: datetime | None = None
    home_latitude: float | None = None
    home_longitude: float | None = None
    radio_local_rssi: int | None = None
    radio_remote_rssi: int | None = None
    radio_local_noise: int | None = None
    radio_remote_noise: int | None = None
    radio_tx_buffer: int | None = None
    radio_rx_errors: int | None = None
    radio_fixed_packets: int | None = None
    packet_delivery_percent: int | None = None
    estimated_dropped_packets: int = 0
    battery_current_amps: float | None = None
    battery_consumed_mah: int | None = None
    battery_received_at: datetime | None = None
    barometric_pressure_hpa: float | None = None
    barometric_temperature_c: float | None = None
    differential_pressure_hpa: float | None = None
    acceleration_x_mps2: float | None = None
    acceleration_y_mps2: float | None = None
    acceleration_z_mps2: float | None = None
    local_north_m: float | None = None
    local_east_m: float | None = None
    local_down_m: float | None = None
    telemetry_round_trip_ms: float | None = None

    @property
    def battery_available(self) -> bool:
        return bool(
            self.battery_percent > 0
            or self.battery_voltage > 0.0
            or self.battery_current_amps is not None
        )

    @property
    def acceleration_magnitude_mps2(self) -> float | None:
        axes = (
            self.acceleration_x_mps2,
            self.acceleration_y_mps2,
            self.acceleration_z_mps2,
        )
        if any(value is None or not math.isfinite(value) for value in axes):
            return None
        x = float(axes[0])
        y = float(axes[1])
        z = float(axes[2])
        return math.sqrt(x * x + y * y + z * z)

    @property
    def load_factor_g(self) -> float | None:
        magnitude = self.acceleration_magnitude_mps2
        if magnitude is None:
            return None
        return magnitude / 9.80665

    @property
    def has_valid_position(self) -> bool:
        return bool(
            self.position_valid
            and math.isfinite(self.latitude)
            and math.isfinite(self.longitude)
            and -90.0 <= self.latitude <= 90.0
            and -180.0 <= self.longitude <= 180.0
            and not (abs(self.latitude) < 1e-9 and abs(self.longitude) < 1e-9)
        )

    @property
    def has_local_position(self) -> bool:
        values = (self.local_north_m, self.local_east_m, self.local_down_m)
        return all(value is not None and math.isfinite(value) for value in values)

    def has_fresh_vehicle_telemetry(
        self,
        max_age_s: float,
        *,
        now: datetime | None = None,
    ) -> bool:
        if max_age_s <= 0:
            raise ValueError("Telemetri tazelik süresi pozitif olmalı")
        if (
            not self.telemetry_link
            or not self.autopilot_link
            or not self.has_valid_position
        ):
            return False
        if not isinstance(self.uav_time, datetime) or self.uav_time.tzinfo is None:
            return False
        received = (self.position_received_at, self.uav_time_received_at)
        if any(value is None or value.tzinfo is None for value in received):
            return False
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return all(
            0.0 <= (current - value.astimezone(UTC)).total_seconds() <= max_age_s
            for value in received
            if value is not None
        )

    def has_fresh_link_quality(
        self,
        max_age_s: float,
        *,
        now: datetime | None = None,
    ) -> bool:
        if max_age_s <= 0:
            raise ValueError("Bağlantı kalitesi tazelik süresi pozitif olmalı")
        received_at = self.link_quality_received_at
        if (
            not self.telemetry_link
            or not self.autopilot_link
            or not self.link_quality_source
            or received_at is None
            or received_at.tzinfo is None
        ):
            return False
        current = (now or datetime.now(UTC)).astimezone(UTC)
        age_s = (current - received_at.astimezone(UTC)).total_seconds()
        return 0.0 <= age_s <= max_age_s

    def has_fresh_gps(
        self,
        max_age_s: float,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Return whether a recent GPS receiver sample reports a usable fix."""

        if max_age_s <= 0:
            raise ValueError("GPS tazelik süresi pozitif olmalı")
        received_at = self.gps_received_at
        if (
            not self.telemetry_link
            or not self.autopilot_link
            or received_at is None
            or received_at.tzinfo is None
            or self.gps_fix in {"NO GPS", "NO FIX"}
            or self.gps_satellites is None
            or self.gps_satellites <= 0
        ):
            return False
        current = (now or datetime.now(UTC)).astimezone(UTC)
        age_s = (current - received_at.astimezone(UTC)).total_seconds()
        return 0.0 <= age_s <= max_age_s

    def copy(self, **changes: Any) -> "TelemetrySnapshot":
        changes.setdefault("warnings", list(self.warnings))
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        if isinstance(self.uav_time, datetime):
            payload["uav_time"] = self.uav_time.isoformat()
        if self.position_received_at is not None:
            payload["position_received_at"] = self.position_received_at.isoformat()
        if self.uav_time_received_at is not None:
            payload["uav_time_received_at"] = self.uav_time_received_at.isoformat()
        if self.link_quality_received_at is not None:
            payload["link_quality_received_at"] = (
                self.link_quality_received_at.isoformat()
            )
        if self.attitude_received_at is not None:
            payload["attitude_received_at"] = self.attitude_received_at.isoformat()
        if self.global_position_received_at is not None:
            payload["global_position_received_at"] = (
                self.global_position_received_at.isoformat()
            )
        if self.hud_received_at is not None:
            payload["hud_received_at"] = self.hud_received_at.isoformat()
        if self.gps_received_at is not None:
            payload["gps_received_at"] = self.gps_received_at.isoformat()
        if self.wind_received_at is not None:
            payload["wind_received_at"] = self.wind_received_at.isoformat()
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
            link_quality_source="",
            gps_satellites=None,
            gps_fix="NO FIX",
            gps_hdop=None,
            flight_mode="DISCONNECTED",
            mission_index=0,
            mission_total=0,
            armed=False,
            telemetry_link=False,
            autopilot_link=False,
            wind_speed=0.0,
            wind_heading=0.0,
            cpu_load=0,
            packets_in=0,
            packets_out=0,
            bandwidth_kbps=0.0,
            position_valid=False,
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
