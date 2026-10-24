"""Battery estimation over normalized measurements, without a MAVLink dependency."""

import threading
from src.models.telemetry import TelemetrySnapshot
from .estimation import VoltageBatteryEstimator


class BatteryProcessor:
    def __init__(self, settings):
        self._lock = threading.RLock()
        self._settings = settings
        self._last_sample = None
        self._estimate = None
        self._estimator = VoltageBatteryEstimator(settings)

    def configure(self, settings):
        with self._lock:
            if settings != self._settings:
                self._settings = settings
                self._estimator = VoltageBatteryEstimator(settings)
                self._last_sample = None
                self._estimate = None

    def __call__(self, snapshot: TelemetrySnapshot) -> TelemetrySnapshot:
        with self._lock:
            if not snapshot.autopilot_link:
                self._estimator.reset()
                self._last_sample = None
                self._estimate = None
                return snapshot
            if self._settings.use_pixhawk_percent or snapshot.battery_voltage <= 0:
                return snapshot
            sample = snapshot.battery_received_at
            if sample is not None and sample != self._last_sample:
                self._estimate = self._estimator.update(snapshot.battery_voltage)
                self._last_sample = sample
            if self._estimate is None:
                return snapshot
            return snapshot.copy(
                battery_percent=self._estimate.percent,
                battery_voltage=self._estimate.filtered_voltage,
            )
