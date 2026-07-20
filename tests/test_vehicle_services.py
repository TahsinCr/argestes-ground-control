from __future__ import annotations

import math
from datetime import UTC, datetime
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.core.events import EventStream, Subscription
from src.models.commands import CommandRequest
from src.models.mission import MapOverlay, MissionState
from src.models.parameters import Parameter
from src.models.telemetry import TelemetrySnapshot
from src.models.vehicle import VehicleConnectionState, VehicleTransportStats
from src.services.logging_service import LoggingService
from src.services.orchestration.commands import DispatchCommandService
from src.services.processing.parameters import MutableParameterService
from src.services.processing.parameters import VehicleParameterService
from src.services.processing.telemetry import TelemetryDecoder, VehicleTelemetryService
from src.services.vehicle.mavlink import MavlinkVehicleService
from src.services.video import GStreamerVideoService


class _Message(SimpleNamespace):
    def get_type(self) -> str:
        return self.message_type


class _FakeVehicle:
    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.state = VehicleConnectionState(connected, "test", 1, 1)
        self.connection_changed = EventStream[VehicleConnectionState]()
        self.subscriptions: dict[str, list] = {}
        self.parameter_requests = 0
        self.parameter_writes: list[tuple[str, object, int | None]] = []
        self.transport_stats = VehicleTransportStats(
            received_packets=0,
            sent_packets=0,
            delivery_quality=100,
        )

    def subscribe(self, message_types, callback):
        types = (message_types,) if isinstance(message_types, str) else tuple(message_types)
        for message_type in types:
            self.subscriptions.setdefault(message_type, []).append(callback)
        return Subscription(lambda: None)

    def request_parameters(self) -> None:
        self.parameter_requests += 1

    def set_parameter(self, name: str, value: object, value_type: int | None = None) -> None:
        self.parameter_writes.append((name, value, value_type))
        self.publish(
            _Message(
                message_type="PARAM_VALUE",
                param_id=name.encode("ascii"),
                param_value=float(value),
                param_count=1,
                param_index=-1,
                param_type=value_type or 9,
            )
        )

    def mode_name(self, _message, fallback="UNKNOWN") -> str:
        return fallback

    def publish(self, message) -> None:
        for callback in self.subscriptions.get(message.get_type(), []):
            callback(message)


class VehicleServiceTests(unittest.TestCase):
    def test_domain_services_subscribe_through_vehicle_abstraction(self) -> None:
        vehicle = _FakeVehicle()
        telemetry = VehicleTelemetryService(vehicle)  # type: ignore[arg-type]
        parameters = VehicleParameterService(
            [Parameter("KNOWN", 1, "-", "test", "Known")],
            vehicle,  # type: ignore[arg-type]
        )
        telemetry.start()
        parameters.start()
        self.assertIn("HEARTBEAT", vehicle.subscriptions)
        self.assertIn("PARAM_VALUE", vehicle.subscriptions)
        self.assertEqual(vehicle.parameter_requests, 1)
        parameters.stop()
        telemetry.stop()

    def test_telemetry_decoder_normalizes_vehicle_units(self) -> None:
        message = _Message(
            message_type="GLOBAL_POSITION_INT",
            lat=399000000,
            lon=328000000,
            alt=125_500,
            relative_alt=12_300,
            vx=300,
            vy=400,
            vz=-150,
            hdg=9_000,
        )
        snapshot = TelemetryDecoder().apply(TelemetrySnapshot.zero(), message)
        self.assertAlmostEqual(snapshot.latitude, 39.9)
        self.assertAlmostEqual(snapshot.longitude, 32.8)
        self.assertAlmostEqual(snapshot.ground_speed, 5.0)
        self.assertAlmostEqual(snapshot.climb_rate, 1.5)
        self.assertAlmostEqual(snapshot.heading, 90.0)
        self.assertTrue(snapshot.position_valid)
        self.assertTrue(snapshot.has_valid_position)
        self.assertIsNotNone(snapshot.position_received_at)

    def test_telemetry_decoder_exposes_critical_flight_readouts(self) -> None:
        decoder = TelemetryDecoder()
        snapshot = decoder.apply(
            TelemetrySnapshot.zero(),
            _Message(
                message_type="VFR_HUD",
                airspeed=27.5,
                groundspeed=25.0,
                heading=182,
                throttle=63,
                alt=123.4,
                climb=-1.2,
            ),
        )
        snapshot = decoder.apply(
            snapshot,
            _Message(message_type="RC_CHANNELS", rssi=200),
        )
        snapshot = decoder.apply(
            snapshot,
            _Message(message_type="EKF_STATUS_REPORT", flags=19),
        )
        snapshot = decoder.apply(
            snapshot,
            _Message(message_type="HOME_POSITION", latitude=399000000, longitude=328000000),
        )
        snapshot = decoder.apply(
            snapshot,
            _Message(
                message_type="GLOBAL_POSITION_INT",
                lat=399010000,
                lon=328010000,
                alt=125_500,
                relative_alt=12_300,
                vx=300,
                vy=400,
                vz=-150,
                hdg=9_000,
            ),
        )
        self.assertEqual(snapshot.air_speed, 27.5)
        self.assertEqual(snapshot.throttle_percent, 63)
        self.assertEqual(snapshot.rc_rssi, 200)
        self.assertTrue(snapshot.ekf_ok)
        self.assertGreater(snapshot.home_distance, 0)
        self.assertGreaterEqual(snapshot.home_heading, 0)

    def test_radio_status_uses_both_rf_margins_and_tx_buffer_for_link_health(self) -> None:
        snapshot = TelemetryDecoder().apply(
            TelemetrySnapshot.zero(),
            _Message(
                message_type="RADIO_STATUS",
                rssi=150,
                noise=100,
                remrssi=135,
                remnoise=100,
                txbuf=91,
            ),
        )
        self.assertEqual(snapshot.link_quality_source, "rf")
        self.assertGreaterEqual(snapshot.link_quality, 50)
        self.assertLessEqual(snapshot.link_quality, 65)
        self.assertEqual(snapshot.radio_local_rssi, 150)
        self.assertEqual(snapshot.radio_remote_rssi, 135)
        self.assertIsNotNone(snapshot.link_quality_received_at)
        self.assertTrue(snapshot.has_fresh_link_quality(5))

    def test_vehicle_telemetry_uses_packet_delivery_when_radio_status_is_absent(self) -> None:
        vehicle = _FakeVehicle()
        vehicle.transport_stats = VehicleTransportStats(
            received_packets=128,
            sent_packets=7,
            estimated_dropped_packets=4,
            delivery_quality=97,
        )
        telemetry = VehicleTelemetryService(vehicle)  # type: ignore[arg-type]
        telemetry.start()
        vehicle.publish(
            _Message(
                message_type="HEARTBEAT",
                base_mode=0,
                system_status=3,
            )
        )
        snapshot = telemetry.current
        telemetry.stop()
        self.assertEqual(snapshot.link_quality, 97)
        self.assertEqual(snapshot.link_quality_source, "mavlink_packets")
        self.assertEqual(snapshot.packets_in, 128)
        self.assertEqual(snapshot.packets_out, 7)

    def test_gps_hdop_is_decoded_and_unknown_value_is_not_displayed(self) -> None:
        decoder = TelemetryDecoder()
        snapshot = decoder.apply(
            TelemetrySnapshot.zero(),
            _Message(
                message_type="GPS_RAW_INT",
                fix_type=3,
                satellites_visible=14,
                eph=83,
                time_usec=0,
            ),
        )
        self.assertAlmostEqual(snapshot.gps_hdop or 0.0, 0.83)
        snapshot = decoder.apply(
            snapshot,
            _Message(
                message_type="GPS_RAW_INT",
                fix_type=1,
                satellites_visible=0,
                eph=65_535,
                time_usec=0,
            ),
        )
        self.assertIsNone(snapshot.gps_hdop)

    def test_arm_command_waits_for_heartbeat_state_confirmation(self) -> None:
        mavlink = SimpleNamespace(
            MAV_CMD_COMPONENT_ARM_DISARM=400,
            MAV_MODE_FLAG_SAFETY_ARMED=128,
            MAV_RESULT_IN_PROGRESS=5,
            MAV_RESULT_ACCEPTED=0,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(target_system=1, target_component=1)
            router = SimpleNamespace(sequence=0)
            is_connected = True

            def __init__(self) -> None:
                self.mavlink = mavlink
                self.waited_for: list[str] = []
                self.sent: list[tuple[str, tuple]] = []

            def call_mav(self, method: str, *args) -> None:
                self.sent.append((method, args))

            def wait_for(self, message_type, **_kwargs):
                self.waited_for.append(message_type)
                if message_type == "COMMAND_ACK":
                    return SimpleNamespace(command=400, result=0)
                return SimpleNamespace(base_mode=128)

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 1, 1)
        self.assertTrue(vehicle._arm(True))
        self.assertEqual(client.waited_for, ["COMMAND_ACK", "HEARTBEAT"])
        self.assertEqual(client.sent[-1][1][4:6], (1.0, 0.0))

        self.assertTrue(
            vehicle.execute(
                CommandRequest("FORCE_ARM", {"operator_confirmation": True})
            )
        )
        self.assertEqual(client.waited_for[-2:], ["COMMAND_ACK", "HEARTBEAT"])
        self.assertEqual(client.sent[-1][1][4:6], (1.0, 21_196.0))

        with self.assertRaises(PermissionError):
            vehicle.execute(CommandRequest("FORCE_ARM"))

    def test_zero_position_is_not_exposed_as_a_real_vehicle_location(self) -> None:
        heartbeat = _Message(message_type="HEARTBEAT", base_mode=0, system_status=3)
        snapshot = TelemetryDecoder().apply(TelemetrySnapshot.zero(), heartbeat)
        self.assertTrue(snapshot.telemetry_link)
        self.assertFalse(snapshot.position_valid)

        zero_position = _Message(
            message_type="GLOBAL_POSITION_INT",
            lat=0,
            lon=0,
            alt=0,
            relative_alt=0,
            vx=0,
            vy=0,
            vz=0,
            hdg=0,
        )
        snapshot = TelemetryDecoder().apply(snapshot, zero_position)
        self.assertFalse(snapshot.position_valid)
        self.assertFalse(snapshot.has_valid_position)

    def test_vehicle_gps_time_is_not_replaced_with_gcs_clock(self) -> None:
        expected = datetime(2026, 7, 16, 12, 34, 56, 789000, tzinfo=UTC)
        message = _Message(
            message_type="SYSTEM_TIME",
            time_unix_usec=int(expected.timestamp() * 1_000_000),
        )
        snapshot = TelemetryDecoder().apply(TelemetrySnapshot.zero(), message)
        self.assertEqual(snapshot.uav_time, expected)
        self.assertIsNotNone(snapshot.uav_time_received_at)

        boot_relative = _Message(message_type="GPS_RAW_INT", time_usec=42_000_000, fix_type=3, satellites_visible=10)
        snapshot = TelemetryDecoder().apply(snapshot, boot_relative)
        self.assertEqual(snapshot.uav_time, expected)

    def test_vehicle_adapter_rejects_messages_from_another_mavlink_system(self) -> None:
        client = SimpleNamespace(
            endpoint=SimpleNamespace(uri="test"),
            connection=SimpleNamespace(target_system=7, target_component=1),
        )
        service = MavlinkVehicleService(client)  # type: ignore[arg-type]
        own_message = SimpleNamespace(get_srcSystem=lambda: 7)
        foreign_message = SimpleNamespace(get_srcSystem=lambda: 9)
        self.assertTrue(service._is_target_system_message(own_message))
        self.assertFalse(service._is_target_system_message(foreign_message))

        autopilot_heartbeat = SimpleNamespace(
            get_type=lambda: "HEARTBEAT",
            get_srcSystem=lambda: 7,
            get_srcComponent=lambda: 1,
        )
        companion_heartbeat = SimpleNamespace(
            get_type=lambda: "HEARTBEAT",
            get_srcSystem=lambda: 7,
            get_srcComponent=lambda: 191,
        )
        companion_attitude = SimpleNamespace(
            get_type=lambda: "ATTITUDE",
            get_srcSystem=lambda: 7,
            get_srcComponent=lambda: 191,
        )
        self.assertTrue(service._is_target_vehicle_message(autopilot_heartbeat))
        self.assertFalse(service._is_target_vehicle_message(companion_heartbeat))
        self.assertTrue(service._is_target_vehicle_message(companion_attitude))

        rf_status = SimpleNamespace(
            get_type=lambda: "RADIO_STATUS",
            get_srcSystem=lambda: 51,
            get_srcComponent=lambda: 68,
        )
        self.assertTrue(service._is_active_connection_message(rf_status))
        self.assertFalse(service._is_active_connection_message(foreign_message))

    def test_parameter_catalog_and_write_are_confirmed_by_vehicle_echo(self) -> None:
        vehicle = _FakeVehicle()
        service = VehicleParameterService(
            [Parameter("KNOWN", 1, "-", "test", "Known")],
            vehicle,  # type: ignore[arg-type]
        )
        published: list[tuple[Parameter, ...]] = []
        service.changed.subscribe(published.append)
        service.start()
        vehicle.publish(
            _Message(
                message_type="PARAM_VALUE",
                param_id=b"KNOWN\x00",
                param_value=3.0,
                param_count=2,
                param_index=0,
                param_type=6,
            )
        )
        vehicle.publish(
            _Message(
                message_type="PARAM_VALUE",
                param_id=b"NEW_PARAM\x00",
                param_value=4.5,
                param_count=2,
                param_index=1,
                param_type=9,
            )
        )
        values = {item.key: item.value for item in service.current}
        self.assertEqual(values, {"KNOWN": 3, "NEW_PARAM": 4.5})

        service.update_values({"KNOWN": 7})
        self.assertEqual(vehicle.parameter_writes, [("KNOWN", 7, 6)])
        self.assertEqual({item.key: item.value for item in service.current}["KNOWN"], 7)
        self.assertGreaterEqual(len(published), 2)
        service.stop()

    def test_incomplete_parameter_catalog_is_retried_and_reported(self) -> None:
        vehicle = _FakeVehicle()
        logs = LoggingService()
        service = VehicleParameterService(
            [],
            vehicle,  # type: ignore[arg-type]
            logs.child("PARAMETER"),
            write_timeout=0.03,
            max_refresh_attempts=2,
        )
        service.start()
        time.sleep(0.12)
        service.stop()
        self.assertEqual(vehicle.parameter_requests, 2)
        self.assertEqual(logs.history[-1].action, "parameter.catalog_failed")

    def test_application_specific_vehicle_command_can_be_injected(self) -> None:
        received: list[CommandRequest] = []
        client = SimpleNamespace(endpoint=SimpleNamespace(uri="test"))
        vehicle = MavlinkVehicleService(
            client,  # type: ignore[arg-type]
            custom_handlers={"TARGET_LOCK": received.append},
        )
        request = CommandRequest("target_lock", {"target_id": 17}, "test")
        vehicle.execute(request)
        self.assertEqual(received, [request])

    def test_unacknowledged_transport_is_logged_as_sent_not_confirmed(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def execute(self, _request) -> bool:
                return False

        logs = LoggingService()
        commands = DispatchCommandService(
            Vehicle(),  # type: ignore[arg-type]
            MutableParameterService(),
            logs.child("COMMAND"),
        )
        result = commands.dispatch(CommandRequest("GOTO", {"latitude": 1, "longitude": 2}))
        self.assertTrue(result.accepted)
        self.assertEqual(logs.history[-1].action, "command.sent")
        self.assertNotIn("doğrulandı", result.message)

    def test_force_arm_audit_details_are_preserved_by_command_service(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def execute(self, request) -> bool:
                self.request = request
                return True

        vehicle = Vehicle()
        logs = LoggingService()
        commands = DispatchCommandService(
            vehicle,  # type: ignore[arg-type]
            MutableParameterService(),
            logs.child("COMMAND"),
        )
        result = commands.dispatch(
            CommandRequest(
                "FORCE_ARM",
                {
                    "operator_confirmation": True,
                    "normal_arm_error": "Pre-arm kontrolü reddetti",
                },
            )
        )
        self.assertTrue(result.accepted)
        self.assertTrue(result.details["forced"])
        self.assertTrue(result.details["operator_confirmation"])
        self.assertEqual(result.details["normal_arm_error"], "Pre-arm kontrolü reddetti")
        self.assertIn("ZORLA ARM", result.message)
        self.assertTrue(logs.history[-1].details["forced"])

    def test_mission_upload_result_reports_vehicle_accepted_item_count(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def upload_mission(self, overlays) -> int:
                self.overlays = list(overlays)
                return len(self.overlays)

        vehicle = Vehicle()
        logs = LoggingService()
        commands = DispatchCommandService(
            vehicle,  # type: ignore[arg-type]
            MutableParameterService(),
            logs.child("COMMAND"),
        )
        result = commands.dispatch(
            CommandRequest(
                "MISSION_UPLOAD",
                {
                    "overlays": [
                        MapOverlay(
                            "wp-1",
                            "waypoint",
                            "WP 1",
                            [[39.1, 32.1]],
                            "#fff",
                            35,
                        ).to_dict()
                    ]
                },
            )
        )
        self.assertTrue(result.accepted)
        self.assertEqual(len(vehicle.overlays), 1)
        self.assertIn("1 görev öğesi", result.message)
        self.assertEqual(logs.history[-1].details["item_count"], 1)

    def test_auto_mode_changes_mode_without_uploading_the_mission(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def upload_mission(self, overlays) -> int:
                items = list(overlays)
                self.calls.append(("upload", items))
                return len(items)

            def execute(self, request) -> bool:
                self.calls.append(("execute", request))
                return True

        overlay = MapOverlay(
            "wp-1",
            "waypoint",
            "WP 1",
            [[39.1, 32.1, 100.0]],
            "#fff",
            35,
        )
        vehicle = Vehicle()
        hss = MapOverlay(
            "teknofest-hss-1",
            "hss",
            "HSS 1",
            [[39.2, 32.2]],
            "#f00",
            100,
            {"source": "teknofest-hss", "read_only": True, "mission_upload": False},
        )
        mission = SimpleNamespace(state=MissionState(overlays=[hss, overlay]))
        commands = DispatchCommandService(
            vehicle,  # type: ignore[arg-type]
            MutableParameterService(),
            mission=mission,  # type: ignore[arg-type]
        )

        result = commands.dispatch(CommandRequest("APPLY_MODE", {"mode": "AUTO"}))

        self.assertTrue(result.accepted)
        self.assertEqual([name for name, _value in vehicle.calls], ["execute"])
        forwarded = vehicle.calls[0][1]
        self.assertEqual(forwarded.code, "SET_MODE")
        self.assertEqual(forwarded.parameters, {"mode": "AUTO"})
        self.assertEqual(result.details, {"mode": "AUTO"})
        self.assertNotIn("mission_uploaded", result.details)
        self.assertEqual(result.message, "AUTO modu heartbeat ile doğrulandı")

    def test_unimplemented_autonomous_task_is_rejected_and_logged(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def execute(self, request) -> bool:
                raise NotImplementedError(f"Araç davranışı tanımlı değil: {request.code}")

        logs = LoggingService()
        commands = DispatchCommandService(
            Vehicle(),  # type: ignore[arg-type]
            MutableParameterService(),
            logs.child("COMMAND"),
        )
        result = commands.dispatch(
            CommandRequest("SET_MISSION_TASK", {"task": "TARGET_LOCK"})
        )
        self.assertFalse(result.accepted)
        self.assertIn("TARGET_LOCK", result.message)
        self.assertEqual(logs.history[-1].action, "command.rejected")

    def test_auto_land_task_uses_fixed_wing_autoland_mode(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def execute(self, request) -> bool:
                self.request = request
                return True

        vehicle = Vehicle()
        commands = DispatchCommandService(
            vehicle,  # type: ignore[arg-type]
            MutableParameterService(),
        )
        result = commands.dispatch(
            CommandRequest("SET_MISSION_TASK", {"task": "AUTO_LAND"})
        )
        self.assertTrue(result.accepted)
        self.assertEqual(vehicle.request.code, "SET_MODE")
        self.assertEqual(vehicle.request.parameters, {"mode": "AUTOLAND"})
        self.assertIn("AUTO_LAND", result.message)

    def test_profile_altitude_is_used_as_the_vehicle_mission_default(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def set_default_mission_altitude(self, altitude: float) -> None:
                self.default_altitude = altitude

        vehicle = Vehicle()
        parameters = MutableParameterService(
            [
                Parameter("AIRSPEED_CRUISE", 20.0, "m/s", "flight", ""),
                Parameter("WP_LOITER_RAD", 50.0, "m", "flight", ""),
            ]
        )
        commands = DispatchCommandService(
            vehicle,  # type: ignore[arg-type]
            parameters,
        )
        result = commands.dispatch(
            CommandRequest(
                "SET_TARGET_PROFILE",
                {"target_altitude": 140.0, "air_speed": 28.0, "loiter_radius": 75.0},
            )
        )
        self.assertTrue(result.accepted)
        self.assertEqual(vehicle.default_altitude, 140.0)
        self.assertEqual(
            {parameter.key: parameter.value for parameter in parameters.current},
            {"AIRSPEED_CRUISE": 28.0, "WP_LOITER_RAD": 75.0},
        )

    def test_mission_upload_completes_the_mavlink_request_ack_handshake(self) -> None:
        mavlink = SimpleNamespace(
            MAV_CMD_NAV_TAKEOFF=22,
            MAV_CMD_NAV_WAYPOINT=16,
            MAV_CMD_NAV_LOITER_UNLIM=17,
            MAV_CMD_NAV_RETURN_TO_LAUNCH=20,
            MAV_CMD_NAV_LAND=21,
            MAV_FRAME_GLOBAL_RELATIVE_ALT_INT=6,
            MAV_MISSION_TYPE_MISSION=0,
            MAV_MISSION_ACCEPTED=0,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(target_system=1, target_component=1)
            router = SimpleNamespace(sequence=0)
            is_connected = True

            def __init__(self) -> None:
                self.mavlink = mavlink
                self.sent: list[tuple[str, tuple]] = []
                self.responses = [
                    SimpleNamespace(seq=0),
                    SimpleNamespace(type=mavlink.MAV_MISSION_ACCEPTED),
                ]

            def call_mav(self, method: str, *args) -> None:
                self.sent.append((method, args))

            def wait_for(self, *_args, **_kwargs):
                return self.responses.pop(0)

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 1, 1)
        count = vehicle.upload_mission(
            [MapOverlay("wp", "waypoint", "WP", [[39.1, 32.1]], "#fff", 35)]
        )
        self.assertEqual(count, 1)
        self.assertEqual([call[0] for call in client.sent], ["mission_count_send", "mission_item_int_send"])
        self.assertEqual(client.sent[0][1][2], 1)
        self.assertEqual(client.sent[1][1][2], 0)

    def test_mission_conversion_sorts_route_items_and_retains_areas_locally(self) -> None:
        mavlink = SimpleNamespace(
            MAV_CMD_NAV_TAKEOFF=22,
            MAV_CMD_NAV_WAYPOINT=16,
            MAV_CMD_NAV_LOITER_UNLIM=17,
            MAV_CMD_NAV_RETURN_TO_LAUNCH=20,
            MAV_CMD_NAV_LAND=21,
            MAV_FRAME_GLOBAL_RELATIVE_ALT_INT=6,
        )
        items, ignored = MavlinkVehicleService._mission_items(
            [
                MapOverlay("safe", "safe_zone", "Safe", [[39.0, 32.0]], "#fff"),
                MapOverlay("land", "land", "Land", [[39.2, 32.2]], "#fff", metadata={"route_order": 2}),
                MapOverlay("wp", "waypoint", "WP", [[39.1, 32.1, 100]], "#fff", 45, {"route_order": 1}),
            ],
            mavlink,
        )
        self.assertEqual([item[1] for item in items], [16, 21])
        self.assertEqual(items[0][8:10], (391_000_000, 321_000_000))
        self.assertEqual(items[0][5:7], (45.0, 0.0))
        self.assertEqual(items[1][10], 0.0)
        self.assertTrue(math.isnan(items[0][7]))
        self.assertEqual(ignored, ["safe_zone"])

    def test_gstreamer_pipeline_produces_backend_video_frames(self) -> None:
        service = GStreamerVideoService(
            "pipeline",
            "videotestsrc num-buffers=1",
        )
        received = []
        ready = threading.Event()
        service.frames.subscribe(lambda frame: (received.append(frame), ready.set()))
        service.start()
        self.assertTrue(ready.wait(3.0))
        service.stop()
        self.assertEqual(received[0].pixel_format, "rgba8888")
        self.assertGreater(received[0].width, 0)

    def test_gstreamer_rtsp_rtp_and_v4l2_plugins_are_available(self) -> None:
        service = GStreamerVideoService("usb", "/dev/video0")
        Gst = service._load_gstreamer()
        for element in (
            "rtspsrc",
            "rtph264depay",
            "rtph265depay",
            "v4l2src",
            "appsink",
        ):
            self.assertIsNotNone(Gst.ElementFactory.find(element), element)

    def test_video_link_drops_when_real_frames_stop_arriving(self) -> None:
        logs = LoggingService()
        service = GStreamerVideoService(
            "udp",
            "5601",
            logs.child("VIDEO"),
            frame_timeout_s=0.1,
        )
        states: list[bool] = []
        service.connection_changed.subscribe(states.append)
        service._last_frame_at = 10.0
        service._set_connected(True)
        service._check_frame_timeout(now=10.2)
        self.assertFalse(service.connected)
        self.assertEqual(states, [True, False])
        self.assertEqual(logs.history[-1].action, "video.frame_timeout")

    def test_structured_logs_are_persisted_outside_ui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            service = LoggingService(log_path=path)
            service.event(
                "VEHICLE",
                "vehicle.connected",
                "Connected",
                severity="success",
                details={"system_id": 1},
                device_id="mavlink:1:1",
            )
            payload = path.read_text(encoding="utf-8")
            self.assertIn('"action":"vehicle.connected"', payload)
            self.assertIn('"device_id":"mavlink:1:1"', payload)


if __name__ == "__main__":
    unittest.main()
