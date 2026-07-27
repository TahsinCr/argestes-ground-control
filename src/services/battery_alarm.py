from __future__ import annotations

from collections.abc import Callable

from src.models.settings import BatteryAlarmSettings
from src.models.telemetry import TelemetrySnapshot
from src.services.logging_service import ServiceLogger


class BatteryAlarmService:
    """Evaluate battery thresholds and limit repeated alarm log entries."""

    FAILSAFE_MODE = "RTL"

    def __init__(
        self,
        logger: ServiceLogger | None = None,
        failsafe_handler: Callable[[str], object] | None = None,
    ) -> None:
        self._logger = logger
        self._failsafe_handler = failsafe_handler
        self._active = False
        self._warning_active = False
        self._failsafe_dispatched = False
        self._last_logged_percent: int | None = None
        self._settings_signature: tuple[str, float, bool, str, float] | None = None

    @property
    def active(self) -> bool:
        return self._active

    def evaluate(
        self,
        snapshot: TelemetrySnapshot,
        settings: BatteryAlarmSettings,
    ) -> bool:
        signature = (
            settings.warning_mode,
            settings.warning_threshold,
            settings.failsafe_enabled,
            settings.failsafe_mode,
            settings.failsafe_threshold,
        )
        if signature != self._settings_signature:
            self._active = False
            self._warning_active = False
            self._failsafe_dispatched = False
            self._last_logged_percent = None
            self._settings_signature = signature

        warning_value = self._trigger_value(snapshot, settings.warning_mode)
        failsafe_value = self._trigger_value(snapshot, settings.failsafe_mode)
        telemetry_available = (
            snapshot.telemetry_link
            and snapshot.autopilot_link
            and snapshot.battery_available
        )
        warning_active = bool(
            telemetry_available
            and warning_value is not None
            and warning_value < settings.warning_threshold
        )
        failsafe_active = bool(
            settings.failsafe_enabled
            and telemetry_available
            and failsafe_value is not None
            and failsafe_value < settings.failsafe_threshold
        )

        if not warning_active:
            self._warning_active = False
            self._last_logged_percent = None
        else:
            percent = max(0, min(100, int(snapshot.battery_percent)))
            should_log = (
                not self._warning_active
                or self._last_logged_percent is None
                or abs(percent - self._last_logged_percent) >= 1
            )
            self._warning_active = True
            if should_log:
                self._last_logged_percent = percent
                self._emit_alarm(snapshot, settings, warning_value)

        if not failsafe_active:
            self._failsafe_dispatched = False
        else:
            self._dispatch_failsafe_if_required(
                snapshot,
                settings,
                failsafe_value,
            )
        self._active = warning_active or failsafe_active
        return self._active

    def _dispatch_failsafe_if_required(
        self,
        snapshot: TelemetrySnapshot,
        settings: BatteryAlarmSettings,
        trigger_value: float,
    ) -> None:
        if (
            not snapshot.armed
            or self._failsafe_dispatched
        ):
            return
        self._failsafe_dispatched = True
        if snapshot.flight_mode.strip().upper() == self.FAILSAFE_MODE:
            return
        if self._logger:
            self._logger.event(
                "battery.failsafe_requested",
                "Düşük batarya nedeniyle RTL failsafe modu istendi",
                severity="danger",
                details={
                    "mode": self.FAILSAFE_MODE,
                    "threshold_mode": settings.failsafe_mode,
                    "threshold": settings.failsafe_threshold,
                    "trigger_value": trigger_value,
                    "unit": "%"
                    if settings.failsafe_mode == "percent"
                    else "V",
                },
            )
        if self._failsafe_handler is None:
            return
        try:
            self._failsafe_handler(self.FAILSAFE_MODE)
        except Exception as exc:
            if self._logger:
                self._logger.event(
                    "battery.failsafe_failed",
                    f"Batarya failsafe komutu gönderilemedi: {exc}",
                    severity="danger",
                    details={
                        "mode": self.FAILSAFE_MODE,
                        "error_type": exc.__class__.__name__,
                    },
                )

    @staticmethod
    def _trigger_value(
        snapshot: TelemetrySnapshot,
        mode: str,
    ) -> float | None:
        if mode == "voltage":
            voltage = float(snapshot.battery_voltage)
            return voltage if voltage > 0.0 else None
        return float(max(0, min(100, int(snapshot.battery_percent))))

    def _emit_alarm(
        self,
        snapshot: TelemetrySnapshot,
        settings: BatteryAlarmSettings,
        trigger_value: float,
    ) -> None:
        if self._logger is None:
            return
        percent = max(0, min(100, int(snapshot.battery_percent)))
        voltage = max(0.0, float(snapshot.battery_voltage))
        unit = "%" if settings.warning_mode == "percent" else "V"
        self._logger.event(
            "battery.low",
            (
                f"Düşük batarya: %{percent} · {voltage:.1f} V "
                f"(uyarı eşiği: {settings.warning_threshold:g} {unit})"
            ),
            severity="danger",
            details={
                "mode": settings.warning_mode,
                "threshold": settings.warning_threshold,
                "unit": unit,
                "trigger_value": trigger_value,
                "percent": percent,
                "voltage": voltage,
            },
        )
