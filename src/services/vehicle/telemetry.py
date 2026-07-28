from __future__ import annotations

import math
import random
import threading
import time
from datetime import UTC, datetime
from typing import Any

from src.core.events import EventStream, Subscription
from src.models.geometry import Point
from src.models.telemetry import TelemetrySnapshot
from src.models.vehicle import VehicleConnectionState, VehicleTransportStats
from src.services.contracts.telemetry import TelemetryService
from src.services.contracts.vehicle import VehicleService
from src.services.logging_service import ServiceLogger


def clone_snapshot(snapshot: TelemetrySnapshot) -> TelemetrySnapshot:
    return snapshot.copy()


class MutableTelemetryService(TelemetryService):
    """Central telemetry service writable by any input adapter."""

    def __init__(self, logger: ServiceLogger | None = None) -> None:
        self._current = TelemetrySnapshot.zero()
        self._updated = EventStream[TelemetrySnapshot]()
        self._logger = logger
        self._lock = threading.RLock()

    @property
    def current(self) -> TelemetrySnapshot:
        with self._lock:
            return clone_snapshot(self._current)

    @property
    def updated(self) -> EventStream[TelemetrySnapshot]:
        return self._updated

    def start(self) -> None:
        self._updated.publish(self.current)
        if self._logger:
            self._logger.info("Telemetri servisi veri kaynağı bekliyor")

    def stop(self) -> None:
        return

    def push(self, snapshot: TelemetrySnapshot) -> None:
        value = clone_snapshot(snapshot)
        with self._lock:
            self._current = value
        self._updated.publish(clone_snapshot(value))

    def patch(self, **changes: Any) -> None:
        with self._lock:
            self._current = self._current.copy(**changes)
            value = clone_snapshot(self._current)
        self._updated.publish(value)


class TelemetryDecoder:
    """Convert flight-controller messages into the UI-independent telemetry model."""

    STANDARD_GRAVITY_MPS2 = 9.80665
    FIX_TYPES = {0: "NO GPS", 1: "NO FIX", 2: "2D FIX", 3: "3D FIX", 4: "DGPS", 5: "RTK FLOAT", 6: "RTK"}
    SYSTEM_STATES = {
        0: "UNINIT",
        1: "BOOT",
        2: "CALIBRATING",
        3: "STANDBY",
        4: "ACTIVE",
        5: "CRITICAL",
        6: "EMERGENCY",
        7: "POWEROFF",
        8: "TERMINATING",
    }

    def apply(self, snapshot: TelemetrySnapshot, message: Any) -> TelemetrySnapshot:
        message_type = str(message.get_type()).upper()
        received_at = datetime.now(UTC)
        updates: dict[str, Any] = {
            "timestamp": received_at,
            "telemetry_link": True,
            "autopilot_link": True,
            "packets_in": snapshot.packets_in + 1,
        }
        if message_type == "HEARTBEAT":
            base_mode = int(getattr(message, "base_mode", 0))
            system_status = int(getattr(message, "system_status", -1))
            updates.update(
                armed=bool(base_mode & 128),
                flight_mode=snapshot.flight_mode,
                system_status=self.SYSTEM_STATES.get(system_status, f"STATE {system_status}"),
            )
        elif message_type == "GLOBAL_POSITION_INT":
            latitude = float(message.lat) / 1e7
            longitude = float(message.lon) / 1e7
            position_valid = (
                math.isfinite(latitude)
                and math.isfinite(longitude)
                and -90.0 <= latitude <= 90.0
                and -180.0 <= longitude <= 180.0
                and not (abs(latitude) < 1e-9 and abs(longitude) < 1e-9)
            )
            vx = float(getattr(message, "vx", 0.0)) / 100.0
            vy = float(getattr(message, "vy", 0.0)) / 100.0
            updates.update(
                latitude=latitude,
                longitude=longitude,
                position_valid=position_valid,
                altitude_msl=float(message.alt) / 1000.0,
                altitude_agl=float(message.relative_alt) / 1000.0,
                ground_speed=math.hypot(vx, vy),
                climb_rate=-float(getattr(message, "vz", 0.0)) / 100.0,
                heading=float(getattr(message, "hdg", 0.0)) / 100.0,
                position_received_at=received_at,
                global_position_received_at=received_at,
            )
            home_metrics = self._home_metrics(
                latitude,
                longitude,
                snapshot.home_latitude,
                snapshot.home_longitude,
            )
            if home_metrics is not None:
                updates.update(home_distance=home_metrics[0], home_heading=home_metrics[1])
        elif message_type == "LOCAL_POSITION_NED":
            updates.update(
                local_north_m=self._required_finite_float(message.x, "local x"),
                local_east_m=self._required_finite_float(message.y, "local y"),
                local_down_m=self._required_finite_float(message.z, "local z"),
            )
        elif message_type == "ATTITUDE":
            updates.update(
                roll=math.degrees(float(message.roll)),
                pitch=math.degrees(float(message.pitch)),
                yaw=math.degrees(float(message.yaw)) % 360.0,
                attitude_received_at=received_at,
            )
        elif message_type == "VFR_HUD":
            updates.update(
                air_speed=float(message.airspeed),
                ground_speed=float(message.groundspeed),
                heading=float(message.heading) % 360.0,
                throttle_percent=float(message.throttle),
                altitude_msl=float(message.alt),
                climb_rate=float(message.climb),
                hud_received_at=received_at,
            )
        elif message_type == "SYS_STATUS":
            remaining = int(getattr(message, "battery_remaining", -1))
            voltage_mv = int(getattr(message, "voltage_battery", 0))
            current_ca = int(getattr(message, "current_battery", -1))
            updates["cpu_load"] = max(
                0,
                min(100, int(float(getattr(message, "load", 0.0)) / 10.0)),
            )
            if 0 <= remaining <= 100:
                updates["battery_percent"] = remaining
            if 0 < voltage_mv < 65_535:
                updates["battery_voltage"] = voltage_mv / 1000.0
            if current_ca >= 0:
                updates["battery_current_amps"] = current_ca / 100.0
        elif message_type == "BATTERY_STATUS":
            remaining = int(getattr(message, "battery_remaining", -1))
            current_ca = int(getattr(message, "current_battery", -1))
            consumed_mah = int(getattr(message, "current_consumed", -1))
            voltage = self._battery_status_voltage(getattr(message, "voltages", ()))
            if 0 <= remaining <= 100:
                updates["battery_percent"] = remaining
            if voltage is not None:
                updates["battery_voltage"] = voltage
            if current_ca >= 0:
                updates["battery_current_amps"] = current_ca / 100.0
            if consumed_mah >= 0:
                updates["battery_consumed_mah"] = consumed_mah
        elif message_type == "SCALED_PRESSURE":
            pressure = self._finite_float(getattr(message, "press_abs", None))
            differential = self._finite_float(getattr(message, "press_diff", None))
            temperature_cdeg = int(getattr(message, "temperature", -32_768))
            if pressure is not None and pressure > 0.0:
                updates["barometric_pressure_hpa"] = pressure
            if differential is not None:
                updates["differential_pressure_hpa"] = differential
            if temperature_cdeg != -32_768:
                updates["barometric_temperature_c"] = temperature_cdeg / 100.0
        elif message_type in {"SCALED_IMU", "SCALED_IMU2"}:
            updates.update(self._scaled_imu_acceleration(message))
        elif message_type == "HIGHRES_IMU":
            updates.update(self._highres_imu_updates(message))
        elif message_type == "GPS_RAW_INT":
            fix_type = int(getattr(message, "fix_type", 0))
            gps_time = self._unix_time(getattr(message, "time_usec", 0))
            updates.update(
                gps_fix=self.FIX_TYPES.get(fix_type, f"FIX {fix_type}"),
                gps_satellites=int(getattr(message, "satellites_visible", 0)),
                gps_hdop=self._scaled_gps_accuracy(getattr(message, "eph", None)),
            )
            if gps_time is not None:
                updates["uav_time"] = gps_time
                updates["uav_time_received_at"] = received_at
        elif message_type == "SYSTEM_TIME":
            vehicle_time = self._unix_time(getattr(message, "time_unix_usec", 0))
            if vehicle_time is not None:
                updates["uav_time"] = vehicle_time
                updates["uav_time_received_at"] = received_at
        elif message_type in {"RADIO", "RADIO_STATUS"}:
            quality = self._radio_link_quality(message)
            updates.update(
                link_quality=quality,
                link_quality_source="rf",
                link_quality_received_at=received_at,
                radio_local_rssi=self._optional_radio_value(message, "rssi"),
                radio_remote_rssi=self._optional_radio_value(message, "remrssi"),
                radio_local_noise=self._optional_radio_value(message, "noise"),
                radio_remote_noise=self._optional_radio_value(message, "remnoise"),
                radio_tx_buffer=self._optional_radio_value(message, "txbuf"),
            )
        elif message_type == "RC_CHANNELS":
            rssi = int(getattr(message, "rssi", 0))
            if rssi == 255:
                rssi = 0
            updates.update(rc_rssi=rssi)
        elif message_type == "EKF_STATUS_REPORT":
            flags = int(getattr(message, "flags", 0))
            updates.update(ekf_ok=bool(flags & 1) and not bool(flags & 128))
        elif message_type == "HOME_POSITION":
            home_latitude = float(getattr(message, "latitude", 0.0)) / 1e7
            home_longitude = float(getattr(message, "longitude", 0.0)) / 1e7
            Point(home_latitude, home_longitude)
            updates.update(
                home_latitude=home_latitude,
                home_longitude=home_longitude,
            )
            home_metrics = self._home_metrics(
                snapshot.latitude,
                snapshot.longitude,
                home_latitude,
                home_longitude,
            )
            if home_metrics is not None:
                updates.update(home_distance=home_metrics[0], home_heading=home_metrics[1])
        elif message_type == "NAV_CONTROLLER_OUTPUT":
            updates.update(
                wp_distance=float(getattr(message, "wp_dist", 0.0)),
                nav_bearing=float(getattr(message, "nav_bearing", 0.0)) % 360.0,
            )
        elif message_type == "WIND":
            updates.update(
                wind_heading=float(getattr(message, "direction", 0.0)) % 360.0,
                wind_speed=float(getattr(message, "speed", 0.0)),
            )
        elif message_type == "MISSION_CURRENT":
            sequence = max(0, int(getattr(message, "seq", 0)))
            updates["mission_index"] = sequence
            total = int(getattr(message, "total", 0) or 0)
            if total > 0:
                updates["mission_total"] = total
        elif message_type == "MISSION_COUNT":
            # ArduPilot reserves wire sequence zero for the home item.
            updates.update(mission_total=max(0, int(getattr(message, "count", 0)) - 1))
        return snapshot.copy(**updates)

    @staticmethod
    def _home_metrics(
        latitude: float,
        longitude: float,
        home_latitude: float | None,
        home_longitude: float | None,
    ) -> tuple[float, float] | None:
        if home_latitude is None or home_longitude is None:
            return None
        current = Point(float(latitude), float(longitude))
        if abs(current.latitude) < 1e-9 and abs(current.longitude) < 1e-9:
            return None
        home = Point(float(home_latitude), float(home_longitude))
        return current.distance_to(home), current.bearing_to(home)

    @staticmethod
    def _unix_time(value: Any) -> datetime | None:
        try:
            microseconds = int(value)
        except (TypeError, ValueError):
            return None
        # ArduPilot uses this field for boot time in some messages. Only UNIX
        # timestamps from 2000 onward are accepted as direct aircraft/GPS time.
        if microseconds < 946_684_800_000_000:
            return None
        try:
            return datetime.fromtimestamp(microseconds / 1_000_000.0, UTC)
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _scaled_gps_accuracy(value: Any) -> float | None:
        try:
            encoded = int(value)
        except (TypeError, ValueError):
            return None
        if encoded < 0 or encoded >= 65_535:
            return None
        return encoded / 100.0

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            converted = float(value)
        except (TypeError, ValueError):
            return None
        return converted if math.isfinite(converted) else None

    @classmethod
    def _required_finite_float(cls, value: Any, field_name: str) -> float:
        converted = cls._finite_float(value)
        if converted is None:
            raise ValueError(f"{field_name} geçerli bir sayı değil")
        return converted

    @classmethod
    def _scaled_imu_acceleration(cls, message: Any) -> dict[str, float]:
        scale = cls.STANDARD_GRAVITY_MPS2 / 1000.0
        values = {
            "acceleration_x_mps2": float(getattr(message, "xacc", 0.0)) * scale,
            "acceleration_y_mps2": float(getattr(message, "yacc", 0.0)) * scale,
            "acceleration_z_mps2": float(getattr(message, "zacc", 0.0)) * scale,
        }
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("SCALED_IMU geçersiz ivme değeri içeriyor")
        return values

    @staticmethod
    def _highres_imu_updates(message: Any) -> dict[str, float]:
        values = {
            "acceleration_x_mps2": float(getattr(message, "xacc", 0.0)),
            "acceleration_y_mps2": float(getattr(message, "yacc", 0.0)),
            "acceleration_z_mps2": float(getattr(message, "zacc", 0.0)),
        }
        pressure = TelemetryDecoder._finite_float(
            getattr(message, "abs_pressure", None)
        )
        differential = TelemetryDecoder._finite_float(
            getattr(message, "diff_pressure", None)
        )
        temperature = TelemetryDecoder._finite_float(
            getattr(message, "temperature", None)
        )
        if pressure is not None and pressure > 0.0:
            values["barometric_pressure_hpa"] = pressure
        if differential is not None:
            values["differential_pressure_hpa"] = differential
        if temperature is not None:
            values["barometric_temperature_c"] = temperature
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("HIGHRES_IMU geçersiz ivme değeri içeriyor")
        return values

    @staticmethod
    def _battery_status_voltage(values: Any) -> float | None:
        try:
            valid_cells = [
                encoded
                for value in values
                if 0 < (encoded := int(value)) < 65_535
            ]
        except (TypeError, ValueError):
            return None
        if not valid_cells:
            return None
        return sum(valid_cells) / 1000.0

    @classmethod
    def _radio_link_quality(cls, message: Any) -> int:
        """Convert SiK/RFD RADIO_STATUS fields into RF link health.

        RSSI and noise share the same modem scale. Local and remote SNR use the
        approximate 1.9 units/dB conversion for the SiK/RFD family; the weaker
        side and transmit buffer limit the result.
        """

        margins: list[float] = []
        for rssi_name, noise_name in (("rssi", "noise"), ("remrssi", "remnoise")):
            rssi = cls._optional_radio_value(message, rssi_name)
            noise = cls._optional_radio_value(message, noise_name)
            if rssi is None or noise is None or rssi <= 0 or noise <= 0:
                continue
            margin_db = (rssi - noise) / 1.9
            margins.append(max(0.0, min(100.0, (margin_db - 3.0) / 27.0 * 100.0)))

        if margins:
            quality = min(margins)
        else:
            raw_rssi = cls._optional_radio_value(message, "rssi") or 0
            quality = max(0.0, min(100.0, raw_rssi / 254.0 * 100.0))

        tx_buffer = cls._optional_radio_value(message, "txbuf")
        if tx_buffer is not None:
            quality = min(quality, max(0.0, min(100.0, float(tx_buffer))))
        return max(0, min(100, round(quality)))

    @staticmethod
    def _optional_radio_value(message: Any, name: str) -> int | None:
        if not hasattr(message, name):
            return None
        try:
            return int(getattr(message, name))
        except (TypeError, ValueError):
            return None

TELEMETRY_MESSAGE_TYPES = (
    "HEARTBEAT",
    "GLOBAL_POSITION_INT",
    "LOCAL_POSITION_NED",
    "ATTITUDE",
    "VFR_HUD",
    "SYS_STATUS",
    "BATTERY_STATUS",
    "SCALED_PRESSURE",
    "SCALED_IMU",
    "SCALED_IMU2",
    "HIGHRES_IMU",
    "GPS_RAW_INT",
    "SYSTEM_TIME",
    "RADIO",
    "RADIO_STATUS",
    "RC_CHANNELS",
    "EKF_STATUS_REPORT",
    "HOME_POSITION",
    "NAV_CONTROLLER_OUTPUT",
    "WIND",
    "MISSION_CURRENT",
    "MISSION_COUNT",
)


class VehicleTelemetryService(MutableTelemetryService):
    def __init__(
        self,
        vehicle: VehicleService,
        logger: ServiceLogger | None = None,
        decoder: TelemetryDecoder | None = None,
    ) -> None:
        super().__init__(logger)
        self._vehicle = vehicle
        self._decoder = decoder or TelemetryDecoder()
        self._subscriptions: list[Subscription] = []
        self._rf_link_logged = False
        self._packet_link_logged = False

    def start(self) -> None:
        if self._subscriptions:
            return
        self._subscriptions.extend(
            [
                self._vehicle.subscribe(TELEMETRY_MESSAGE_TYPES, self._consume_message),
                self._vehicle.connection_changed.subscribe(self._on_connection_changed),
            ]
        )
        self._updated.publish(self.current)

    def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()

    def _consume_message(self, message: Any) -> None:
        try:
            message_type = str(message.get_type()).upper()
            snapshot = self._decoder.apply(self.current, message)
            if message_type == "HEARTBEAT":
                snapshot = snapshot.copy(
                    flight_mode=self._vehicle.mode_name(message, snapshot.flight_mode)
                )
            transport = getattr(self._vehicle, "transport_stats", VehicleTransportStats())
            snapshot = snapshot.copy(
                packets_in=transport.received_packets,
                packets_out=transport.sent_packets,
            )
            rf_is_fresh = bool(
                snapshot.link_quality_source == "rf"
                and snapshot.has_fresh_link_quality(3.0, now=snapshot.timestamp)
            )
            if not rf_is_fresh and transport.delivery_quality is not None:
                snapshot = snapshot.copy(
                    link_quality=transport.delivery_quality,
                    link_quality_source="mavlink_packets",
                    link_quality_received_at=snapshot.timestamp,
                )
                if not self._packet_link_logged and self._logger:
                    self._packet_link_logged = True
                    self._logger.event(
                        "telemetry.packet_health_detected",
                        f"MAVLink paket sağlık ölçümü başladı: %{snapshot.link_quality}",
                        severity="success",
                        details={
                            "endpoint": self._vehicle.state.endpoint,
                            "received_packets": transport.received_packets,
                            "sent_packets": transport.sent_packets,
                            "estimated_dropped_packets": transport.estimated_dropped_packets,
                        },
                        device_id=self._vehicle.state.device_id,
                    )
            self.push(snapshot)
            if message_type in {"RADIO", "RADIO_STATUS"} and not self._rf_link_logged:
                self._rf_link_logged = True
                if self._logger:
                    self._logger.event(
                        "telemetry.rf_link_detected",
                        f"RF telemetri hat sağlığı alınmaya başladı: %{snapshot.link_quality}",
                        severity="success",
                        details={
                            "endpoint": self._vehicle.state.endpoint,
                            "source_system": self._message_source(message, "get_srcSystem"),
                            "source_component": self._message_source(message, "get_srcComponent"),
                        },
                        device_id=self._vehicle.state.device_id,
                    )
        except (AttributeError, TypeError, ValueError) as exc:
            if self._logger:
                self._logger.event(
                    "telemetry.decode_failed",
                    f"Uçuş cihazı mesajı çözümlenemedi: {exc}",
                    severity="warning",
                    details={"message_type": str(getattr(message, "get_type", lambda: "UNKNOWN")())},
                    device_id=self._vehicle.state.device_id if self._vehicle.connected else None,
                )

    def _on_connection_changed(self, state: VehicleConnectionState) -> None:
        if not state.connected:
            self._rf_link_logged = False
            self._packet_link_logged = False
            self.push(
                self.current.copy(
                    telemetry_link=False,
                    autopilot_link=False,
                    position_valid=False,
                    position_received_at=None,
                    uav_time_received_at=None,
                    attitude_received_at=None,
                    global_position_received_at=None,
                    hud_received_at=None,
                    link_quality=0,
                    link_quality_source="",
                    link_quality_received_at=None,
                    radio_local_rssi=None,
                    radio_remote_rssi=None,
                    radio_local_noise=None,
                    radio_remote_noise=None,
                    radio_tx_buffer=None,
                    battery_percent=0,
                    battery_voltage=0.0,
                    battery_current_amps=None,
                    battery_consumed_mah=None,
                    barometric_pressure_hpa=None,
                    barometric_temperature_c=None,
                    differential_pressure_hpa=None,
                    acceleration_x_mps2=None,
                    acceleration_y_mps2=None,
                    acceleration_z_mps2=None,
                    local_north_m=None,
                    local_east_m=None,
                    local_down_m=None,
                )
            )

    @staticmethod
    def _message_source(message: Any, method_name: str) -> int | None:
        method = getattr(message, method_name, None)
        if not callable(method):
            return None
        try:
            return int(method())
        except (TypeError, ValueError):
            return None


class MockTelemetryService(MutableTelemetryService):
    def __init__(self, logger: ServiceLogger | None = None, *, interval: float = 0.12) -> None:
        super().__init__(logger)
        self._interval = interval
        self._phase = 0.0
        self._packet_count = 16000
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="MockTelemetry", daemon=True)
        self._thread.start()
        if self._logger:
            self._logger.success("Mock telemetri akışı başlatıldı")

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def tick(self) -> TelemetrySnapshot:
        self._phase += 0.07
        phase = self._phase
        self._packet_count += random.randint(18, 31)
        current = self.current
        vehicle_now = datetime.now(UTC)
        snapshot = TelemetrySnapshot(
            team_id=17,
            latitude=39.8680 + math.sin(phase * 0.28) * 0.0031,
            longitude=32.7485 + math.cos(phase * 0.31) * 0.0044,
            altitude_agl=137.0 + math.sin(phase) * 18.0,
            altitude_msl=1082.0 + math.sin(phase * 0.44) * 26.0,
            ground_speed=27.0 + math.sin(phase * 0.7) * 4.0,
            air_speed=29.5 + math.cos(phase * 0.66) * 3.2,
            roll=math.sin(phase * 0.9) * 28.0,
            pitch=math.cos(phase * 0.62) * 11.0,
            yaw=(phase * 17.0) % 360.0,
            heading=(phase * 16.0 + 25.0) % 360.0,
            climb_rate=math.sin(phase * 0.52) * 3.0,
            battery_percent=max(26, 94 - int(phase * 0.18)),
            battery_voltage=24.7 - phase * 0.006,
            link_quality=89 + int(math.sin(phase * 0.22) * 7),
            link_quality_source="mock",
            gps_satellites=18 + int((math.sin(phase * 0.34) + 1.0) * 2),
            gps_fix="RTK" if int(phase) % 11 else "3D FIX",
            gps_hdop=0.7 + abs(math.sin(phase * 0.2)) * 0.25,
            flight_mode=["AUTO", "GUIDED", "LOITER", "AUTO"][int(phase // 12) % 4],
            mission_index=4 + int(phase // 10) % 3,
            mission_total=9,
            armed=True,
            telemetry_link=True,
            video_link=True,
            autopilot_link=True,
            server_link=current.server_link,
            target_distance=420.0 + math.cos(phase * 0.45) * 80.0,
            wind_speed=9.0 + math.sin(phase * 0.38) * 2.2,
            wind_heading=(phase * 9.0 + 240.0) % 360.0,
            cpu_load=42 + int(math.sin(phase * 0.29) * 8),
            packets_in=self._packet_count,
            packets_out=self._packet_count - 1040,
            bandwidth_kbps=96.0 + math.sin(phase * 0.57) * 22.0,
            position_valid=True,
            throttle_percent=58.0 + math.sin(phase * 0.42) * 8.0,
            home_distance=180.0 + math.cos(phase * 0.35) * 25.0,
            home_heading=(phase * 11.0 + 210.0) % 360.0,
            wp_distance=95.0 + math.cos(phase * 0.61) * 20.0,
            nav_bearing=(phase * 13.0 + 35.0) % 360.0,
            rc_rssi=86 + int(math.sin(phase * 0.26) * 6),
            ekf_ok=True,
            system_status="ACTIVE",
            warnings=["VIDEO JITTER"] if phase % 27.0 > 24.0 else [],
            timestamp=vehicle_now,
            server_time=current.server_time,
            uav_time=vehicle_now,
            target_track=current.target_track,
            position_received_at=vehicle_now,
            uav_time_received_at=vehicle_now,
            link_quality_received_at=vehicle_now,
            attitude_received_at=vehicle_now,
            global_position_received_at=vehicle_now,
            hud_received_at=vehicle_now,
            battery_current_amps=8.4 + math.sin(phase * 0.33) * 1.2,
            battery_consumed_mah=max(0, int(phase * 18.0)),
            barometric_pressure_hpa=898.0 + math.sin(phase * 0.18) * 1.4,
            barometric_temperature_c=24.0 + math.sin(phase * 0.15) * 2.0,
            differential_pressure_hpa=5.2 + math.sin(phase * 0.66) * 0.8,
            acceleration_x_mps2=math.sin(phase * 0.71) * 1.8,
            acceleration_y_mps2=math.cos(phase * 0.63) * 1.4,
            acceleration_z_mps2=9.80665 + math.sin(phase * 0.52) * 0.9,
            local_north_m=phase * 3.4,
            local_east_m=math.sin(phase * 0.22) * 85.0,
            local_down_m=-(137.0 + math.sin(phase) * 18.0),
        )
        self.push(snapshot)
        return snapshot

    def _run(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            self.tick()
            self._stop_event.wait(max(0.0, self._interval - (time.monotonic() - started)))
