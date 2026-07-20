from __future__ import annotations

import queue
import threading
import unittest
from abc import ABC

from src.core.abstracts import Model, Service
from src.core.dependency import CircularDependencyError, DependencyContainer
from src.core.events import EventStream
from src.core.mavlink.router import MavlinkMessageRouter
from src.models.geometry import Point


class _Message:
    def __init__(self, message_type: str, value: int = 0) -> None:
        self._message_type = message_type
        self.value = value

    def get_type(self) -> str:
        return self._message_type


class _Connection:
    def __init__(self) -> None:
        self.inbox: queue.Queue[_Message] = queue.Queue()
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def receive(self, *, blocking: bool = False, timeout: float | None = None):
        del blocking
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def evaluate_condition(self, _condition: str) -> bool:
        return True


class CoreTests(unittest.TestCase):
    def test_model_and_service_abstracts_define_the_universal_contracts(self) -> None:
        self.assertTrue(issubclass(Model, ABC))
        self.assertTrue(issubclass(Service, ABC))
        with self.assertRaises(TypeError):
            Model()

    def test_event_stream_subscription_can_be_cancelled(self) -> None:
        stream = EventStream[int]()
        received: list[int] = []
        subscription = stream.subscribe(received.append)
        stream.publish(1)
        subscription.cancel()
        stream.publish(2)
        self.assertEqual(received, [1])
        self.assertFalse(subscription.active)

    def test_isolated_event_publish_keeps_healthy_subscribers_running(self) -> None:
        stream = EventStream[int]()
        received: list[int] = []
        stream.subscribe(lambda _value: (_ for _ in ()).throw(RuntimeError("broken")))
        stream.subscribe(received.append)
        errors = stream.publish_isolated(4)
        self.assertEqual(received, [4])
        self.assertEqual(len(errors), 1)

    def test_dependency_lifetimes_and_circular_detection(self) -> None:
        root = DependencyContainer()
        root.singleton("singleton", object)
        root.scoped("scoped", object)
        self.assertIs(root.resolve("singleton"), root.resolve("singleton"))

        first_scope = root.create_scope()
        second_scope = root.create_scope()
        self.assertIs(first_scope.resolve("scoped"), first_scope.resolve("scoped"))
        self.assertIsNot(first_scope.resolve("scoped"), second_scope.resolve("scoped"))

        root.transient("a", lambda b: b, dependencies={"b": "b"})
        root.transient("b", lambda a: a, dependencies={"a": "a"})
        with self.assertRaises(CircularDependencyError):
            root.resolve("a")

    def test_point_distance_and_bearing(self) -> None:
        ankara = Point(39.9334, 32.8597)
        nearby = Point(39.9434, 32.8597)
        self.assertGreater(ankara.distance_to(nearby), 1_000)
        self.assertLess(ankara.distance_to(nearby), 1_200)
        self.assertAlmostEqual(ankara.bearing_to(nearby), 0.0, delta=0.2)

    def test_mavlink_router_is_the_single_reader_and_waits_after_boundary(self) -> None:
        connection = _Connection()
        router = MavlinkMessageRouter(connection)  # type: ignore[arg-type]
        received: list[_Message] = []
        router.messages.subscribe(received.append)
        router.start()
        boundary = router.sequence
        connection.inbox.put(_Message("HEARTBEAT", 7))
        message = router.wait_for("heartbeat", timeout=1.0, after_sequence=boundary)
        router.stop()
        self.assertEqual(message.value, 7)
        self.assertEqual(received, [message])
        self.assertFalse(connection.started)


if __name__ == "__main__":
    unittest.main()
