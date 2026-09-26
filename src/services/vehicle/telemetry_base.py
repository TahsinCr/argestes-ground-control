"""Protocol-neutral telemetry state and the local simulator."""

from __future__ import annotations

import math
import random
import threading
import time
from datetime import UTC, datetime
from typing import Any

from src.core.events import EventBus
from src.models.telemetry import TelemetrySnapshot
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import ServiceLogger


def clone_snapshot(snapshot: TelemetrySnapshot) -> TelemetrySnapshot:
    return snapshot.copy()


class MutableTelemetryService(TelemetryService):
    """Central telemetry service writable by any protocol adapter."""

    def __init__(self, logger: ServiceLogger | None = None) -> None:
        self._current = TelemetrySnapshot.zero()
        self._updated = EventBus[TelemetrySnapshot]()
        self._logger = logger
        self._lock = threading.RLock()

    @property
    def current(self) -> TelemetrySnapshot:
        with self._lock:
            return clone_snapshot(self._current)

    @property
    def updated(self) -> EventBus[TelemetrySnapshot]:
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

    def _current_for_decoder(self) -> TelemetrySnapshot:
        """Borrow the read-only snapshot for a decoder pass."""
        with self._lock:
            return self._current

    def patch(self, **changes: Any) -> None:
        with self._lock:
            self._current = self._current.copy(**changes)
            value = clone_snapshot(self._current)
        self._updated.publish(value)


class SwitchableTelemetryService(MutableTelemetryService):
    """Stable telemetry identity for consumers while an optional decoder changes."""

    def __init__(self) -> None:
        super().__init__()
        self._delegate: TelemetryService | None = None
        self._delegate_subscription = None
        self._started = False
        self._switch_lock = threading.RLock()

    def attach(self, delegate: TelemetryService | None) -> None:
        with self._switch_lock:
            if self._delegate is delegate:
                return
            previous, subscription = self._delegate, self._delegate_subscription
            self._delegate = None
            self._delegate_subscription = None
            if subscription is not None:
                subscription.cancel()
            if previous is not None and self._started:
                previous.stop()
            if delegate is not None:
                self._delegate_subscription = delegate.updated.subscribe(self.push)
                self._delegate = delegate
                if self._started:
                    try:
                        delegate.start()
                    except Exception:
                        self._delegate_subscription.cancel()
                        self._delegate_subscription = None
                        self._delegate = None
                        self.push(TelemetrySnapshot.zero())
                        raise
                self.push(delegate.current)
            else:
                self.push(TelemetrySnapshot.zero())

    def start(self) -> None:
        with self._switch_lock:
            if self._started:
                return
            self._started = True
            if self._delegate is not None:
                try:
                    self._delegate.start()
                except Exception:
                    self._started = False
                    raise
            else:
                super().start()

    def stop(self) -> None:
        with self._switch_lock:
            if not self._started:
                return
            self._started = False
            if self._delegate is not None:
                self._delegate.stop()


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
            gps_received_at=vehicle_now,
            flight_mode=["AUTO", "GUIDED", "LOITER", "AUTO"][int(phase // 12) % 4],
            mission_index=4 + int(phase // 10) % 3,
            mission_total=9,
            armed=True,
            telemetry_link=True,
            autopilot_link=True,
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
            warnings=[],
            timestamp=vehicle_now,
            uav_time=vehicle_now,
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
