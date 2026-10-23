from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from extensions.joystick.models import JoystickButtonConfig, JoystickSettings
from extensions.joystick.runtime import LinuxRcJoystickService


class _Settings:
    def extension_config(self, _identifier: str):
        return {}


class _Vehicle:
    connected = True

    def __init__(self) -> None:
        self.started = threading.Event()
        self.finish = threading.Event()
        self.calls = 0

    def execute(self, _request) -> bool:
        self.calls += 1
        self.started.set()
        self.finish.wait(2)
        return True

    def send_rc_override(self, _channels) -> None:
        pass

    def release_rc_override(self) -> None:
        pass


class JoystickActionLifecycleTests(unittest.TestCase):
    def test_stop_does_not_wait_for_active_action_and_prevents_post_stop_work(self) -> None:
        vehicle = _Vehicle()
        service = LinuxRcJoystickService(_Settings(), vehicle)
        service._action_executor = ThreadPoolExecutor(max_workers=1)
        config = JoystickSettings(buttons=[JoystickButtonConfig(button=0, action="ARM")])

        service._execute_button_action(config, 0, service._action_generation)
        self.assertTrue(vehicle.started.wait(1))
        stopped = threading.Event()
        stopper = threading.Thread(target=lambda: (service.stop(), stopped.set()))
        stopper.start()
        stopper.join(0.5)
        self.assertTrue(stopped.is_set())
        self.assertEqual(vehicle.calls, 1)

        vehicle.finish.set()
        self.assertTrue(service._action_lock.acquire(timeout=1))
        service._action_lock.release()

        service._execute_button_action(config, 0, service._action_generation)
        self.assertEqual(vehicle.calls, 1)

    def test_stale_session_generation_does_not_dispatch_action(self) -> None:
        vehicle = _Vehicle()
        service = LinuxRcJoystickService(_Settings(), vehicle)
        service._action_executor = ThreadPoolExecutor(max_workers=1)
        service._action_generation += 1
        config = JoystickSettings(buttons=[JoystickButtonConfig(button=0, action="ARM")])

        service._execute_button_action(config, 0, service._action_generation - 1)

        self.assertEqual(vehicle.calls, 0)
        service._cancel_button_actions(wait=True)

    def test_device_session_end_waits_for_active_action_but_keeps_worker(self) -> None:
        vehicle = _Vehicle()
        service = LinuxRcJoystickService(_Settings(), vehicle)
        executor = ThreadPoolExecutor(max_workers=1)
        service._action_executor = executor
        config = JoystickSettings(buttons=[JoystickButtonConfig(button=0, action="ARM")])

        service._execute_button_action(config, 0, service._action_generation)
        self.assertTrue(vehicle.started.wait(1))
        finished = threading.Event()
        closer = threading.Thread(
            target=lambda: (service._cancel_button_actions(wait=True), finished.set())
        )
        closer.start()
        time.sleep(0.03)
        self.assertFalse(finished.is_set())

        vehicle.finish.set()
        closer.join(1)
        self.assertTrue(finished.is_set())
        self.assertIs(service._action_executor, executor)
        service._cancel_button_actions(wait=True, shutdown=True)


if __name__ == "__main__":
    unittest.main()
