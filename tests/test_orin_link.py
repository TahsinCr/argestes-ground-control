from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from src.core.events import EventStream
from src.core.mavlink import (
    MavlinkApplicationPeer,
    MavlinkApplicationPacket,
    MavlinkApplicationResponse,
    MavlinkConnection,
    MavlinkEndpoint,
)
from src.models.commands import CommandRequest
from src.models.competition import CompetitionConnectionState
from src.models.orin import OrinCommandReceipt, OrinConnectionState
from src.models.vehicle import VehicleConnectionState
from src.services.logging_service import LoggingService
from src.services.vehicle.orin import configured_orin_commands
from src.services.commands import DispatchCommandService
from src.services.connections import BackendConnectionControlService
from src.services.vehicle.parameters import MutableParameterService
from src.services.vehicle.telemetry import MutableTelemetryService
from src.services.settings import SettingsService
from src.services.vehicle.orin import MavlinkOrinService
from src.services.video import MutableVideoService


class OrinLinkTests(unittest.TestCase):
    def test_application_peer_probes_and_correlates_responses(self) -> None:
        class Channel:
            def __init__(self) -> None:
                self.packets = EventStream()
                self.errors = EventStream()
                self.sent: list[tuple[MavlinkApplicationPacket, int, int]] = []
                self.running = False

            def start(self) -> None:
                self.running = True

            def stop(self) -> None:
                self.running = False

            def send(
                channel_self,
                packet_type,
                payload,
                *,
                packet_id=None,
                target_system=None,
                target_component=None,
            ):
                packet = MavlinkApplicationPacket(
                    packet_type,
                    payload or {},
                    packet_id or 1,
                )
                channel_self.sent.append(
                    (packet, int(target_system or 0), int(target_component or 0))
                )
                if packet.packet_type == "task.start":
                    channel_self.packets.publish(
                        MavlinkApplicationPacket(
                            "system.ack",
                            {"request_id": packet.packet_id, "accepted": True},
                            source_system=1,
                            source_component=191,
                        )
                    )
                return packet

        channel = Channel()
        peer = MavlinkApplicationPeer(  # type: ignore[arg-type]
            channel,
            role="yki",
            target_system=1,
            target_component=191,
            heartbeat_interval=60.0,
            heartbeat_timeout=120.0,
        )
        peer.start()
        try:
            peer.probe()
            exchange = peer.request(
                "task.start",
                {"task": "TARGET_LOCK"},
                response_types=frozenset({"system.ack", "system.error"}),
                timeout=0.5,
            )
            self.assertTrue(exchange.response.payload["accepted"])
            self.assertEqual(
                exchange.response.payload["request_id"],
                exchange.request.packet_id,
            )
            channel.packets.publish(
                MavlinkApplicationPacket(
                    "system.ping",
                    {},
                    packet_id=77,
                    source_system=1,
                    source_component=191,
                )
            )
            pong = next(
                packet
                for packet, target_system, target_component in channel.sent
                if packet.packet_type == "system.pong"
                and target_system == 1
                and target_component == 191
            )
            self.assertEqual(pong.payload["request_id"], 77)
            self.assertTrue(peer.alive)
            self.assertGreaterEqual(peer.state.packets_in, 2)
            self.assertGreaterEqual(peer.state.packets_out, 4)
        finally:
            peer.stop()

    def test_yki_reads_configured_orin_command_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "commands": {
                            "ping_test": "orin_commands/ping_test.py",
                            "CAMERA_START": ["/usr/bin/python3", "camera.py"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                configured_orin_commands(config_path),
                ("PING_TEST", "CAMERA_START"),
            )

    def test_connection_ignores_onboard_heartbeat_until_pixhawk_heartbeat_arrives(self) -> None:
        class Raw:
            def __init__(self) -> None:
                self.heartbeats = [
                    SimpleNamespace(is_vehicle=False),
                    SimpleNamespace(is_vehicle=True),
                ]
                self.closed = False

            def wait_heartbeat(self, *, timeout):
                self.timeout = timeout
                return self.heartbeats.pop(0)

            @staticmethod
            def probably_vehicle_heartbeat(message) -> bool:
                return bool(message.is_vehicle)

            def close(self) -> None:
                self.closed = True

        raw = Raw()
        connection = MavlinkConnection(
            MavlinkEndpoint(heartbeat_timeout=0.2),
            connection_factory=lambda _endpoint: raw,
        )
        connection.start()
        self.assertEqual(raw.heartbeats, [])
        self.assertTrue(connection.is_connected)
        connection.stop()
        self.assertTrue(raw.closed)

    def test_orin_service_requires_heartbeat_and_waits_for_real_ack(self) -> None:
        vehicle = SimpleNamespace(
            connected=True,
            state=VehicleConnectionState(True, "COM3", 1, 1),
            connection_changed=EventStream(),
        )
        client = SimpleNamespace(
            endpoint=SimpleNamespace(uri="COM3", source_system=255, source_component=0),
            connection=SimpleNamespace(target_system=1),
        )
        logs = LoggingService()
        service = MavlinkOrinService(  # type: ignore[arg-type]
            client,
            vehicle,
            logger=logs.child("ORIN"),
        )
        received = []
        service.received.subscribe(received.append)
        service._on_packet(
            MavlinkApplicationPacket(
                "system.heartbeat",
                {"health": "ok", "active_task": "", "version": "test"},
                source_system=1,
                source_component=191,
            )
        )

        class ImmediateAckPeer:
            def request(
                _self,
                packet_type,
                payload,
                *,
                response_types,
                timeout,
                on_sent=None,
            ):
                del response_types, timeout
                request = MavlinkApplicationPacket(packet_type, payload or {})
                if on_sent is not None:
                    on_sent(request)
                response = MavlinkApplicationPacket(
                    "system.ack",
                    {
                        "request_id": request.packet_id,
                        "accepted": True,
                        "message": "command completed: TARGET_LOCK",
                        "active_task": "TARGET_LOCK",
                        "task": "TARGET_LOCK",
                        "execution": "completed",
                        "exit_code": 0,
                        "stdout": "ok",
                    },
                    source_system=1,
                    source_component=191,
                )
                service._on_packet(
                    response
                )
                return MavlinkApplicationResponse(request, response, 2.0)

        service._peer = ImmediateAckPeer()  # type: ignore[assignment]
        service._started = True
        receipt = service.issue("task.start", {"task": "TARGET_LOCK"})
        self.assertTrue(receipt.accepted)
        self.assertEqual(receipt.message, "command completed: TARGET_LOCK")
        self.assertEqual(service.state.active_task, "TARGET_LOCK")
        self.assertEqual(received[0].packet_type, "system.heartbeat")
        self.assertEqual(received[-1].packet_type, "system.ack")
        self.assertEqual(logs.history[-1].action, "orin.script_completed")
        self.assertEqual(logs.history[-1].details["task"], "TARGET_LOCK")
        self.assertEqual(logs.history[-1].details["stdout"], "ok")

    def test_autonomous_processing_task_is_routed_to_orin_not_pixhawk(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "COM3", 1, 1)

            def execute(self, request) -> bool:
                raise AssertionError(f"Orin task leaked to Pixhawk: {request.code}")

        class Orin:
            def issue(self, packet_type, payload):
                self.packet_type = packet_type
                self.payload = payload
                return OrinCommandReceipt(91, True, "accepted", {"active_task": payload["task"]}, 12.5)

        orin = Orin()
        service = DispatchCommandService(
            Vehicle(),  # type: ignore[arg-type]
            MutableParameterService(),
            LoggingService().child("COMMAND"),
            orin=orin,  # type: ignore[arg-type]
        )
        result = service.dispatch(
            CommandRequest("SET_MISSION_TASK", {"task": "KAMIKAZE", "target_id": 27})
        )
        self.assertTrue(result.accepted)
        self.assertEqual(orin.packet_type, "task.start")
        self.assertEqual(orin.payload["task"], "KAMIKAZE")
        self.assertEqual(orin.payload["parameters"], {"target_id": 27})
        self.assertEqual(result.details["target"], "orin")
        self.assertIn("Orin", result.message)

    def test_configured_ping_task_is_exposed_and_routed_to_orin(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "COM3", 1, 1)

            def execute(self, request) -> bool:
                raise AssertionError(f"Configured Orin task leaked to Pixhawk: {request.code}")

        class Orin:
            def issue(self, packet_type, payload):
                self.packet_type = packet_type
                self.payload = payload
                return OrinCommandReceipt(
                    92,
                    True,
                    "command completed: PING_TEST",
                    {
                        "task": "PING_TEST",
                        "execution": "completed",
                        "exit_code": 0,
                    },
                    8.5,
                )

        orin = Orin()
        service = DispatchCommandService(
            Vehicle(),  # type: ignore[arg-type]
            MutableParameterService(),
            LoggingService().child("COMMAND"),
            orin=orin,  # type: ignore[arg-type]
            configured_orin_tasks=("PING_TEST",),
        )
        self.assertIn("PING_TEST", service.available_tasks)
        result = service.dispatch(
            CommandRequest("SET_MISSION_TASK", {"task": "PING_TEST", "value": 27})
        )
        self.assertTrue(result.accepted)
        self.assertEqual(orin.packet_type, "task.start")
        self.assertEqual(orin.payload["task"], "PING_TEST")
        self.assertEqual(orin.payload["parameters"]["value"], 27)
        self.assertEqual(result.details["orin_execution"], "completed")
        self.assertIn("scripti başarıyla tamamlandı", result.message)

    def test_orin_status_uses_its_heartbeat_but_shared_vehicle_connection_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            settings.set_value("backend.mavlink.endpoint", "COM3")
            settings.set_value("backend.mavlink.baud", 57_600)
            vehicle = SimpleNamespace(
                connected=True,
                state=VehicleConnectionState(True, "COM3", 1, 1),
                connection_changed=EventStream(),
            )
            orin = SimpleNamespace(
                connected=True,
                state=OrinConnectionState(
                    alive=True,
                    endpoint="COM3",
                    system_id=1,
                    component_id=191,
                    health="ok",
                    active_task="WAYPOINT_TRACK",
                    last_heartbeat_at=datetime.now(UTC),
                    last_packet_at=datetime.now(UTC),
                    packets_in=5,
                    packets_out=2,
                ),
                changed=EventStream(),
            )
            competition = SimpleNamespace(
                state=CompetitionConnectionState(
                    connected=False,
                    authenticated=False,
                    running=False,
                    endpoint="http://127.0.0.1:5000",
                    team_number=1,
                ),
                changed=EventStream(),
            )
            control = BackendConnectionControlService(
                settings=settings,
                vehicle=vehicle,  # type: ignore[arg-type]
                telemetry=MutableTelemetryService(),
                video=MutableVideoService(),
                competition=competition,
                orin=orin,  # type: ignore[arg-type]
            )
            status = control.status("orin")
            self.assertTrue(status.connected)
            self.assertEqual(status.endpoint, "COM3")
            self.assertEqual(status.options["baud"], 57_600)
            self.assertTrue(status.options["shared_vehicle_link"])
            self.assertIn("WAYPOINT_TRACK", status.detail)

    def test_orin_reconnect_probes_shared_link_without_reconnecting_vehicle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))

            class Vehicle:
                connected = True
                state = VehicleConnectionState(True, "COM3", 1, 1)
                connection_changed = EventStream()

                def reconnect(self, _config) -> None:
                    raise AssertionError("Orin reconnect opened the vehicle transport")

            class Orin:
                def __init__(self) -> None:
                    self.connected = False
                    self.running = False
                    self.state = OrinConnectionState(
                        endpoint="COM3",
                        system_id=1,
                        component_id=191,
                    )
                    self.changed = EventStream()
                    self.config = None
                    self.probes = 0

                def configure(self, config) -> None:
                    self.config = config

                def start(self) -> None:
                    self.running = True

                def stop(self) -> None:
                    self.running = False

                def probe(self) -> int:
                    self.probes += 1
                    return 19

            orin = Orin()
            competition = SimpleNamespace(
                state=CompetitionConnectionState(
                    connected=False,
                    authenticated=False,
                    running=False,
                    endpoint="http://127.0.0.1:5000",
                    team_number=1,
                ),
                changed=EventStream(),
            )
            control = BackendConnectionControlService(
                settings=settings,
                vehicle=Vehicle(),  # type: ignore[arg-type]
                telemetry=MutableTelemetryService(),
                video=MutableVideoService(),
                competition=competition,
                orin=orin,  # type: ignore[arg-type]
            )
            status = control.reconnect("orin", {"timeout_s": 9})
            self.assertFalse(status.connected)
            self.assertTrue(orin.running)
            self.assertEqual(orin.probes, 1)
            self.assertIsNotNone(orin.config)
            self.assertTrue(settings.current.backend.orin.enabled)
            self.assertEqual(settings.current.backend.orin.heartbeat_timeout_s, 9)


if __name__ == "__main__":
    unittest.main()
