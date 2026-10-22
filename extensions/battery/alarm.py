from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
import threading
import time

from src.models.settings import BatteryAlarmSettings
from src.models.telemetry import TelemetrySnapshot
from src.services.logging_service import ServiceLogger


class BatteryAlarmService:
    """Evaluate battery thresholds and limit repeated alarm log entries."""

    FAILSAFE_MODE = "RTL"
    MAX_FAILSAFE_ATTEMPTS = 2
    FAILSAFE_RETRY_DELAY_S = 5.0

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
        self._failsafe_pending = False
        self._failsafe_attempts = 0
        self._failsafe_retry_after = 0.0
        self._failsafe_unavailable_logged = False
        self._failsafe_generation = 0
        self._lock = threading.RLock()
        self._last_logged_percent: int | None = None
        self._settings_signature: tuple[str, float, bool, str, float] | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def evaluate(
        self,
        snapshot: TelemetrySnapshot,
        settings: BatteryAlarmSettings,
    ) -> bool:
        with self._lock:
            return self._evaluate(snapshot, settings)

    def _evaluate(
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
            self._failsafe_pending = False
            self._failsafe_attempts = 0
            self._failsafe_retry_after = 0.0
            self._failsafe_unavailable_logged = False
            self._failsafe_generation += 1
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
            if self._failsafe_pending:
                self._failsafe_generation += 1
            self._failsafe_dispatched = False
            self._failsafe_pending = False
            self._failsafe_attempts = 0
            self._failsafe_retry_after = 0.0
            self._failsafe_unavailable_logged = False
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
            or self._failsafe_pending
            or self._failsafe_attempts >= self.MAX_FAILSAFE_ATTEMPTS
            or time.monotonic() < self._failsafe_retry_after
        ):
            return
        if snapshot.flight_mode.strip().upper() == self.FAILSAFE_MODE:
            self._failsafe_dispatched = True
            return
        if self._failsafe_handler is None:
            self._failsafe_dispatched = True
            if not self._failsafe_unavailable_logged and self._logger:
                self._failsafe_unavailable_logged = True
                self._logger.event(
                    "battery.failsafe_unavailable",
                    "RTL failsafe yapılandırılmış ancak komut işleyicisi kullanılamıyor",
                    severity="danger",
                    details={"mode": self.FAILSAFE_MODE},
                )
            return
        self._failsafe_pending = True
        self._failsafe_attempts += 1
        generation = self._failsafe_generation
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
                    "unit": "%" if settings.failsafe_mode == "percent" else "V",
                },
            )
        try:
            result = self._failsafe_handler(self.FAILSAFE_MODE)
        except Exception as exc:
            self._finish_failsafe(
                False, str(exc), exc.__class__.__name__, generation
            )
            return
        if isinstance(result, Future):
            result.add_done_callback(
                lambda future: self._failsafe_future_completed(future, generation)
            )
            return
        accepted = bool(getattr(result, "accepted", result))
        message = getattr(result, "message", "")
        self._finish_failsafe(
            accepted,
            None if accepted else (str(message) or "Komut kabul edilmedi"),
            None,
            generation,
        )

    def _failsafe_future_completed(self, future: Future, generation: int) -> None:
        try:
            result = future.result()
        except Exception as exc:
            self._finish_failsafe(
                False, str(exc), exc.__class__.__name__, generation
            )
            return
        accepted = bool(getattr(result, "accepted", result))
        message = getattr(result, "message", "")
        self._finish_failsafe(
            accepted,
            None if accepted else (str(message) or "Komut kabul edilmedi"),
            None,
            generation,
        )

    def _finish_failsafe(
        self,
        accepted: bool,
        error: str | None,
        error_type: str | None,
        generation: int,
    ) -> None:
        with self._lock:
            if generation != self._failsafe_generation:
                return
            self._failsafe_pending = False
            if accepted:
                self._failsafe_dispatched = True
                if self._logger:
                    self._logger.event(
                        "battery.failsafe_accepted",
                        "RTL failsafe komutu komut servisi tarafından kabul edildi",
                        severity="warning",
                        details={"mode": self.FAILSAFE_MODE},
                    )
                return
            self._failsafe_retry_after = (
                time.monotonic() + self.FAILSAFE_RETRY_DELAY_S
            )
            if self._failsafe_attempts >= self.MAX_FAILSAFE_ATTEMPTS:
                self._failsafe_dispatched = True
            if self._logger:
                self._logger.event(
                    "battery.failsafe_failed",
                    f"Batarya failsafe komutu başarısız: {error or 'bilinmeyen hata'}",
                    severity="danger",
                    details={
                        "mode": self.FAILSAFE_MODE,
                        "attempt": self._failsafe_attempts,
                        "retry_limit": self.MAX_FAILSAFE_ATTEMPTS,
                        "error_type": error_type,
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
