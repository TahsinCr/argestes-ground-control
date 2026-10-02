from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from src.core.events import EventBus
from src.models.telemetry import TelemetrySnapshot
from src.models.vehicle import VehicleConnectionState
from extensions.mavlink.connection_control import VehicleConnectionControlService


class _Settings:
    def __init__(
        self, *, autoreconnect: bool = True, auto_connect: bool = False
    ) -> None:
        self.changed = EventBus()
        self.current = SimpleNamespace(
            backend=SimpleNamespace(
                mavlink=SimpleNamespace(
                    endpoint="udpin:0.0.0.0:14550",
                    baud=115200,
                    heartbeat_timeout=10,
                    auto_connect=auto_connect,
                    autoreconnect=autoreconnect,
                    dialect="ardupilotmega",
                    source_system=255,
                    source_component=0,
                ),
                active_mavlink_connection_id="telemetry",
                mavlink_connections=(),
            )
        )


class _Vehicle:
    def __init__(self) -> None:
        self.connected = True
        self.state = VehicleConnectionState(True, "udpin:0.0.0.0:14550", 1, 1)
        self.connection_changed = EventBus[VehicleConnectionState]()
        self.reconnect_calls = 0
        self.stop_calls = 0

    def reconnect(self, _config=None) -> None:
        self.reconnect_calls += 1
        self.connected = True
        self.state = VehicleConnectionState(True, "udpin:0.0.0.0:14550", 1, 1)
        self.connection_changed.publish(self.state)

    def stop(self) -> None:
        self.stop_calls += 1
        self.connected = False
        self.state = VehicleConnectionState(False, "udpin:0.0.0.0:14550")
        self.connection_changed.publish(self.state)

    def lose_heartbeat(self) -> None:
        self.connected = False
        self.state = VehicleConnectionState(False, "udpin:0.0.0.0:14550")
        self.connection_changed.publish(self.state)


class _Telemetry:
    current = TelemetrySnapshot.zero()


class _FailingVehicle(_Vehicle):
    def reconnect(self, _config=None) -> None:
        self.reconnect_calls += 1
        # The production adapter emits this transition from reconnect()'s
        # internal stop.  It must not reset the exponential retry delay.
        self.lose_heartbeat()
        raise ConnectionError("test transport unavailable")


class VehicleConnectionControlTests(unittest.TestCase):
    def test_dialect_change_reconfigures_connected_transport_in_background(self):
        import threading
        from unittest.mock import Mock

        caller = threading.get_ident()
        finished = threading.Event()
        captured = []

        def reconnect(config):
            captured.append((threading.get_ident(), config))
            finished.set()

        self.vehicle.reconnect = Mock(side_effect=reconnect)
        self.settings.current.backend.mavlink.dialect = "common"
        self.settings.changed.publish(self.settings.current)
        self.assertTrue(finished.wait(1))
        self.assertNotEqual(captured[0][0], caller)
        self.assertEqual(captured[0][1].options["dialect"], "common")
        self.settings.changed.publish(self.settings.current)
        self.vehicle.reconnect.assert_called_once()

    def setUp(self) -> None:
        self.settings = _Settings()
        self.vehicle = _Vehicle()
        self.service = VehicleConnectionControlService(
            settings=self.settings,  # type: ignore[arg-type]
            vehicle=self.vehicle,  # type: ignore[arg-type]
            telemetry=_Telemetry(),  # type: ignore[arg-type]
        )
        self.service._INITIAL_RETRY_DELAY_S = 0.01
        self.service.start()

    def tearDown(self) -> None:
        self.service.stop()

    def test_heartbeat_loss_reconnects_the_existing_vehicle_transport(self) -> None:
        self.vehicle.lose_heartbeat()
        deadline = time.monotonic() + 1.0
        while self.vehicle.reconnect_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(self.vehicle.reconnect_calls, 1)
        self.assertTrue(self.vehicle.connected)

    def test_manual_disconnect_suppresses_automatic_recovery(self) -> None:
        self.service.disconnect("telemetry")
        time.sleep(0.05)

        self.assertEqual(self.vehicle.stop_calls, 1)
        self.assertEqual(self.vehicle.reconnect_calls, 0)
        self.assertFalse(self.service.status("telemetry").connected)

    def test_auto_connect_is_attempted_once_without_enabling_recovery(self) -> None:
        self.service.stop()
        self.settings = _Settings(autoreconnect=False, auto_connect=True)
        self.vehicle = _Vehicle()
        self.service = VehicleConnectionControlService(
            settings=self.settings,  # type: ignore[arg-type]
            vehicle=self.vehicle,  # type: ignore[arg-type]
            telemetry=_Telemetry(),  # type: ignore[arg-type]
        )
        self.service._INITIAL_RETRY_DELAY_S = 0.01
        self.vehicle.lose_heartbeat()
        self.service.start()
        deadline = time.monotonic() + 1.0
        while self.vehicle.reconnect_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(self.vehicle.reconnect_calls, 1)

    def test_no_automatic_setting_means_no_initial_connection_attempt(self) -> None:
        self.service.stop()
        self.settings = _Settings(autoreconnect=False, auto_connect=False)
        self.vehicle = _Vehicle()
        self.vehicle.lose_heartbeat()
        self.service = VehicleConnectionControlService(
            settings=self.settings,  # type: ignore[arg-type]
            vehicle=self.vehicle,  # type: ignore[arg-type]
            telemetry=_Telemetry(),  # type: ignore[arg-type]
        )
        self.service._INITIAL_RETRY_DELAY_S = 0.01
        self.service.start()
        time.sleep(0.05)

        self.assertEqual(self.vehicle.reconnect_calls, 0)

    def test_failed_reconnect_keeps_the_exponential_retry_delay(self) -> None:
        self.service.stop()
        self.vehicle = _FailingVehicle()
        self.service = VehicleConnectionControlService(
            settings=self.settings,  # type: ignore[arg-type]
            vehicle=self.vehicle,  # type: ignore[arg-type]
            telemetry=_Telemetry(),  # type: ignore[arg-type]
        )
        self.service._INITIAL_RETRY_DELAY_S = 0.01
        self.service.start()
        self.vehicle.lose_heartbeat()
        deadline = time.monotonic() + 1.0
        # The call counter changes before the worker commits its backoff.
        while time.monotonic() < deadline:
            with self.service._state_lock:
                if (
                    self.service._retry_attempt == 1
                    and self.service._next_retry_at is not None
                ):
                    break
            time.sleep(0.005)

        with self.service._state_lock:
            retry_at = self.service._next_retry_at
            retry_attempt = self.service._retry_attempt
        self.assertEqual(retry_attempt, 1)
        self.assertIsNotNone(retry_at)
        self.assertGreaterEqual(float(retry_at) - time.monotonic(), 0.8)
