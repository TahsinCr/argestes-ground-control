from __future__ import annotations

import time
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from src.models.settings import BatteryAlarmSettings
from src.models.telemetry import TelemetrySnapshot
from extensions.battery.alarm import BatteryAlarmService
from src.services.logging_service import LoggingService


class BatteryAlarmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logs = LoggingService()
        self.service = BatteryAlarmService(self.logs.child("BATTERY"))
        self.base = TelemetrySnapshot.zero().copy(
            telemetry_link=True,
            autopilot_link=True,
            battery_percent=19,
            battery_voltage=14.8,
        )

    def test_percent_alarm_logs_once_then_at_each_percent_change(self) -> None:
        settings = BatteryAlarmSettings(
            warning_mode="percent",
            warning_threshold=20,
        )
        self.assertTrue(self.service.evaluate(self.base, settings))
        self.assertTrue(self.service.evaluate(self.base, settings))
        self.assertEqual(len(self.logs.history), 1)

        self.assertTrue(
            self.service.evaluate(
                self.base.copy(battery_percent=18, battery_voltage=14.7),
                settings,
            )
        )
        self.assertEqual(len(self.logs.history), 2)
        self.assertEqual(self.logs.history[-1].action, "battery.low")
        self.assertEqual(self.logs.history[-1].details["percent"], 18)

        self.assertFalse(
            self.service.evaluate(self.base.copy(battery_percent=21), settings)
        )
        self.assertTrue(self.service.evaluate(self.base, settings))
        self.assertEqual(len(self.logs.history), 3)

    def test_voltage_alarm_uses_voltage_but_relogs_on_percent_change(self) -> None:
        settings = BatteryAlarmSettings(
            warning_mode="voltage",
            warning_threshold=14.5,
        )
        self.assertFalse(self.service.evaluate(self.base, settings))

        low_voltage = self.base.copy(battery_voltage=14.4)
        self.assertTrue(self.service.evaluate(low_voltage, settings))
        self.assertTrue(
            self.service.evaluate(
                low_voltage.copy(battery_percent=18),
                settings,
            )
        )
        self.assertEqual(len(self.logs.history), 2)
        self.assertEqual(self.logs.history[-1].details["mode"], "voltage")

    def test_disconnected_or_missing_voltage_does_not_alarm(self) -> None:
        percent_settings = BatteryAlarmSettings(
            warning_mode="percent",
            warning_threshold=20,
        )
        self.assertFalse(
            self.service.evaluate(
                self.base.copy(telemetry_link=False),
                percent_settings,
            )
        )
        voltage_settings = BatteryAlarmSettings(
            warning_mode="voltage",
            warning_threshold=15,
        )
        self.assertFalse(
            self.service.evaluate(
                self.base.copy(battery_voltage=0.0),
                voltage_settings,
            )
        )

    def test_disabled_failsafe_never_changes_mode_even_at_zero_percent(self) -> None:
        requested_modes: list[str] = []
        service = BatteryAlarmService(
            self.logs.child("BATTERY"),
            requested_modes.append,
        )
        settings = BatteryAlarmSettings(
            failsafe_enabled=False,
            warning_mode="percent",
            warning_threshold=20,
            failsafe_mode="percent",
            failsafe_threshold=10,
        )
        zero_battery = self.base.copy(
            armed=True,
            battery_percent=0,
            flight_mode="AUTO",
        )
        self.assertTrue(service.evaluate(zero_battery, settings))
        self.assertEqual(requested_modes, [])
        self.assertFalse(
            any(
                entry.action == "battery.failsafe_requested"
                for entry in self.logs.history
            )
        )

    def test_enabled_failsafe_requests_rtl_once_per_alarm_episode(self) -> None:
        requested_modes: list[str] = []
        service = BatteryAlarmService(
            self.logs.child("BATTERY"),
            requested_modes.append,
        )
        settings = BatteryAlarmSettings(
            failsafe_enabled=True,
            warning_mode="percent",
            warning_threshold=20,
            failsafe_mode="percent",
            failsafe_threshold=10,
        )
        warning_battery = self.base.copy(
            armed=True,
            flight_mode="AUTO",
        )
        self.assertTrue(service.evaluate(warning_battery, settings))
        self.assertEqual(requested_modes, [])
        self.assertTrue(
            service.evaluate(warning_battery.copy(battery_percent=9), settings)
        )
        self.assertEqual(requested_modes, ["RTL"])
        self.assertEqual(
            sum(
                entry.action == "battery.failsafe_requested"
                for entry in self.logs.history
            ),
            1,
        )

        self.assertFalse(
            service.evaluate(warning_battery.copy(battery_percent=30), settings)
        )
        self.assertTrue(
            service.evaluate(warning_battery.copy(battery_percent=9), settings)
        )
        self.assertEqual(requested_modes, ["RTL", "RTL"])

    def test_enabled_failsafe_waits_until_vehicle_is_armed(self) -> None:
        requested_modes: list[str] = []
        service = BatteryAlarmService(
            self.logs.child("BATTERY"),
            requested_modes.append,
        )
        settings = BatteryAlarmSettings(
            failsafe_enabled=True,
            warning_mode="percent",
            warning_threshold=20,
            failsafe_mode="voltage",
            failsafe_threshold=15,
        )
        low_battery = self.base.copy(
            battery_voltage=14.0,
            armed=False,
            flight_mode="AUTO",
        )
        self.assertTrue(service.evaluate(low_battery, settings))
        self.assertEqual(requested_modes, [])
        self.assertTrue(service.evaluate(low_battery.copy(armed=True), settings))
        self.assertEqual(requested_modes, ["RTL"])

    def test_warning_and_failsafe_thresholds_are_independent(self) -> None:
        requested_modes: list[str] = []
        service = BatteryAlarmService(
            self.logs.child("BATTERY"),
            requested_modes.append,
        )
        settings = BatteryAlarmSettings(
            warning_mode="voltage",
            warning_threshold=14.0,
            failsafe_enabled=True,
            failsafe_mode="percent",
            failsafe_threshold=10,
        )

        warning_only = self.base.copy(
            armed=True,
            battery_percent=15,
            battery_voltage=13.8,
            flight_mode="AUTO",
        )
        self.assertTrue(service.evaluate(warning_only, settings))
        self.assertEqual(requested_modes, [])
        self.assertEqual(self.logs.history[-1].action, "battery.low")

        critical_only = self.base.copy(
            armed=True,
            battery_percent=9,
            battery_voltage=14.8,
            flight_mode="AUTO",
        )
        self.assertTrue(service.evaluate(critical_only, settings))
        self.assertEqual(requested_modes, ["RTL"])
        self.assertEqual(
            sum(entry.action == "battery.low" for entry in self.logs.history),
            1,
        )

    def test_failed_failsafe_is_retried_once_after_backoff(self) -> None:
        outcomes = iter((False, True))
        requested_modes: list[str] = []

        def dispatch(mode: str) -> bool:
            requested_modes.append(mode)
            return next(outcomes)

        service = BatteryAlarmService(self.logs.child("BATTERY"), dispatch)
        settings = BatteryAlarmSettings(
            failsafe_enabled=True,
            failsafe_mode="percent",
            failsafe_threshold=10,
        )
        low_battery = self.base.copy(
            armed=True,
            battery_percent=9,
            flight_mode="AUTO",
        )

        with patch("extensions.battery.alarm.time.monotonic", return_value=0.0):
            service.evaluate(low_battery, settings)
            service.evaluate(low_battery, settings)
        self.assertEqual(requested_modes, ["RTL"])

        with patch("extensions.battery.alarm.time.monotonic", return_value=6.0):
            service.evaluate(low_battery, settings)
        self.assertEqual(requested_modes, ["RTL", "RTL"])
        self.assertTrue(service._failsafe_dispatched)
        self.assertFalse(service._failsafe_pending)

    def test_pending_failsafe_future_is_not_dispatched_twice(self) -> None:
        future: Future = Future()
        requested_modes: list[str] = []
        service = BatteryAlarmService(
            self.logs.child("BATTERY"),
            lambda mode: (requested_modes.append(mode), future)[1],
        )
        settings = BatteryAlarmSettings(
            failsafe_enabled=True,
            failsafe_mode="percent",
            failsafe_threshold=10,
        )
        low_battery = self.base.copy(
            armed=True,
            battery_percent=9,
            flight_mode="AUTO",
        )

        service.evaluate(low_battery, settings)
        service.evaluate(low_battery, settings)
        self.assertEqual(requested_modes, ["RTL"])
        self.assertTrue(service._failsafe_pending)

        future.set_result(type("Accepted", (), {"accepted": True, "message": ""})())
        self.assertFalse(service._failsafe_pending)
        self.assertTrue(service._failsafe_dispatched)
        service.close()

    def test_failsafe_future_timeout_is_unknown_and_never_automatically_retried(self) -> None:
        future: Future = Future()
        requested_modes: list[str] = []
        service = BatteryAlarmService(
            self.logs.child("BATTERY"),
            lambda mode: (requested_modes.append(mode), future)[1],
        )
        service.FAILSAFE_TIMEOUT_S = 0.02
        settings = BatteryAlarmSettings(
            failsafe_enabled=True,
            failsafe_mode="percent",
            failsafe_threshold=10,
        )
        low_battery = self.base.copy(
            armed=True,
            battery_percent=9,
            flight_mode="AUTO",
        )

        service.evaluate(low_battery, settings)
        deadline = time.monotonic() + 1.0
        while service._failsafe_pending and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertFalse(service._failsafe_pending)
        self.assertTrue(service._failsafe_dispatched)
        service.evaluate(low_battery, settings)
        self.assertEqual(requested_modes, ["RTL"])
        self.assertTrue(
            any(entry.action == "battery.failsafe_outcome_unknown" for entry in self.logs.history)
        )
        future.set_result(type("Accepted", (), {"accepted": True, "message": ""})())
        service.close()

    def test_close_invalidates_pending_failsafe_completion(self) -> None:
        future: Future = Future()
        service = BatteryAlarmService(self.logs.child("BATTERY"), lambda _mode: future)
        settings = BatteryAlarmSettings(
            failsafe_enabled=True,
            failsafe_mode="percent",
            failsafe_threshold=10,
        )
        low_battery = self.base.copy(
            armed=True,
            battery_percent=9,
            flight_mode="AUTO",
        )

        service.evaluate(low_battery, settings)
        service.close()
        future.set_result(type("Accepted", (), {"accepted": True, "message": ""})())

        self.assertFalse(service._failsafe_pending)
        self.assertFalse(service._failsafe_dispatched)
        self.assertFalse(service.evaluate(low_battery, settings))


if __name__ == "__main__":
    unittest.main()
