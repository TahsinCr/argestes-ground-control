from __future__ import annotations

import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.container import ApplicationContainer, VehicleStack, create_container
from src.core import DependencyContainer, EventBus
from src.core.mavlink import MavlinkClient, MavlinkConnection, MavlinkEndpoint
from src.services.runtime import RuntimeServices


ROOT = Path(__file__).resolve().parents[1]
CORE_REPOSITORY = "https://github.com/TahsinCr/vehicle-autonomy-core.git"


class _MavSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return call


class _RawConnection:
    def __init__(self) -> None:
        self.mav = _MavSender()
        self.messages: dict[str, object] = {}
        self.target_system = 1
        self.target_component = 1
        self.closed = False

    def wait_heartbeat(self, *, timeout: float):
        del timeout
        return SimpleNamespace(get_type=lambda: "HEARTBEAT")

    def close(self) -> None:
        self.closed = True


class CoreIntegrationTests(unittest.TestCase):
    def test_core_submodule_uses_the_canonical_repository(self) -> None:
        config = configparser.ConfigParser()
        config.read(ROOT / ".gitmodules", encoding="utf-8")

        section = 'submodule "src/core"'
        self.assertTrue(config.has_section(section))
        self.assertEqual(config.get(section, "path"), "src/core")
        self.assertEqual(config.get(section, "url"), CORE_REPOSITORY)
        self.assertTrue((ROOT / "src/core/pyproject.toml").is_file())
        self.assertTrue((ROOT / "src/core/tests").is_dir())

    def test_application_container_resolves_through_the_core_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            application = create_container(Path(directory))
            try:
                runtime = application.resolve(RuntimeServices)
                self.assertIsInstance(application, ApplicationContainer)
                self.assertIsInstance(application.container, DependencyContainer)
                self.assertIs(runtime.container, application.container)
                self.assertIs(application.resolve(RuntimeServices), runtime)
            finally:
                application.shutdown()

    def test_event_bus_keeps_other_service_consumers_alive_after_an_error(self) -> None:
        events = EventBus[int]()
        received: list[int] = []
        events.subscribe(
            lambda _value: (_ for _ in ()).throw(RuntimeError("consumer failed"))
        )
        events.subscribe(received.append)

        result = events.publish(4)

        self.assertEqual(received, [4])
        self.assertEqual(result.delivered, 1)
        self.assertEqual(result.failed, 1)

    def test_udpout_advertises_the_yki_endpoint_before_waiting_for_vehicle(self) -> None:
        raw = _RawConnection()
        mavlink = SimpleNamespace(
            MAV_TYPE_GCS=6,
            MAV_AUTOPILOT_INVALID=8,
            MAV_STATE_ACTIVE=4,
        )
        connection = MavlinkConnection(
            MavlinkEndpoint(
                uri="udpout:192.168.1.30:14550",
                heartbeat_timeout=0.1,
            ),
            connection_factory=lambda _endpoint: raw,
            mavutil_module=SimpleNamespace(mavlink=mavlink),
        )

        connection.start()
        try:
            method, arguments, keywords = raw.mav.calls[0]
            self.assertEqual(method, "heartbeat_send")
            self.assertEqual(arguments, (6, 8, 0, 0, 4))
            self.assertEqual(keywords, {})
            self.assertEqual(connection.sent_messages, 1)
        finally:
            connection.stop()

    def test_passive_udp_does_not_emit_a_discovery_heartbeat(self) -> None:
        raw = _RawConnection()
        connection = MavlinkConnection(
            MavlinkEndpoint(
                uri="udpin:0.0.0.0:14550",
                heartbeat_timeout=0.1,
            ),
            connection_factory=lambda _endpoint: raw,
            mavutil_module=SimpleNamespace(),
        )

        connection.start()
        try:
            self.assertEqual(raw.mav.calls, [])
            self.assertEqual(connection.sent_messages, 0)
        finally:
            connection.stop()


if __name__ == "__main__":
    unittest.main()
