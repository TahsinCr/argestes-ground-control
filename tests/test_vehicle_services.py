from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.core.events import EventBus, Subscription
from src.models.commands import CommandRequest, CommandResult
from src.models.mission import MapOverlay
from src.models.parameters import Parameter
from src.models.settings import BatteryTelemetrySettings
from src.models.telemetry import TelemetrySnapshot
from src.models.vehicle import (
    VehicleCapabilities,
    VehicleConnectionState,
    VehicleTransportStats,
)
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from extensions.mavlink.commands import DispatchCommandService
from extensions.parameters.mutable import MutableParameterService
from extensions.parameters.protocols.mavlink import VehicleParameterService
from extensions.parameters.protocols.mavlink_defaults import decode_parameter_defaults
from src.extension.api.parameters import ParameterService
from extensions.battery.estimation import VoltageBatteryEstimator
from extensions.battery.processor import BatteryProcessor
from extensions.mavlink.telemetry import TelemetryDecoder, VehicleTelemetryService
from src.services.abstracts.vehicle import MissionTransferInterrupted
from extensions.mavlink.vehicle import MavlinkVehicleService
from src.ui.i18n import localized_log_message


class _Message(SimpleNamespace):
    def get_type(self) -> str:
        return self.message_type


class _FakeVehicle:
    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.state = VehicleConnectionState(connected, "test", 1, 1)
        self.connection_changed = EventBus[VehicleConnectionState]()
        self.subscriptions: dict[str, list] = {}
        self.parameter_requests = 0
        self.parameter_writes: list[tuple[str, object, int | None]] = []
        self.transport_stats = VehicleTransportStats(
            received_packets=0,
            sent_packets=0,
            delivery_quality=100,
        )

    def subscribe(self, message_types, callback):
        types = (
            (message_types,) if isinstance(message_types, str) else tuple(message_types)
        )
        for message_type in types:
            self.subscriptions.setdefault(message_type, []).append(callback)
        return Subscription(0, lambda: None)

    def request_parameters(self) -> None:
        self.parameter_requests += 1

    def set_parameter(
        self, name: str, value: object, value_type: int | None = None
    ) -> None:
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
            vehicle,  # type: ignore[arg-type]
        )
        telemetry.start()
        parameters.start()
        self.assertIn("HEARTBEAT", vehicle.subscriptions)
        self.assertIn("LOCAL_POSITION_NED", vehicle.subscriptions)
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
        self.assertIsNotNone(snapshot.global_position_received_at)

        snapshot = TelemetryDecoder().apply(
            snapshot,
            _Message(
                message_type="LOCAL_POSITION_NED",
                x=145.25,
                y=-32.5,
                z=-114.6,
            ),
        )
        self.assertAlmostEqual(snapshot.local_north_m or 0.0, 145.25)
        self.assertAlmostEqual(snapshot.local_east_m or 0.0, -32.5)
        self.assertAlmostEqual(snapshot.local_down_m or 0.0, -114.6)

    def test_vehicle_requests_a_safe_fast_telemetry_profile(self) -> None:
        message_types = tuple(MavlinkVehicleService.DEFAULT_STREAM_RATES_HZ)
        mavlink = SimpleNamespace(
            **{
                f"MAVLINK_MSG_ID_{message_type}": index + 1
                for index, message_type in enumerate(message_types)
            }
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(
                target_system=7,
                target_component=1,
                message_state={"HEARTBEAT": SimpleNamespace(base_mode=0)},
            )
            is_connected = True

            def __init__(self) -> None:
                self.mavlink = mavlink
                self.requests: list[tuple[int, float, int, int]] = []

            def request_message_rate(
                self,
                message_id,
                frequency_hz,
                *,
                target_system,
                target_component,
            ) -> int:
                self.requests.append(
                    (
                        int(message_id),
                        float(frequency_hz),
                        int(target_system),
                        int(target_component),
                    )
                )
                return round(1_000_000 / float(frequency_hz))

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 7, 1)
        vehicle._last_heartbeat = time.monotonic()
        vehicle._request_telemetry_streams()

        requested_rates = {
            message_types[message_id - 1]: rate
            for message_id, rate, system, component in client.requests
            if system == 7 and component == 1
        }
        self.assertEqual(
            requested_rates,
            MavlinkVehicleService.DEFAULT_STREAM_RATES_HZ,
        )
        self.assertEqual(requested_rates["ATTITUDE"], 20.0)
        self.assertLessEqual(sum(requested_rates.values()), 80.0)

    def test_vehicle_sends_standard_gcs_heartbeat_for_radio_status(self) -> None:
        mavlink = SimpleNamespace(
            MAV_TYPE_GCS=6,
            MAV_AUTOPILOT_INVALID=8,
            MAV_STATE_ACTIVE=4,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")

            def __init__(self) -> None:
                self.mavlink = mavlink
                self.sent: list[tuple[str, tuple[object, ...]]] = []

            def call_mav(self, method: str, *args: object) -> None:
                self.sent.append((method, args))

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]

        vehicle._send_gcs_heartbeat()

        self.assertEqual(
            client.sent,
            [("heartbeat_send", (6, 8, 0, 0, 4))],
        )

    def test_vehicle_gcs_heartbeat_repeats_until_watchdog_stops(self) -> None:
        mavlink = SimpleNamespace(
            MAV_TYPE_GCS=6,
            MAV_AUTOPILOT_INVALID=8,
            MAV_STATE_ACTIVE=4,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test", heartbeat_timeout=0.2)

            def __init__(self) -> None:
                self.mavlink = mavlink
                self.sent: list[tuple[str, tuple[object, ...]]] = []

            def call_mav(self, method: str, *args: object) -> None:
                self.sent.append((method, args))

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle.GCS_HEARTBEAT_INTERVAL_S = 0.02
        worker = threading.Thread(target=vehicle._watch_heartbeat)
        worker.start()
        deadline = time.monotonic() + 0.5
        while len(client.sent) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)

        vehicle._watchdog_stop.set()
        worker.join(timeout=0.5)
        sent_at_stop = len(client.sent)
        time.sleep(0.03)

        self.assertGreaterEqual(sent_at_stop, 2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(client.sent), sent_at_stop)

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
            _Message(
                message_type="HOME_POSITION", latitude=399000000, longitude=328000000
            ),
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
        self.assertIsNotNone(snapshot.hud_received_at)
        self.assertEqual(snapshot.throttle_percent, 63)
        self.assertEqual(snapshot.rc_rssi, 200)
        self.assertTrue(snapshot.ekf_ok)
        self.assertGreater(snapshot.home_distance, 0)
        self.assertGreaterEqual(snapshot.home_heading, 0)

    def test_telemetry_decoder_normalizes_ardupilot_mission_home_sequence(self) -> None:
        decoder = TelemetryDecoder()
        snapshot = decoder.apply(
            TelemetrySnapshot.zero(),
            _Message(message_type="MISSION_COUNT", count=6),
        )
        snapshot = decoder.apply(
            snapshot,
            _Message(message_type="MISSION_CURRENT", seq=1, total=5),
        )

        self.assertEqual(snapshot.mission_index, 1)
        self.assertEqual(snapshot.mission_total, 5)

    def test_telemetry_decoder_exposes_battery_baro_and_body_axis_acceleration(
        self,
    ) -> None:
        decoder = TelemetryDecoder()
        snapshot = decoder.apply(
            TelemetrySnapshot.zero(),
            _Message(
                message_type="BATTERY_STATUS",
                battery_remaining=74,
                current_battery=835,
                current_consumed=1460,
                voltages=[4100, 4090, 4080, 65_535],
            ),
        )
        snapshot = decoder.apply(
            snapshot,
            _Message(
                message_type="SCALED_PRESSURE",
                press_abs=898.25,
                press_diff=5.5,
                temperature=2435,
            ),
        )
        snapshot = decoder.apply(
            snapshot,
            _Message(
                message_type="SCALED_IMU",
                xacc=100,
                yacc=-200,
                zacc=1000,
            ),
        )

        self.assertEqual(snapshot.battery_percent, 74)
        self.assertAlmostEqual(snapshot.battery_voltage, 12.27)
        self.assertAlmostEqual(snapshot.battery_current_amps or 0.0, 8.35)
        self.assertEqual(snapshot.battery_consumed_mah, 1460)
        self.assertAlmostEqual(snapshot.barometric_pressure_hpa or 0.0, 898.25)
        self.assertAlmostEqual(snapshot.differential_pressure_hpa or 0.0, 5.5)
        self.assertAlmostEqual(snapshot.barometric_temperature_c or 0.0, 24.35)
        self.assertAlmostEqual(snapshot.acceleration_x_mps2 or 0.0, 0.980665)
        self.assertAlmostEqual(snapshot.acceleration_y_mps2 or 0.0, -1.96133)
        self.assertAlmostEqual(snapshot.acceleration_z_mps2 or 0.0, 9.80665)
        self.assertIsNotNone(snapshot.load_factor_g)

    def test_voltage_only_battery_uses_filtered_voltage_and_preserves_current(
        self,
    ) -> None:
        decoder = TelemetryDecoder()
        snapshot = TelemetrySnapshot.zero().copy(
            battery_current_amps=42.0,
            battery_consumed_mah=900,
        )

        snapshot = decoder.apply(
            snapshot,
            _Message(
                message_type="SYS_STATUS",
                load=300,
                battery_remaining=88,
                voltage_battery=14_800,
                current_battery=2_400,
            ),
        )

        snapshot = BatteryProcessor(BatteryTelemetrySettings())(snapshot)
        self.assertEqual(snapshot.battery_percent, 10)
        self.assertAlmostEqual(snapshot.battery_voltage, 14.8)
        self.assertAlmostEqual(snapshot.battery_current_amps or 0.0, 24.0)
        self.assertIsNone(snapshot.battery_consumed_mah)

    def test_voltage_estimator_rejects_transient_motor_sag(self) -> None:
        settings = BatteryTelemetrySettings()
        estimator = VoltageBatteryEstimator(settings)
        initial = estimator.update(16.2, sample_at=0.0)
        self.assertIsNotNone(initial)

        # One severe sample cannot move a seven-sample rolling median.
        sag = estimator.update(14.2, sample_at=0.5)
        self.assertIsNotNone(sag)
        self.assertAlmostEqual(sag.filtered_voltage, 16.2)

        # A sustained lower voltage is followed gradually, never as an
        # instantaneous percentage collapse.
        sustained = sag
        for index in range(2, 12):
            sustained = estimator.update(14.8, sample_at=index * 0.5)
        self.assertIsNotNone(sustained)
        self.assertGreater(sustained.filtered_voltage, 14.8)
        self.assertLess(sustained.filtered_voltage, 16.2)

        before_recovery = sustained.filtered_voltage
        for index in range(12, 20):
            recovered = estimator.update(16.2, sample_at=index * 0.5)
        self.assertGreater(recovered.filtered_voltage, before_recovery)

    def test_vehicle_telemetry_applies_changed_battery_profile_without_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            vehicle = _FakeVehicle()
            telemetry = VehicleTelemetryService(
                vehicle,  # type: ignore[arg-type]
            )
            from src.services.telemetry import MutableTelemetryService

            hub = MutableTelemetryService()
            processor = BatteryProcessor(settings.current.backend.battery_telemetry)
            hub.add_processor("battery", processor)
            profile_subscription = settings.changed.subscribe(
                lambda model: processor.configure(model.backend.battery_telemetry)
            )
            feed_subscription = telemetry.updated.subscribe(hub.push)
            telemetry.start()
            vehicle.publish(
                _Message(
                    message_type="SYS_STATUS",
                    load=200,
                    battery_remaining=90,
                    voltage_battery=14_800,
                    current_battery=1_500,
                )
            )
            self.assertEqual(hub.current.battery_percent, 10)

            changed = settings.current.with_value(
                "backend.battery_telemetry.reserve_percent",
                20.0,
            )
            settings.save(changed)
            vehicle.publish(
                _Message(
                    message_type="SYS_STATUS",
                    load=200,
                    battery_remaining=90,
                    voltage_battery=14_800,
                    current_battery=1_500,
                )
            )

            self.assertEqual(hub.current.battery_percent, 20)
            self.assertAlmostEqual(hub.current.battery_current_amps or 0.0, 15.0)
            telemetry.stop()
            profile_subscription.cancel()
            feed_subscription.cancel()
            hub.remove_processor("battery")
            self.assertEqual(hub.current.battery_percent, 90)

    def test_radio_status_uses_both_rf_margins_and_tx_buffer_for_link_health(
        self,
    ) -> None:
        snapshot = TelemetryDecoder().apply(
            TelemetrySnapshot.zero(),
            _Message(
                message_type="RADIO_STATUS",
                rssi=150,
                noise=100,
                remrssi=135,
                remnoise=100,
                txbuf=91,
                rxerrors=7,
                fixed=3,
            ),
        )
        self.assertEqual(snapshot.link_quality_source, "rf")
        self.assertGreaterEqual(snapshot.link_quality, 50)
        self.assertLessEqual(snapshot.link_quality, 65)
        self.assertEqual(snapshot.radio_local_rssi, 150)
        self.assertEqual(snapshot.radio_remote_rssi, 135)
        self.assertEqual(snapshot.radio_rx_errors, 7)
        self.assertEqual(snapshot.radio_fixed_packets, 3)
        self.assertIsNotNone(snapshot.link_quality_received_at)
        self.assertTrue(snapshot.has_fresh_link_quality(5))

    def test_vehicle_telemetry_uses_packet_delivery_when_radio_status_is_absent(
        self,
    ) -> None:
        vehicle = _FakeVehicle()
        vehicle.transport_stats = VehicleTransportStats(
            received_packets=128,
            sent_packets=7,
            estimated_dropped_packets=4,
            delivery_quality=97,
            round_trip_ms=83.5,
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
        self.assertEqual(snapshot.packet_delivery_percent, 97)
        self.assertEqual(snapshot.estimated_dropped_packets, 4)
        self.assertEqual(snapshot.packets_in, 128)
        self.assertEqual(snapshot.packets_out, 7)
        self.assertEqual(snapshot.telemetry_round_trip_ms, 83.5)

    def test_vehicle_link_ping_measures_correlated_round_trip_time(self) -> None:
        sent: list[tuple[str, tuple[object, ...]]] = []
        router_stats = SimpleNamespace(
            received_messages=10,
            estimated_dropped_messages=0,
            delivery_quality=100,
        )
        connection = SimpleNamespace(
            target_system=1,
            target_component=1,
            sent_messages=2,
        )
        client = SimpleNamespace(
            endpoint=SimpleNamespace(uri="test"),
            connection=connection,
            router=SimpleNamespace(stats=router_stats),
            is_connected=True,
            call_mav=lambda name, *args: sent.append((name, args)),
        )
        service = MavlinkVehicleService(client)  # type: ignore[arg-type]
        service._state = VehicleConnectionState(True, "test", 1, 1)

        sent_at = time.monotonic()
        sequence = service._send_link_ping(now=sent_at)
        time_usec = service._pending_pings[sequence][0]
        service._on_ping(
            _Message(message_type="PING", seq=sequence, time_usec=time_usec),
            received_at=sent_at + 0.084,
        )

        self.assertEqual(sent[0][0], "ping_send")
        self.assertEqual(sent[0][1][1:], (sequence, 1, 1))
        self.assertAlmostEqual(service.transport_stats.round_trip_ms or 0.0, 84.0)

    def test_vehicle_link_ping_ignores_uncorrelated_response(self) -> None:
        client = SimpleNamespace(
            endpoint=SimpleNamespace(uri="test"),
            connection=SimpleNamespace(target_system=1, target_component=1),
            is_connected=True,
        )
        service = MavlinkVehicleService(client)  # type: ignore[arg-type]
        service._on_ping(
            _Message(message_type="PING", seq=99, time_usec=123),
            received_at=5.0,
        )
        self.assertIsNone(service._round_trip_ms)

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

    def test_gps_freshness_uses_its_own_message_timestamp_and_sentinels(self) -> None:
        decoder = TelemetryDecoder()
        snapshot = decoder.apply(
            TelemetrySnapshot.zero(),
            _Message(
                message_type="GPS_RAW_INT",
                fix_type=3,
                satellites_visible=14,
                eph=90,
                time_usec=0,
            ),
        )
        snapshot = decoder.apply(
            snapshot,
            _Message(
                message_type="GLOBAL_POSITION_INT",
                lat=399000000,
                lon=328000000,
                alt=100_000,
                relative_alt=50_000,
                vx=0,
                vy=0,
                vz=0,
                hdg=65_535,
            ),
        )
        self.assertTrue(snapshot.has_fresh_gps(2.0))
        self.assertEqual(snapshot.heading, 0.0)

        stale = snapshot.copy(
            gps_received_at=datetime.now(UTC) - timedelta(seconds=5),
            position_received_at=datetime.now(UTC),
        )
        self.assertFalse(stale.has_fresh_gps(2.0))

        unknown = decoder.apply(
            stale,
            _Message(
                message_type="GPS_RAW_INT",
                fix_type=1,
                satellites_visible=255,
                eph=65_535,
                time_usec=0,
            ),
        )
        self.assertIsNone(unknown.gps_satellites)
        self.assertFalse(unknown.has_fresh_gps(2.0))

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

            def wait_for(self, message_type, **kwargs):
                self.waited_for.append(message_type)
                if message_type == "COMMAND_ACK":
                    predicate = kwargs.get("predicate")
                    foreign = SimpleNamespace(
                        command=400,
                        result=0,
                        get_srcSystem=lambda: 9,
                        get_srcComponent=lambda: 1,
                    )
                    companion = SimpleNamespace(
                        command=400,
                        result=0,
                        get_srcSystem=lambda: 1,
                        get_srcComponent=lambda: 191,
                    )
                    acknowledgement = SimpleNamespace(
                        command=400,
                        result=0,
                        get_srcSystem=lambda: 1,
                        get_srcComponent=lambda: 1,
                    )
                    if predicate(foreign) or predicate(companion):
                        raise AssertionError("foreign COMMAND_ACK matched")
                    if not predicate(acknowledgement):
                        raise AssertionError("target COMMAND_ACK did not match")
                    return acknowledgement
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

        boot_relative = _Message(
            message_type="GPS_RAW_INT",
            time_usec=42_000_000,
            fix_type=3,
            satellites_visible=10,
        )
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

    def test_optional_parameter_sync_status_supports_slotted_legacy_provider(self) -> None:
        class LegacyParameterService(ParameterService):
            __slots__ = ()

            @property
            def current(self):
                return ()

            @property
            def changed(self):
                return EventBus()

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def replace(self, _parameters) -> None:
                pass

            def upsert(self, _parameter) -> None:
                pass

            def update_values(self, _changes) -> None:
                pass

            def refresh(self) -> None:
                pass

        service = LegacyParameterService()

        self.assertEqual(service.sync_status.state.value, "idle")
        self.assertIs(service.sync_status_changed, service.sync_status_changed)

    def test_parameter_list_and_write_are_confirmed_by_vehicle_echo(self) -> None:
        vehicle = _FakeVehicle()
        service = VehicleParameterService(
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
        self.assertEqual(service.sync_status.state.value, "complete")
        self.assertEqual(service.sync_status.received_count, 2)
        self.assertEqual(service.sync_status.expected_count, 2)

        service.update_values({"KNOWN": 7})
        self.assertEqual(vehicle.parameter_writes, [("KNOWN", 7, 6)])
        self.assertEqual({item.key: item.value for item in service.current}["KNOWN"], 7)
        self.assertGreaterEqual(len(published), 2)
        service.stop()

    def test_parameter_echo_matches_float32_wire_value_without_relative_tolerance(self) -> None:
        service = VehicleParameterService(_FakeVehicle())  # type: ignore[arg-type]
        large_write = SimpleNamespace(
            expected=1_000_000.0,
            value_type=6,
            event=threading.Event(),
            confirmed=False,
        )
        service._pending_writes["LARGE"] = large_write
        service._consume_message(
            SimpleNamespace(
                get_type=lambda: "PARAM_VALUE",
                param_id=b"LARGE\x00",
                param_value=1_000_009.0,
                param_count=1,
                param_index=0,
                param_type=6,
            )
        )
        self.assertFalse(large_write.confirmed)
        self.assertFalse(large_write.event.is_set())

        expected_float = VehicleParameterService._wire_float32(0.1)
        float_write = SimpleNamespace(
            expected=expected_float,
            value_type=9,
            event=threading.Event(),
            confirmed=False,
        )
        service._pending_writes["FLOAT"] = float_write
        service._consume_message(
            SimpleNamespace(
                get_type=lambda: "PARAM_VALUE",
                param_id=b"FLOAT\x00",
                param_value=expected_float,
                param_count=1,
                param_index=0,
                param_type=9,
            )
        )
        self.assertTrue(float_write.confirmed)
        self.assertTrue(float_write.event.is_set())

    def test_parameter_write_rejects_integer_lost_by_float32_cast(self) -> None:
        vehicle = _FakeVehicle()
        service = VehicleParameterService(vehicle)  # type: ignore[arg-type]
        service.replace(
            [Parameter(key="LARGE_INT", value=1, unit="-", category="test", description="", value_type=6)]
        )

        with self.assertRaisesRegex(ValueError, "tam olarak temsil edilemiyor"):
            service.update_values({"LARGE_INT": 16_777_217})

        self.assertEqual(vehicle.parameter_writes, [])

    def test_parameter_values_from_other_mavlink_component_are_ignored(self) -> None:
        service = VehicleParameterService(_FakeVehicle())  # type: ignore[arg-type]

        for source_system, source_component in ((2, 1), (1, 2)):
            service._consume_message(
                SimpleNamespace(
                    get_type=lambda: "PARAM_VALUE",
                    get_srcSystem=lambda value=source_system: value,
                    get_srcComponent=lambda value=source_component: value,
                    param_id=b"FOREIGN\x00",
                    param_value=42.0,
                    param_count=1,
                    param_index=0,
                    param_type=6,
                )
            )

        self.assertEqual(service.current, ())

    def test_partial_parameter_write_reports_confirmed_uncertain_and_unattempted(self) -> None:
        class _PartialVehicle(_FakeVehicle):
            def set_parameter(
                self, name: str, value: object, value_type: int | None = None
            ) -> None:
                if name == "SECOND":
                    self.parameter_writes.append((name, value, value_type))
                    raise OSError("transport closed while sending")
                super().set_parameter(name, value, value_type)

        vehicle = _PartialVehicle()
        service = VehicleParameterService(vehicle, write_timeout=0.02)  # type: ignore[arg-type]
        service.start()
        service.replace(
            [
                Parameter(key=key, value=1.0, unit="-", category="test", description="")
                for key in ("FIRST", "SECOND", "THIRD")
            ]
        )

        with self.assertRaises(RuntimeError) as caught:
            service.update_values({"FIRST": 2.0, "SECOND": 3.0, "THIRD": 4.0})

        error = caught.exception
        self.assertEqual(error.confirmed_values, {"FIRST": 2.0})
        self.assertEqual(error.uncertain_keys, ("SECOND",))
        self.assertEqual(error.unattempted_keys, ("THIRD",))
        self.assertEqual([item[0] for item in vehicle.parameter_writes], ["FIRST", "SECOND"])
        service.stop()

    def test_parameter_writer_rejects_non_finite_and_unrepresentable_values(self) -> None:
        for value in (float("nan"), float("inf"), 10**10000):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises((TypeError, ValueError, OverflowError)):
                    VehicleParameterService._numeric_value("LIMIT", value)

    def test_parameter_list_is_cleared_when_vehicle_disconnects(self) -> None:
        vehicle = _FakeVehicle()
        service = VehicleParameterService(vehicle)  # type: ignore[arg-type]
        service.start()
        vehicle.publish(
            _Message(
                message_type="PARAM_VALUE",
                param_id=b"ARSPD_FBW_MIN\x00",
                param_value=17.0,
                param_count=1,
                param_index=0,
                param_type=9,
            )
        )
        self.assertEqual([item.key for item in service.current], ["ARSPD_FBW_MIN"])

        vehicle.connected = False
        vehicle.state = VehicleConnectionState(False, "test", 1, 1)
        vehicle.connection_changed.publish(vehicle.state)

        self.assertEqual(service.current, ())
        self.assertEqual(service.sync_status.state.value, "disconnected")
        service.stop()

    def test_incomplete_parameter_list_is_retried_and_reported(self) -> None:
        vehicle = _FakeVehicle()
        logs = LoggingService()
        service = VehicleParameterService(
            vehicle,  # type: ignore[arg-type]
            logs.child("PARAMETER"),
            write_timeout=0.03,
            max_refresh_attempts=2,
        )
        service.start()
        time.sleep(0.12)
        status = service.sync_status
        service.stop()
        self.assertEqual(status.state.value, "failed")
        self.assertEqual(vehicle.parameter_requests, 2)
        self.assertEqual(logs.history[-1].action, "parameter.sync_failed")

    def test_parameter_sync_timeout_tracks_stream_progress(self) -> None:
        vehicle = _FakeVehicle()
        service = VehicleParameterService(
            vehicle,  # type: ignore[arg-type]
            write_timeout=1.0,
            max_refresh_attempts=2,
        )
        service.start()
        vehicle.publish(
            _Message(
                message_type="PARAM_VALUE",
                param_id=b"FIRST\x00",
                param_value=1.0,
                param_count=3,
                param_index=0,
                param_type=9,
            )
        )

        service._check_refresh_timeout()

        status = service.sync_status
        still_loading = service._loading
        refresh_attempt = service._refresh_attempt
        service.stop()
        self.assertEqual(vehicle.parameter_requests, 1)
        self.assertTrue(still_loading)
        self.assertEqual(refresh_attempt, 1)
        self.assertEqual(status.state.value, "partial")
        self.assertEqual(status.received_count, 1)
        self.assertEqual(status.expected_count, 3)

    def test_parameter_catalogue_is_published_in_streaming_batches(self) -> None:
        vehicle = _FakeVehicle()
        service = VehicleParameterService(vehicle)  # type: ignore[arg-type]
        service.PROGRESS_BATCH_SIZE = 2
        service.start()

        for index in range(2):
            vehicle.publish(
                _Message(
                    message_type="PARAM_VALUE",
                    param_id=f"PARAM_{index}".encode("ascii"),
                    param_value=float(index),
                    param_count=3,
                    param_index=index,
                    param_type=9,
                )
            )

        self.assertTrue(service._loading)
        self.assertEqual(len(service.current), 2)
        vehicle.publish(
            _Message(
                message_type="PARAM_VALUE",
                param_id=b"PARAM_2",
                param_value=2.0,
                param_count=3,
                param_index=2,
                param_type=9,
            )
        )
        self.assertFalse(service._loading)
        self.assertEqual(len(service.current), 3)
        service.stop()

    def test_parameter_defaults_enrich_completed_vehicle_catalogue(self) -> None:
        vehicle = _FakeVehicle()
        loaded = threading.Event()

        def load_defaults() -> dict[str, object]:
            loaded.set()
            return {"ARSPD_FBW_MIN": 12.0}

        service = VehicleParameterService(
            vehicle,  # type: ignore[arg-type]
            default_loader=load_defaults,
        )
        service.start()
        vehicle.publish(
            _Message(
                message_type="PARAM_VALUE",
                param_id=b"ARSPD_FBW_MIN",
                param_value=17.0,
                param_count=1,
                param_index=0,
                param_type=9,
            )
        )

        self.assertTrue(loaded.wait(1.0))
        deadline = time.monotonic() + 1.0
        while service.current[0].default_value is None and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(service.current[0].default_value, 12.0)
        service.stop()

    def test_packed_parameter_defaults_decoder_uses_compiled_defaults(self) -> None:
        import struct

        packed = bytearray(struct.pack("<HHH", 0x671C, 2, 2))
        packed.extend(struct.pack("<BB", 0x14, 0x20))
        packed.extend(b"FOO")
        packed.extend(struct.pack("<ff", 17.0, 12.0))
        packed.extend(struct.pack("<BB", 0x04, 0x23))
        packed.extend(b"BAR")
        packed.extend(struct.pack("<f", 8.0))

        defaults = decode_parameter_defaults(bytes(packed))

        self.assertEqual(defaults["FOO"], 12.0)
        self.assertEqual(defaults["FOOBAR"], 8.0)

    def test_identical_parameter_echo_does_not_republish_catalogue(self) -> None:
        parameter = Parameter("UNCHANGED", 4.0, "-", "vehicle", "")
        service = MutableParameterService([parameter])
        published: list[tuple[Parameter, ...]] = []
        service.changed.subscribe(published.append)

        service.upsert(parameter)

        self.assertEqual(published, [])

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

    def test_operator_commands_are_serialized_on_the_shared_vehicle_link(self) -> None:
        class SlowVehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def __init__(self) -> None:
                self.active = 0
                self.maximum_active = 0
                self.lock = threading.Lock()

            def execute(self, _request: CommandRequest) -> bool:
                with self.lock:
                    self.active += 1
                    self.maximum_active = max(self.maximum_active, self.active)
                time.sleep(0.03)
                with self.lock:
                    self.active -= 1
                return True

        vehicle = SlowVehicle()
        commands = DispatchCommandService(
            vehicle,  # type: ignore[arg-type]
            MutableParameterService(),
        )
        workers = [
            threading.Thread(
                target=commands.dispatch,
                args=(CommandRequest("ARM" if index % 2 == 0 else "DISARM"),),
            )
            for index in range(4)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=1.0)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(vehicle.maximum_active, 1)

    def test_queued_navigation_commands_keep_only_the_newest_target(self) -> None:
        first_started = threading.Event()
        release_first = threading.Event()

        class SlowVehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def __init__(self) -> None:
                self.targets: list[float] = []

            def execute(self, request: CommandRequest) -> bool:
                self.targets.append(float(request.parameters["latitude"]))
                if len(self.targets) == 1:
                    first_started.set()
                    release_first.wait(timeout=1.0)
                return True

        vehicle = SlowVehicle()
        commands = DispatchCommandService(
            vehicle,  # type: ignore[arg-type]
            MutableParameterService(),
        )
        results: list[CommandResult] = []

        def send(latitude: float) -> None:
            results.append(
                commands.dispatch(
                    CommandRequest("GOTO", {"latitude": latitude, "longitude": 32.0})
                )
            )

        workers = [
            threading.Thread(target=send, args=(value,)) for value in (1.0, 2.0, 3.0)
        ]
        workers[0].start()
        self.assertTrue(first_started.wait(timeout=1.0))
        workers[1].start()
        workers[2].start()
        deadline = time.monotonic() + 1.0
        while commands._navigation_revision < 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        release_first.set()
        for worker in workers:
            worker.join(timeout=1.0)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(vehicle.targets, [1.0, 3.0])
        superseded = [
            result
            for result in results
            if result.details.get("navigation") == "superseded"
        ]
        self.assertEqual(len(superseded), 1)
        self.assertFalse(superseded[0].accepted)
        self.assertEqual(
            sum(result.details.get("navigation") == "superseded" for result in results),
            1,
        )

    def test_home_reference_is_sent_with_acknowledged_mavlink_command(self) -> None:
        mavlink = SimpleNamespace(
            MAV_CMD_DO_SET_HOME=179,
            MAV_RESULT_IN_PROGRESS=5,
            MAV_RESULT_ACCEPTED=0,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(
                target_system=7,
                target_component=1,
                message_state={"HEARTBEAT": SimpleNamespace(base_mode=0)},
            )
            router = SimpleNamespace(sequence=12)
            is_connected = True

            def __init__(self) -> None:
                self.mavlink = mavlink
                self.sent: list[tuple[str, tuple]] = []

            def call_mav(self, method: str, *args) -> None:
                self.sent.append((method, args))

            def wait_for(self, *_args, **_kwargs):
                return SimpleNamespace(command=179, result=0)

            def latest(self, _message_type):
                return None

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 7, 1)
        vehicle._last_heartbeat = time.monotonic()

        confirmed = vehicle.execute(
            CommandRequest(
                "SET_HOME",
                {
                    "latitude": 39.925533,
                    "longitude": 32.836974,
                    "altitude": 938.4,
                },
            )
        )

        self.assertTrue(confirmed)
        self.assertEqual(client.sent[0][0], "command_long_send")
        arguments = client.sent[0][1]
        self.assertEqual(arguments[:4], (7, 1, 179, 0))
        self.assertEqual(arguments[4:8], (0.0, 0.0, 0.0, 0.0))
        self.assertAlmostEqual(arguments[8], 39.925533)
        self.assertAlmostEqual(arguments[9], 32.836974)
        self.assertAlmostEqual(arguments[10], 938.4)

    def test_home_reference_is_rejected_while_vehicle_is_armed(self) -> None:
        mavlink = SimpleNamespace(
            MAV_CMD_DO_SET_HOME=179,
            MAV_MODE_FLAG_SAFETY_ARMED=128,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(
                target_system=7,
                target_component=1,
                message_state={"HEARTBEAT": SimpleNamespace(base_mode=128)},
            )
            is_connected = True

            def __init__(self) -> None:
                self.mavlink = mavlink

            def call_mav(self, *_args) -> None:
                raise AssertionError("MAV_CMD_DO_SET_HOME must not be sent while armed")

            def latest(self, _message_type):
                return None

        vehicle = MavlinkVehicleService(Client())  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 7, 1)
        vehicle._last_heartbeat = time.monotonic()

        with self.assertRaisesRegex(RuntimeError, "armed"):
            vehicle.execute(
                CommandRequest(
                    "SET_HOME",
                    {"latitude": 39.925533, "longitude": 32.836974, "altitude": 938.4},
                )
            )

    def test_guided_goto_holds_current_relative_altitude(self) -> None:
        mavlink = SimpleNamespace(MAV_FRAME_GLOBAL_RELATIVE_ALT_INT=6)

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(target_system=7, target_component=1)
            router = SimpleNamespace(sequence=0)
            is_connected = True

            def __init__(self) -> None:
                self.mavlink = mavlink
                self.sent: list[tuple[str, tuple]] = []

            def call_mav(self, method: str, *args) -> None:
                self.sent.append((method, args))

            @staticmethod
            def latest(message_type: str):
                assert message_type == "GLOBAL_POSITION_INT"
                return SimpleNamespace(relative_alt=123_400)

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 7, 1)

        vehicle.execute(
            CommandRequest("GOTO", {"latitude": 39.925533, "longitude": 32.836974})
        )

        method, arguments = client.sent[0]
        self.assertEqual(method, "set_position_target_global_int_send")
        self.assertEqual(arguments[4], 0b0000111111111000)
        self.assertAlmostEqual(arguments[7], 123.4)

    def test_capabilities_use_connected_autopilot_mode_mapping(self) -> None:
        test_case = self
        mavlink = SimpleNamespace(
            MAV_CMD_NAV_TAKEOFF=22,
            MAV_CMD_MISSION_START=300,
            MAV_CMD_DO_PAUSE_CONTINUE=193,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(target_system=7, target_component=1)
            is_connected = True

            def __init__(self) -> None:
                self.mavlink = mavlink

            def call_raw(self, name: str):
                test_case.assertEqual(name, "mode_mapping")
                return {"GUIDED": 15, "BRAKE": 17, "RTL": 11}

        vehicle = MavlinkVehicleService(Client())  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 7, 1, 2, 3)

        capabilities = vehicle.capabilities

        self.assertEqual(capabilities.flight_modes, ("GUIDED", "BRAKE", "RTL"))
        self.assertTrue(capabilities.supports("TAKEOFF"))
        self.assertTrue(capabilities.supports("HOLD"))
        self.assertTrue(capabilities.supports("MISSION_PAUSE"))
        self.assertEqual(capabilities.vehicle_type, 2)

    def test_dispatch_rejects_unsupported_capability_before_vehicle_call(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)
            capabilities = VehicleCapabilities(command_codes=("ARM",))

            def execute(self, _request: CommandRequest) -> bool:
                raise AssertionError("unsupported command must not reach vehicle")

        commands = DispatchCommandService(
            Vehicle(),  # type: ignore[arg-type]
            MutableParameterService(),
        )

        result = commands.dispatch(
            CommandRequest("GOTO", {"latitude": 1, "longitude": 2})
        )

        self.assertFalse(result.accepted)
        self.assertIn("desteklediğini bildirmiyor", result.message)

    def test_dispatch_normalizes_portable_command_aliases(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)
            capabilities = VehicleCapabilities(command_codes=("RTL",))

            def __init__(self) -> None:
                self.request: CommandRequest | None = None

            def execute(self, request: CommandRequest) -> bool:
                self.request = request
                return True

        vehicle = Vehicle()
        commands = DispatchCommandService(vehicle, MutableParameterService())  # type: ignore[arg-type]

        result = commands.dispatch(CommandRequest("return_to_launch"))

        self.assertTrue(result.accepted)
        self.assertIsNotNone(vehicle.request)
        self.assertEqual(vehicle.request.code, "RTL")

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
        result = commands.dispatch(
            CommandRequest("GOTO", {"latitude": 1, "longitude": 2})
        )
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
        self.assertEqual(
            result.details["normal_arm_error"], "Pre-arm kontrolü reddetti"
        )
        self.assertIn("ZORLA ARM", result.message)
        self.assertTrue(logs.history[-1].details["forced"])

    def test_mission_upload_result_reports_vehicle_accepted_item_count(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def upload_mission(self, overlays, *, loop: bool = False) -> int:
                self.overlays = list(overlays)
                self.loop = loop
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
        self.assertFalse(vehicle.loop)
        self.assertIn("1 görev öğesi", result.message)
        self.assertEqual(logs.history[-1].details["item_count"], 1)
        self.assertEqual(logs.history[-1].details["vehicle_item_count"], 1)
        self.assertEqual(logs.history[-1].details["requested_item_count"], 1)
        self.assertEqual(logs.history[-1].details["repeat_mode"], "once")
        self.assertEqual(logs.history[-1].details["item_types"], {"waypoint": 1})
        self.assertEqual(
            localized_log_message(logs.history[-1], "tr"),
            "1 görev öğeli tek seferlik plan uçuş cihazı tarafından kabul edildi",
        )

    def test_failed_mission_upload_log_preserves_plan_summary(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def upload_mission(self, _overlays, *, loop: bool = False) -> int:
                self.loop = loop
                raise TimeoutError("MISSION_ACK zaman aşımı")

        logs = LoggingService()
        commands = DispatchCommandService(
            Vehicle(),  # type: ignore[arg-type]
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
                        ).to_dict(),
                    ],
                    "loop": True,
                },
            )
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.details["vehicle_item_count"], 1)
        self.assertTrue(result.details["loop"])
        self.assertEqual(result.details["repeat_mode"], "loop")
        failure = logs.history[-1]
        self.assertEqual(failure.action, "command.failed")
        self.assertEqual(failure.details["error"], "MISSION_ACK zaman aşımı")
        self.assertEqual(
            localized_log_message(failure, "tr"),
            "1 görev öğeli döngü planı gönderilemedi: MISSION_ACK zaman aşımı",
        )

    def test_auto_mode_changes_mode_without_uploading_the_mission(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def upload_mission(self, overlays, *, loop: bool = False) -> int:
                items = list(overlays)
                self.calls.append(("upload", (items, loop)))
                return len(items)

            def execute(self, request) -> bool:
                self.calls.append(("execute", request))
                return True

        vehicle = Vehicle()
        commands = DispatchCommandService(
            vehicle,  # type: ignore[arg-type]
            MutableParameterService(),
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
                raise NotImplementedError(
                    f"Araç davranışı tanımlı değil: {request.code}"
                )

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
                    SimpleNamespace(seq=1),
                    SimpleNamespace(type=mavlink.MAV_MISSION_ACCEPTED),
                ]

            def call_mav(self, method: str, *args) -> None:
                self.sent.append((method, args))

            def wait_for(self, *_args, **kwargs):
                response = self.responses[0]
                predicate = kwargs.get("predicate")
                if predicate is not None:
                    foreign = SimpleNamespace(
                        get_srcSystem=lambda: 2,
                        get_srcComponent=lambda: 1,
                    )
                    foreign_component = SimpleNamespace(
                        get_srcSystem=lambda: 1,
                        get_srcComponent=lambda: 191,
                    )
                    if predicate(foreign) or predicate(foreign_component):
                        raise AssertionError("foreign mission response matched")
                    response.get_srcSystem = lambda: 1
                    response.get_srcComponent = lambda: 1
                    if not predicate(response):
                        raise AssertionError("target mission response did not match")
                return self.responses.pop(0)

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 1, 1)
        count = vehicle.upload_mission(
            [MapOverlay("wp", "waypoint", "WP", [[39.1, 32.1]], "#fff", 35)]
        )
        self.assertEqual(count, 1)
        self.assertEqual(
            [call[0] for call in client.sent],
            [
                "mission_count_send",
                "mission_item_int_send",
                "mission_item_int_send",
                "mission_set_current_send",
            ],
        )
        self.assertEqual(client.sent[0][1][2], 2)
        self.assertEqual(client.sent[1][1][2], 0)
        self.assertEqual(client.sent[2][1][2], 1)
        self.assertEqual(client.sent[2][1][11:13], (391_000_000, 321_000_000))
        self.assertEqual(client.sent[3][1], (1, 1, 1))

    def test_mission_download_filters_count_by_target_source(self) -> None:
        mavlink = SimpleNamespace(
            MAV_MISSION_TYPE_MISSION=0,
            MAV_MISSION_ACCEPTED=0,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(target_system=7, target_component=1)
            router = SimpleNamespace(sequence=0)
            is_connected = True

            def __init__(self) -> None:
                self.mavlink = mavlink

            def call_mav(self, *_args) -> None:
                pass

            def wait_for(self, *_args, **kwargs):
                predicate = kwargs["predicate"]
                foreign = SimpleNamespace(
                    get_srcSystem=lambda: 8,
                    get_srcComponent=lambda: 1,
                    count=0,
                )
                if predicate(foreign):
                    raise AssertionError("foreign MISSION_COUNT matched")
                target = SimpleNamespace(
                    get_srcSystem=lambda: 7,
                    get_srcComponent=lambda: 1,
                    count=0,
                )
                if not predicate(target):
                    raise AssertionError("target MISSION_COUNT did not match")
                return target

        vehicle = MavlinkVehicleService(Client())  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 7, 1)

        self.assertEqual(vehicle.download_mission(), [])

    def test_mission_download_filters_item_by_target_source(self) -> None:
        target = SimpleNamespace(
            get_srcSystem=lambda: 7,
            get_srcComponent=lambda: 1,
            seq=0,
        )

        class Client:
            endpoint = SimpleNamespace(uri="test")
            connection = SimpleNamespace(target_system=7, target_component=1)
            router = SimpleNamespace(sequence=0)
            is_connected = True

            def wait_for(self, _message_types, *, predicate, **_kwargs):
                foreign_component = SimpleNamespace(
                    get_srcSystem=lambda: 7,
                    get_srcComponent=lambda: 191,
                    seq=0,
                )
                if predicate(foreign_component):
                    raise AssertionError("foreign MISSION_ITEM matched")
                if not predicate(target):
                    raise AssertionError("target MISSION_ITEM did not match")
                return target

        vehicle = MavlinkVehicleService(Client())  # type: ignore[arg-type]

        received = vehicle._wait_for_mission_download(
            "MISSION_ITEM_INT",
            after_sequence=0,
            target=(7, 1),
            predicate=lambda message: int(message.seq) == 0,
        )

        self.assertIs(received, target)

    def test_operator_upload_preempts_an_incomplete_background_mission_download(
        self,
    ) -> None:
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
                self.download_waiting = threading.Event()
                self.responses = [
                    SimpleNamespace(seq=0),
                    SimpleNamespace(seq=1),
                    SimpleNamespace(type=mavlink.MAV_MISSION_ACCEPTED),
                ]

            def call_mav(self, *_args) -> None:
                return

            def wait_for(self, message_types, *, timeout, **_kwargs):
                if message_types == "MISSION_COUNT":
                    self.download_waiting.set()
                    time.sleep(min(timeout, 0.01))
                    raise TimeoutError("mission count not available")
                return self.responses.pop(0)

        client = Client()
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 1, 1)
        download_errors: list[Exception] = []

        def download() -> None:
            try:
                vehicle.download_mission()
            except Exception as exc:
                download_errors.append(exc)

        thread = threading.Thread(target=download)
        thread.start()
        self.assertTrue(client.download_waiting.wait(0.5))

        count = vehicle.upload_mission(
            [MapOverlay("wp", "waypoint", "WP", [[39.1, 32.1]], "#fff", 35)]
        )
        thread.join(timeout=0.5)

        self.assertEqual(count, 1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(download_errors), 1)
        self.assertIsInstance(download_errors[0], MissionTransferInterrupted)

    def test_loop_mission_repeats_waypoints_without_repeating_takeoff(self) -> None:
        mavlink = SimpleNamespace(
            MAV_CMD_NAV_TAKEOFF=22,
            MAV_CMD_NAV_WAYPOINT=16,
            MAV_CMD_NAV_LOITER_UNLIM=17,
            MAV_CMD_NAV_RETURN_TO_LAUNCH=20,
            MAV_CMD_NAV_LAND=21,
            MAV_CMD_DO_JUMP=177,
            MAV_FRAME_GLOBAL_RELATIVE_ALT_INT=6,
            MAV_FRAME_MISSION=2,
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
                    *(SimpleNamespace(seq=sequence) for sequence in range(5)),
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
            [
                MapOverlay(
                    "takeoff",
                    "takeoff",
                    "Takeoff",
                    [[39.0, 32.0, 60.0]],
                    "#fff",
                    metadata={"route_order": 1},
                ),
                MapOverlay(
                    "route",
                    "route",
                    "Route",
                    [[39.1, 32.1, 100.0], [39.2, 32.2, 100.0]],
                    "#fff",
                    metadata={"route_order": 2},
                ),
            ],
            loop=True,
        )

        self.assertEqual(count, 3)
        self.assertEqual(client.sent[0], ("mission_count_send", (1, 1, 5, 0)))
        jump = client.sent[-2]
        self.assertEqual(jump[0], "mission_item_int_send")
        self.assertEqual(jump[1][2], 4)
        self.assertEqual(jump[1][4], mavlink.MAV_CMD_DO_JUMP)
        self.assertEqual(jump[1][7], 2.0)
        self.assertEqual(jump[1][8], -1.0)
        self.assertEqual(client.sent[-1], ("mission_set_current_send", (1, 1, 1)))

    def test_mission_structure_respects_navigation_command_phases(self) -> None:
        waypoint = MapOverlay(
            "wp",
            "waypoint",
            "WP",
            [[39.1, 32.1, 100.0]],
            "#fff",
            metadata={"route_order": 1},
        )
        takeoff_second = MapOverlay(
            "takeoff",
            "takeoff",
            "Takeoff",
            [[39.0, 32.0, 60.0]],
            "#fff",
            metadata={"route_order": 2},
        )
        with self.assertRaisesRegex(ValueError, "TAKEOFF.*ilk"):
            MavlinkVehicleService._validate_mission_structure(
                [waypoint, takeoff_second],
                loop=False,
            )

        land_first = MapOverlay(
            "land",
            "land",
            "Land",
            [[39.2, 32.2, 0.0]],
            "#fff",
            metadata={"route_order": 1},
        )
        waypoint_second = waypoint.with_changes(metadata={"route_order": 2})
        with self.assertRaisesRegex(ValueError, "LAND.*sonunda"):
            MavlinkVehicleService._validate_mission_structure(
                [land_first, waypoint_second],
                loop=False,
            )

        land_last = land_first.with_changes(metadata={"route_order": 2})
        waypoint_first = waypoint.with_changes(metadata={"route_order": 1})
        with self.assertRaisesRegex(ValueError, "Döngü.*terminal"):
            MavlinkVehicleService._validate_mission_structure(
                [waypoint_first, land_last],
                loop=True,
            )

    def test_mission_conversion_sorts_route_items_and_retains_areas_locally(
        self,
    ) -> None:
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
                MapOverlay(
                    "takeoff",
                    "takeoff",
                    "Takeoff",
                    [[39.0, 32.0, 100]],
                    "#fff",
                    metadata={"route_order": 0, "takeoff_pitch_deg": 12.5},
                ),
                MapOverlay("safe", "safe_zone", "Safe", [[39.0, 32.0]], "#fff"),
                MapOverlay(
                    "land",
                    "land",
                    "Land",
                    [[39.2, 32.2]],
                    "#fff",
                    metadata={"route_order": 2},
                ),
                MapOverlay(
                    "wp",
                    "waypoint",
                    "WP",
                    [[39.1, 32.1, 100]],
                    "#fff",
                    45,
                    {"route_order": 1},
                ),
            ],
            mavlink,
        )
        self.assertEqual([item[1] for item in items], [22, 16, 21])
        self.assertEqual(items[0][4], 12.5)
        self.assertEqual(items[1][8:10], (391_000_000, 321_000_000))
        self.assertEqual(items[1][5:7], (45.0, 0.0))
        self.assertEqual(items[2][10], 0.0)
        self.assertTrue(math.isnan(items[1][7]))
        self.assertEqual(ignored, ["safe_zone"])

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
