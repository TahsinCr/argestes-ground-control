from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.core.events import EventBus
from src.models.connections import ConnectionPhase
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.vehicle import VehicleConnectionState
from src.services.settings import SettingsService
from extensions.mavlink.connection_control import VehicleConnectionControlService


class _Settings:
    def __init__(
        self, *, autoreconnect: bool = True, auto_connect: bool = False
    ) -> None:
        self.changed = EventBus()
        self.current = SettingsModel()
        self.current.backend.mavlink.endpoint = "udpin:0.0.0.0:14550"
        self.current.backend.mavlink.baud = 115200
        self.current.backend.mavlink.heartbeat_timeout = 10
        self.current.backend.mavlink.auto_connect = auto_connect
        self.current.backend.mavlink.autoreconnect = autoreconnect
        self.current.backend.mavlink.dialect = "ardupilotmega"
        self.current.backend.mavlink.source_system = 255
        self.current.backend.mavlink.source_component = 0

    def update(self, mutator, *, notify=True):
        self.current = mutator(self.current)
        if notify:
            self.changed.publish(self.current)
        return self.current


class _BlockingSettings(_Settings):
    def __init__(self) -> None:
        super().__init__(autoreconnect=False)
        self.update_started = threading.Event()
        self.finish_update = threading.Event()

    def update(self, mutator, *, notify=True):
        self.update_started.set()
        self.finish_update.wait(1)
        return super().update(mutator, notify=notify)


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


class _LateConnectingVehicle(_Vehicle):
    def __init__(self) -> None:
        super().__init__()
        self.connected = False
        self.state = VehicleConnectionState(False, "udpin:0.0.0.0:14550")
        self.started = threading.Event()
        self.finish_connect = threading.Event()

    def reconnect(self, _config=None) -> None:
        self.reconnect_calls += 1
        self.started.set()
        self.finish_connect.wait(1)
        self.connected = True
        self.state = VehicleConnectionState(True, "udpin:0.0.0.0:14550", 1, 1)
        self.connection_changed.publish(self.state)


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

    def test_disconnect_cancels_a_late_successful_connection_attempt(self) -> None:
        settings = _Settings(autoreconnect=False)
        vehicle = _LateConnectingVehicle()
        service = VehicleConnectionControlService(
            settings=settings,  # type: ignore[arg-type]
            vehicle=vehicle,  # type: ignore[arg-type]
            telemetry=_Telemetry(),  # type: ignore[arg-type]
        )
        statuses = []
        subscription = service.changed.subscribe(statuses.append)
        service.start()
        connect = threading.Thread(
            target=lambda: service.reconnect("telemetry", {}), daemon=True
        )
        disconnect = threading.Thread(
            target=lambda: service.disconnect("telemetry"), daemon=True
        )
        try:
            connect.start()
            self.assertTrue(vehicle.started.wait(1))
            self.assertEqual(
                service.status("telemetry").phase, ConnectionPhase.CONNECTING
            )
            disconnect.start()
            deadline = time.monotonic() + 1
            while (
                service.status("telemetry").phase != ConnectionPhase.DISCONNECTING
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)
            self.assertEqual(
                service.status("telemetry").phase, ConnectionPhase.DISCONNECTING
            )
            vehicle.finish_connect.set()
            connect.join(1)
            disconnect.join(1)
            self.assertFalse(connect.is_alive())
            self.assertFalse(disconnect.is_alive())
            self.assertFalse(vehicle.connected)
            self.assertGreaterEqual(vehicle.stop_calls, 2)
            self.assertTrue(
                any(item.phase == ConnectionPhase.CONNECTING for item in statuses)
            )
            self.assertEqual(
                service.status("telemetry").phase, ConnectionPhase.DISCONNECTED
            )
        finally:
            vehicle.finish_connect.set()
            subscription.cancel()
            service.stop()

    def test_disconnect_during_profile_save_prevents_transport_open(self) -> None:
        settings = _BlockingSettings()
        vehicle = _Vehicle()
        service = VehicleConnectionControlService(
            settings=settings,  # type: ignore[arg-type]
            vehicle=vehicle,  # type: ignore[arg-type]
            telemetry=_Telemetry(),  # type: ignore[arg-type]
        )
        service.start()
        connect = threading.Thread(
            target=lambda: service.reconnect("telemetry", {}), daemon=True
        )
        disconnect = threading.Thread(
            target=lambda: service.disconnect("telemetry"), daemon=True
        )
        try:
            connect.start()
            self.assertTrue(settings.update_started.wait(1))
            self.assertEqual(
                service.status("telemetry").phase, ConnectionPhase.CONNECTING
            )
            disconnect.start()
            deadline = time.monotonic() + 1
            while (
                service.status("telemetry").phase != ConnectionPhase.DISCONNECTING
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)
            self.assertEqual(
                service.status("telemetry").phase, ConnectionPhase.DISCONNECTING
            )
            settings.finish_update.set()
            connect.join(1)
            disconnect.join(1)
            self.assertFalse(connect.is_alive())
            self.assertFalse(disconnect.is_alive())
            self.assertEqual(vehicle.reconnect_calls, 0)
            self.assertFalse(vehicle.connected)
            self.assertEqual(
                service.status("telemetry").phase, ConnectionPhase.DISCONNECTED
            )
        finally:
            settings.finish_update.set()
            service.stop()

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

    def test_recovery_and_status_require_a_configured_profile(self) -> None:
        self.settings.current.backend.mavlink_connections = []
        self.settings.current.backend.active_mavlink_connection_id = ""
        self.vehicle.lose_heartbeat()
        time.sleep(0.05)

        self.assertEqual(self.vehicle.reconnect_calls, 0)
        status = self.service.status("telemetry")
        self.assertEqual(status.options["profile_id"], "")
        self.assertEqual(status.options["profile_name"], "")

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

    def test_profile_change_preserves_unrelated_settings_from_latest_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            settings.apply_changes(
                {
                    ("ui", "language"): "en",
                    ("backend", "mavlink", "dialect"): "common",
                }
            )
            service = object.__new__(VehicleConnectionControlService)
            service._settings = settings

            service._apply_profile_options(
                {
                    "profile_id": "field-link",
                    "profile_name": "Field link",
                    "endpoint": "udpin:127.0.0.1:14550",
                    "baud": 57600,
                    "timeout_s": 8,
                }
            )

            current = settings.current
            self.assertEqual(current.ui.language, "en")
            self.assertEqual(current.backend.mavlink.dialect, "common")
            self.assertEqual(current.backend.mavlink.endpoint, "udpin:127.0.0.1:14550")
