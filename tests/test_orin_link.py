from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from src.core.events import EventBus
from src.core.mavlink import (
    MavlinkApplicationAssembler,
    MavlinkApplicationCodec,
    MavlinkApplicationDispatcher,
    MavlinkApplicationPeer,
    MavlinkApplicationPacket,
    MavlinkApplicationResponse,
    MavlinkConnection,
    MavlinkEndpoint,
    MavlinkRemoteLogBatch,
    MavlinkRemoteLogLevel,
    MavlinkRemoteLogRecord,
)
from src.models.commands import CommandRequest
from src.models.competition import CompetitionConnectionState
from src.models.orin import OrinCommandReceipt, OrinConnectionState, OrinLogRecord
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
    def test_remote_log_batch_roundtrips_structured_records(self) -> None:
        batch = MavlinkRemoteLogBatch(
            "orin-test-session",
            (
                MavlinkRemoteLogRecord(
                    1,
                    "MISSION",
                    "mission.transition",
                    "Target lock mission started",
                    MavlinkRemoteLogLevel.SUCCESS,
                    details={
                        "mission_id": "target.lock",
                        "generation": 2,
                    },
                    correlation_id="target.lock:2",
                ),
            ),
        )

        decoded = MavlinkRemoteLogBatch.from_payload(batch.to_payload())

        self.assertEqual(decoded.session_id, "orin-test-session")
        self.assertEqual(decoded.records[0].source, "MISSION")
        self.assertEqual(decoded.records[0].level, MavlinkRemoteLogLevel.SUCCESS)
        self.assertEqual(
            decoded.records[0].details["mission_id"],
            "target.lock",
        )

    def test_orin_service_deduplicates_acknowledged_remote_logs(self) -> None:
        vehicle = SimpleNamespace(
            connected=True,
            state=VehicleConnectionState(True, "COM3", 1, 1),
            connection_changed=EventBus(),
        )
        client = SimpleNamespace(
            endpoint=SimpleNamespace(
                uri="COM3",
                source_system=255,
                source_component=0,
            ),
            connection=SimpleNamespace(target_system=1),
        )
        service = MavlinkOrinService(client, vehicle)  # type: ignore[arg-type]
        received: list[OrinLogRecord] = []
        service.remote_logs.subscribe(received.append)
        batch = MavlinkRemoteLogBatch(
            "orin-dedupe-session",
            (
                MavlinkRemoteLogRecord(
                    7,
                    "CAMERA",
                    "camera.connected",
                    "Camera connected",
                ),
                MavlinkRemoteLogRecord(
                    8,
                    "MISSION",
                    "mission.running",
                    "Mission is running",
                ),
            ),
        )
        packet = MavlinkApplicationPacket("logs.push", batch.to_payload())

        first = service._handle_remote_logs(packet)
        duplicate = service._handle_remote_logs(packet)

        self.assertEqual(first.payload["received"], 2)
        self.assertEqual(first.message, "Logs accepted")
        self.assertEqual(duplicate.payload["received"], 0)
        self.assertEqual(duplicate.payload["duplicates"], 2)
        self.assertEqual([record.sequence for record in received], [7, 8])
        self.assertEqual(received[0].session_id, "orin-dedupe-session")

    def test_orin_service_only_advances_log_cursor_after_delivery(self) -> None:
        vehicle = SimpleNamespace(
            connected=True,
            state=VehicleConnectionState(True, "COM3", 1, 1),
            connection_changed=EventBus(),
        )
        client = SimpleNamespace(
            endpoint=SimpleNamespace(
                uri="COM3",
                source_system=255,
                source_component=0,
            ),
            connection=SimpleNamespace(target_system=1),
        )
        service = MavlinkOrinService(client, vehicle)  # type: ignore[arg-type]
        failures = 1

        def persist(records: tuple[OrinLogRecord, ...]) -> None:
            nonlocal failures
            self.assertEqual(len(records), 1)
            if failures:
                failures -= 1
                raise OSError("simulated log storage failure")

        service.remote_log_batches.subscribe(persist)
        batch = MavlinkRemoteLogBatch(
            "orin-retry-session",
            (
                MavlinkRemoteLogRecord(
                    1,
                    "MISSION",
                    "mission.failed",
                    "Mission failed",
                    MavlinkRemoteLogLevel.ERROR,
                ),
            ),
        )
        packet = MavlinkApplicationPacket("logs.push", batch.to_payload())

        with self.assertRaises(ExceptionGroup):
            service._handle_remote_logs(packet)
        retried = service._handle_remote_logs(packet)

        self.assertEqual(retried.payload["received"], 1)
        self.assertEqual(retried.payload["duplicates"], 0)

    def test_application_peer_probes_and_correlates_responses(self) -> None:
        class Channel:
            def __init__(self) -> None:
                self.packets = EventBus()
                self.errors = EventBus()
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
                expects_response=False,
            ):
                packet = MavlinkApplicationPacket(
                    packet_type,
                    payload or {},
                    packet_id or 1,
                    expects_response=expects_response,
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
            self.assertGreaterEqual(peer.state.packets_out, 3)
        finally:
            peer.stop()

    def test_application_codec_preserves_request_semantics(self) -> None:
        packet = MavlinkApplicationPacket(
            "camera.capture",
            {"quality": 90},
            expects_response=True,
        )
        assembler = MavlinkApplicationAssembler()
        decoded = None
        for fragment in reversed(MavlinkApplicationCodec.encode(packet)):
            decoded = assembler.accept(
                fragment,
                source_system=1,
                source_component=191,
            ) or decoded
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertTrue(decoded.expects_response)
        self.assertEqual(decoded.packet_type, "camera.capture")
        self.assertEqual(decoded.payload, {"quality": 90})

    def test_dispatcher_routes_commands_and_correlates_acknowledgements(self) -> None:
        class Peer:
            def __init__(self) -> None:
                self.packets = EventBus()
                self.sent: list[tuple[MavlinkApplicationPacket, int, int]] = []

            def send(
                peer_self,
                packet_type,
                payload=None,
                *,
                target_system=None,
                target_component=None,
            ):
                packet = MavlinkApplicationPacket(packet_type, payload or {})
                peer_self.sent.append(
                    (packet, int(target_system or 0), int(target_component or 0))
                )
                return packet

        peer = Peer()
        dispatcher = MavlinkApplicationDispatcher(peer)  # type: ignore[arg-type]
        handled = threading.Event()
        dispatcher.register(
            "camera.capture",
            lambda packet: (
                handled.set(),
                {"frame_id": packet.payload["frame_id"]},
            )[1],
        )
        dispatcher.start()
        try:
            peer.packets.publish(
                MavlinkApplicationPacket(
                    "camera.capture",
                    {"frame_id": 27},
                    packet_id=81,
                    source_system=1,
                    source_component=191,
                    expects_response=True,
                )
            )
            self.assertTrue(handled.wait(0.5))
            deadline = datetime.now(UTC).timestamp() + 0.5
            while not peer.sent and datetime.now(UTC).timestamp() < deadline:
                threading.Event().wait(0.01)
            response, target_system, target_component = peer.sent[-1]
            self.assertEqual(response.packet_type, "system.ack")
            self.assertEqual(response.payload["request_id"], 81)
            self.assertEqual(response.payload["frame_id"], 27)
            self.assertEqual((target_system, target_component), (1, 191))
        finally:
            dispatcher.stop()

    def test_shared_application_protocol_supports_requests_in_both_directions(self) -> None:
        class LinkedChannel:
            def __init__(self, system_id: int, component_id: int) -> None:
                self.system_id = system_id
                self.component_id = component_id
                self.packets = EventBus()
                self.errors = EventBus()
                self.remote = None

            def start(self) -> None:
                return

            def stop(self) -> None:
                return

            def send(
                channel_self,
                packet_type,
                payload=None,
                *,
                packet_id=None,
                target_system=None,
                target_component=None,
                expects_response=False,
            ):
                del target_system, target_component
                packet = MavlinkApplicationPacket(
                    packet_type,
                    payload or {},
                    packet_id or 1,
                    source_system=channel_self.system_id,
                    source_component=channel_self.component_id,
                    expects_response=expects_response,
                )
                channel_self.remote.packets.publish(packet)
                return packet

        yki_channel = LinkedChannel(255, 0)
        orin_channel = LinkedChannel(1, 191)
        yki_channel.remote = orin_channel
        orin_channel.remote = yki_channel
        yki_peer = MavlinkApplicationPeer(  # type: ignore[arg-type]
            yki_channel,
            role="yki",
            target_system=1,
            target_component=191,
            heartbeat_interval=60.0,
            heartbeat_timeout=120.0,
        )
        orin_peer = MavlinkApplicationPeer(  # type: ignore[arg-type]
            orin_channel,
            role="orin",
            target_system=255,
            target_component=0,
            heartbeat_interval=60.0,
            heartbeat_timeout=120.0,
        )
        yki_dispatcher = MavlinkApplicationDispatcher(yki_peer)
        orin_dispatcher = MavlinkApplicationDispatcher(orin_peer)
        orin_dispatcher.register(
            "camera.capture",
            lambda packet: {"frame_id": packet.payload["frame_id"]},
        )
        yki_dispatcher.register(
            "operator.confirm",
            lambda packet: {"confirmed": packet.payload["question"] == "continue"},
        )
        yki_dispatcher.start()
        orin_dispatcher.start()
        yki_peer.start()
        orin_peer.start()
        try:
            onboard_response = yki_peer.request(
                "camera.capture",
                {"frame_id": 27},
                response_types=frozenset({"system.ack", "system.error"}),
                timeout=0.5,
            )
            gcs_response = orin_peer.request(
                "operator.confirm",
                {"question": "continue"},
                response_types=frozenset({"system.ack", "system.error"}),
                timeout=0.5,
            )
            self.assertEqual(onboard_response.response.payload["frame_id"], 27)
            self.assertTrue(gcs_response.response.payload["confirmed"])
            self.assertEqual(onboard_response.response.source_component, 191)
            self.assertEqual(gcs_response.response.source_system, 255)
        finally:
            yki_dispatcher.stop()
            orin_dispatcher.stop()
            yki_peer.stop()
            orin_peer.stop()

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
            connection_changed=EventBus(),
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
                connection_changed=EventBus(),
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
                changed=EventBus(),
            )
            competition = SimpleNamespace(
                state=CompetitionConnectionState(
                    connected=False,
                    authenticated=False,
                    running=False,
                    endpoint="http://127.0.0.1:5000",
                    team_number=1,
                ),
                changed=EventBus(),
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
                connection_changed = EventBus()

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
                    self.changed = EventBus()
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
                changed=EventBus(),
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
