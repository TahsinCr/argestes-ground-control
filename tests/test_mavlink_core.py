from __future__ import annotations

import queue
import asyncio
import threading
import time
import unittest
from types import SimpleNamespace

from src.core.mavlink import (
    MavlinkClient,
    MavlinkAsyncChannel,
    MavlinkConnection,
    MavlinkEndpoint,
    MavlinkMessageFilter,
    MavlinkMessage,
)
from src.core.mavlink.cache import MessageCache

try:
    from pymavlink import mavutil as real_mavutil
except ImportError:
    real_mavutil = None


class _Message(SimpleNamespace):
    def get_type(self) -> str:
        return self.message_type

    def get_srcSystem(self) -> int:
        return self.source_system

    def get_srcComponent(self) -> int:
        return self.source_component

    def get_msgId(self) -> int:
        return self.message_id

    def get_seq(self) -> int:
        return int(getattr(self, "sequence", 0))

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class _MavSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return call


class _RawConnection:
    def __init__(self) -> None:
        self.inbox: queue.Queue[_Message] = queue.Queue()
        self.mav = _MavSender()
        self.messages: dict[str, _Message] = {}
        self.target_system = 17
        self.target_component = 1
        self.closed = False
        self.recv_calls: list[dict] = []
        self.mode = 0

    def wait_heartbeat(self, *, timeout: float):
        del timeout
        return _message("HEARTBEAT")

    def recv_match(self, **kwargs):
        self.recv_calls.append(kwargs)
        try:
            message = self.inbox.get(timeout=kwargs.get("timeout") or 0.01)
        except queue.Empty:
            return None
        self.messages[message.get_type()] = message
        return message

    def mode_mapping(self) -> dict[str, int]:
        return {"AUTO": 10}

    def set_mode(self, mode: int) -> None:
        self.mode = mode

    def close(self) -> None:
        self.closed = True


class _Mavutil:
    @staticmethod
    def evaluate_condition(condition: str, messages: dict[str, _Message]) -> bool:
        if condition == "ATTITUDE.roll > 0":
            return bool(messages.get("ATTITUDE") and messages["ATTITUDE"].roll > 0)
        return False


def _message(
    message_type: str,
    *,
    system: int = 1,
    component: int = 1,
    message_id: int = 0,
    **values,
) -> _Message:
    return _Message(
        message_type=message_type,
        source_system=system,
        source_component=component,
        message_id=message_id,
        **values,
    )


class MavlinkCoreTests(unittest.TestCase):
    @unittest.skipIf(real_mavutil is None, "pymavlink kurulmamış")
    def test_filter_matches_a_real_pymavlink_message(self) -> None:
        message = real_mavutil.mavlink.MAVLink_attitude_message(
            1000,
            0.2,
            -0.1,
            0.3,
            0.0,
            0.0,
            0.0,
        )
        message._header.srcSystem = 3
        message._header.srcComponent = 1
        message_filter = MavlinkMessageFilter.for_types(
            "ATTITUDE",
            source_systems=3,
            message_ids=30,
            condition="ATTITUDE.roll > 0",
        )
        self.assertIsInstance(message, MavlinkMessage)
        self.assertTrue(
            message_filter.matches(
                message,
                condition_evaluator=lambda condition: real_mavutil.evaluate_condition(
                    condition,
                    {"ATTITUDE": message},
                ),
            )
        )

    def test_endpoint_factories_keep_transport_details_out_of_consumers(self) -> None:
        self.assertEqual(MavlinkEndpoint.tcp("127.0.0.1", 5760).uri, "tcp:127.0.0.1:5760")
        self.assertEqual(MavlinkEndpoint.udp("0.0.0.0", 14550).uri, "udp:0.0.0.0:14550")
        self.assertEqual(
            MavlinkEndpoint(uri="127.0.0.1:5762").uri,
            "tcp:127.0.0.1:5762",
        )
        serial = MavlinkEndpoint.serial("/dev/ttyUSB0", baud=921600)
        self.assertEqual((serial.uri, serial.baud), ("/dev/ttyUSB0", 921600))
        with self.assertRaisesRegex(ValueError, "baud ayrı"):
            MavlinkEndpoint(uri="tcp:127.0.0.1:115200")

    def test_async_channel_reuses_router_and_drops_oldest_when_full(self) -> None:
        async def scenario() -> None:
            raw = _RawConnection()
            connection = MavlinkConnection(
                MavlinkEndpoint(heartbeat_timeout=0.1),
                connection_factory=lambda _endpoint: raw,
                mavutil_module=_Mavutil,
            )
            client = MavlinkClient(connection=connection)
            channel = MavlinkAsyncChannel(client.router, "GPS_RAW_INT", maxsize=1)
            channel.start()
            client.start()
            raw.inbox.put(_message("HEARTBEAT"))
            raw.inbox.put(_message("GPS_RAW_INT", message_id=1))
            raw.inbox.put(_message("GPS_RAW_INT", message_id=2))
            await asyncio.sleep(0.15)
            message = await channel.receive(timeout=1.0)
            channel.stop()
            client.stop()
            self.assertEqual(message.message_id, 2)
            self.assertGreaterEqual(channel.dropped_messages, 1)

        asyncio.run(scenario())

    def test_filter_uses_native_mavlink_metadata_and_condition(self) -> None:
        message = _message("ATTITUDE", system=3, component=7, message_id=30, roll=0.5)
        evaluated: list[str] = []
        message_filter = MavlinkMessageFilter(
            message_types={"attitude"},
            source_systems=3,
            source_components={7},
            message_ids=30,
            condition="ATTITUDE.roll > 0",
            predicate=lambda value: value.roll < 1.0,
        )
        self.assertTrue(
            message_filter.matches(
                message,
                condition_evaluator=lambda condition: evaluated.append(condition) or True,
            )
        )
        self.assertEqual(evaluated, ["ATTITUDE.roll > 0"])
        self.assertFalse(message_filter.matches(_message("HEARTBEAT")))

    def test_connection_exposes_recv_match_filters_and_serialized_senders(self) -> None:
        raw = _RawConnection()
        connection = MavlinkConnection(
            MavlinkEndpoint(heartbeat_timeout=0.1),
            connection_factory=lambda _endpoint: raw,
            mavutil_module=_Mavutil,
        )
        states: list[bool] = []
        connection.connection_changed.subscribe(states.append)
        connection.start()
        raw.inbox.put(_message("ATTITUDE", roll=0.2))
        result = connection.receive(
            message_types={"heartbeat", "attitude"},
            condition="ATTITUDE.roll > 0",
            blocking=True,
            timeout=0.2,
        )
        connection.call_mav("command_long_send", 1, 2, 3)
        connection.call_raw("set_mode", 10)
        self.assertEqual(result.get_type(), "ATTITUDE")
        self.assertEqual(raw.recv_calls[-1]["type"], ["ATTITUDE", "HEARTBEAT"])
        self.assertEqual(raw.recv_calls[-1]["condition"], "ATTITUDE.roll > 0")
        self.assertEqual(raw.mav.calls[-1][0], "command_long_send")
        self.assertEqual(raw.mode, 10)
        self.assertEqual(connection.sent_messages, 2)
        self.assertTrue(connection.evaluate_condition("ATTITUDE.roll > 0"))
        self.assertEqual((connection.target_system, connection.target_component), (17, 1))
        connection.stop()
        self.assertEqual(states, [True, False])
        self.assertTrue(raw.closed)

    def test_client_requests_standard_mavlink_message_rate(self) -> None:
        raw = _RawConnection()
        mavutil = SimpleNamespace(
            mavlink=SimpleNamespace(MAV_CMD_SET_MESSAGE_INTERVAL=511),
        )
        connection = MavlinkConnection(
            MavlinkEndpoint(heartbeat_timeout=0.1),
            connection_factory=lambda _endpoint: raw,
            mavutil_module=mavutil,
        )
        client = MavlinkClient(connection=connection)
        client.start()
        interval_us = client.request_message_rate(
            30,
            20.0,
            target_system=17,
            target_component=1,
        )
        client.stop()

        self.assertEqual(interval_us, 50_000)
        method, arguments, keywords = raw.mav.calls[-1]
        self.assertEqual(method, "command_long_send")
        self.assertEqual(arguments[:4], (17, 1, 511, 0))
        self.assertEqual(arguments[4:6], (30.0, 50_000.0))
        self.assertEqual(keywords, {})

    def test_filtered_router_isolates_callback_errors_and_keeps_history(self) -> None:
        raw = _RawConnection()
        connection = MavlinkConnection(
            MavlinkEndpoint(heartbeat_timeout=0.1),
            connection_factory=lambda _endpoint: raw,
            mavutil_module=_Mavutil,
        )
        client = MavlinkClient(connection=connection)
        self.assertIs(MavlinkClient(router=client.router).connection, connection)
        received: list[_Message] = []
        errors = []
        delivered = threading.Event()
        client.router.errors.subscribe(errors.append)

        message_filter = MavlinkMessageFilter.for_types("HEARTBEAT", source_systems=1)

        def broken(_message) -> None:
            raise RuntimeError("consumer failed")

        client.subscribe(broken, message_filter)
        subscription = client.subscribe(
            lambda message: (received.append(message), delivered.set()),
            message_filter,
        )
        client.start()
        boundary = client.router.sequence
        raw.inbox.put(_message("ATTITUDE", system=1, roll=0.5))
        raw.inbox.put(_message("HEARTBEAT", system=2))
        expected = _message("HEARTBEAT", system=1)
        raw.inbox.put(expected)
        waited = client.wait_for(message_filter, timeout=1.0, after_sequence=boundary)
        self.assertTrue(delivered.wait(1.0))
        self.assertIs(waited, expected)
        self.assertEqual(received, [expected])
        self.assertIs(client.latest(message_filter), expected)
        self.assertEqual(client.router.history(message_filter)[-1].message, expected)
        self.assertGreaterEqual(client.router.stats.dispatch_errors, 1)
        self.assertTrue(errors)
        subscription.cancel()
        client.stop()

    def test_router_estimates_recent_mavlink_packet_delivery_from_source_sequences(self) -> None:
        raw = _RawConnection()
        connection = MavlinkConnection(
            MavlinkEndpoint(heartbeat_timeout=0.1),
            connection_factory=lambda _endpoint: raw,
            mavutil_module=_Mavutil,
        )
        client = MavlinkClient(connection=connection)
        client.start()
        raw.inbox.put(_message("ATTITUDE", sequence=10))
        raw.inbox.put(_message("ATTITUDE", sequence=11))
        raw.inbox.put(_message("ATTITUDE", sequence=14))
        deadline = time.monotonic() + 1.0
        while client.router.stats.received_messages < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        stats = client.router.stats
        client.stop()
        self.assertEqual(stats.estimated_dropped_messages, 2)
        self.assertEqual(stats.delivery_quality, 60)

    def test_router_evaluates_pymavlink_condition_from_current_message_state(self) -> None:
        raw = _RawConnection()
        connection = MavlinkConnection(
            MavlinkEndpoint(heartbeat_timeout=0.1),
            connection_factory=lambda _endpoint: raw,
            mavutil_module=_Mavutil,
        )
        client = MavlinkClient(connection=connection)
        received: list[_Message] = []
        delivered = threading.Event()
        client.subscribe(
            lambda message: (received.append(message), delivered.set()),
            MavlinkMessageFilter.for_types(
                "ATTITUDE",
                condition="ATTITUDE.roll > 0",
            ),
        )
        client.start()
        raw.inbox.put(_message("ATTITUDE", roll=-0.2))
        raw.inbox.put(_message("ATTITUDE", roll=0.3))
        self.assertTrue(delivered.wait(1.0))
        client.stop()
        self.assertEqual([message.roll for message in received], [0.3])

    def test_message_cache_keeps_bounded_per_type_snapshots(self) -> None:
        cache = MessageCache[_Message, str](lambda message: message.get_type(), per_key_limit=2)
        cache.add(_message("A", message_id=1))
        cache.add(_message("A", message_id=2))
        cache.add(_message("A", message_id=3))
        cache.add(_message("B", message_id=4))
        self.assertEqual([item.message_id for item in cache.all("A")], [2, 3])
        self.assertEqual(set(cache.keys), {"A", "B"})
        self.assertEqual(len(cache), 3)
        self.assertEqual(set(cache.snapshot()), {"A", "B"})


if __name__ == "__main__":
    unittest.main()
