from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass

from src.models.settings import BatteryTelemetrySettings


@dataclass(frozen=True, slots=True)
class BatteryVoltageEstimate:
    """A display-only battery estimate derived from filtered pack voltage."""

    raw_voltage: float
    filtered_voltage: float
    percent: int


class VoltageBatteryEstimator:
    """Robust voltage-only state-of-charge estimator for a configured LiPo pack.

    A rolling median rejects short spikes. The following asymmetric low-pass
    filter deliberately follows recovery faster than voltage sag: motor-load
    dips therefore do not immediately look like lost capacity, while the
    display recovers promptly after the load is removed. Flight safety remains
    the responsibility of the flight controller's raw-voltage failsafes.
    """

    def __init__(self, settings: BatteryTelemetrySettings) -> None:
        self.settings = settings
        self._samples: deque[float] = deque(maxlen=settings.median_window)
        self._filtered_voltage: float | None = None
        self._last_sample_at: float | None = None

    @property
    def empty_voltage(self) -> float:
        return self.settings.empty_voltage

    def reset(self) -> None:
        self._samples.clear()
        self._filtered_voltage = None
        self._last_sample_at = None

    def update(
        self,
        voltage: float,
        *,
        sample_at: float | None = None,
    ) -> BatteryVoltageEstimate | None:
        raw_voltage = float(voltage)
        if not self._valid_pack_voltage(raw_voltage):
            return None

        now = time.monotonic() if sample_at is None else float(sample_at)
        if not math.isfinite(now):
            return None
        self._samples.append(raw_voltage)
        # median_high keeps a lone downward spike from dragging the estimator
        # during startup, before the configured odd-sized window is full.
        candidate = float(statistics.median_high(self._samples))

        if self._filtered_voltage is None or self._last_sample_at is None:
            filtered = candidate
        else:
            elapsed = max(0.0, min(5.0, now - self._last_sample_at))
            time_constant = (
                self.settings.recovery_filter_seconds
                if candidate >= self._filtered_voltage
                else self.settings.discharge_filter_seconds
            )
            alpha = 1.0 - math.exp(-elapsed / time_constant)
            filtered = self._filtered_voltage + alpha * (
                candidate - self._filtered_voltage
            )

        self._filtered_voltage = filtered
        self._last_sample_at = now
        return BatteryVoltageEstimate(
            raw_voltage=raw_voltage,
            filtered_voltage=filtered,
            percent=self.percent_for_voltage(filtered),
        )

    def percent_for_voltage(self, voltage: float) -> int:
        span = self.settings.full_voltage - self.empty_voltage
        normalized = (float(voltage) - self.empty_voltage) / span
        return max(0, min(100, round(normalized * 100.0)))

    def _valid_pack_voltage(self, voltage: float) -> bool:
        if not math.isfinite(voltage):
            return False
        # Reject MAVLink sentinels, disconnected sensors and a battery with a
        # different series count instead of poisoning the filter state.
        minimum = self.settings.cell_count * 2.5
        maximum = self.settings.cell_count * 4.5
        return minimum <= voltage <= maximum


__all__ = ["BatteryVoltageEstimate", "VoltageBatteryEstimator"]
