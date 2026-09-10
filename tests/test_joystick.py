from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.models.joystick import JoystickButtonConfig, JoystickChannelConfig, JoystickSettings
from src.models.settings import SettingsModel
from src.models.vehicle import VehicleConnectionState
from src.services.joystick import LinuxRcJoystickService
from src.services.vehicle.mavlink import MavlinkVehicleService


class JoystickTests(unittest.TestCase):
    def test_settings_round_trip_keeps_rc_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsModel.default(Path(directory))
            payload = settings.to_dict()
            payload["backend"]["joystick"] = {
                "enabled": True,
                "device_path": "/dev/input/js2",
                "send_rate_hz": 30,
                "channels": [
                    {
                        "channel": 3,
                        "control": "axis:2",
                        "pwm_min": 1100,
                        "pwm_trim": 1500,
                        "pwm_max": 1900,
                        "deadzone": 0.05,
                        "expo": 25,
                        "reversed": True,
                    }
                ],
                "buttons": [
                    {"button": 0, "action": "SET_MODE", "mode": "GUIDED"},
                    {"button": 1, "action": "SET_SERVO", "servo": 5, "pwm": 1750},
                ],
            }

            restored = SettingsModel.from_dict(payload).backend.joystick

        self.assertTrue(restored.enabled)
        self.assertEqual(restored.device_path, "/dev/input/js2")
        self.assertEqual(restored.send_rate_hz, 30)
        self.assertEqual(len(restored.channels), 16)
        self.assertEqual(restored.channels[2].control, "axis:2")
        self.assertEqual(restored.channels[2].pwm_min, 1100)
        self.assertTrue(restored.channels[2].reversed)
        self.assertEqual(restored.buttons[0], JoystickButtonConfig(0, "SET_MODE", "GUIDED"))
        self.assertEqual(restored.buttons[1], JoystickButtonConfig(1, "SET_SERVO", servo=5, pwm=1750))

    def test_axis_deadzone_expo_reverse_and_button_modes_produce_pwm(self) -> None:
        config = JoystickSettings(
            channels=[
                JoystickChannelConfig(1, "axis:0", deadzone=0.1),
                JoystickChannelConfig(2, "axis:1", expo=100, reversed=True),
                JoystickChannelConfig(3, "button:0", pwm_min=900, pwm_max=2100),
                JoystickChannelConfig(4, "toggle:1"),
            ]
        )

        pwm = LinuxRcJoystickService.channel_pwm(
            config,
            axes=(0.05, 0.5),
            buttons=(True, False),
            toggles={1: True},
        )

        self.assertEqual(pwm[0], 1500)
        self.assertEqual(pwm[1], 1443)
        self.assertEqual(pwm[2], 2100)
        self.assertEqual(pwm[3], 2000)
        self.assertIsNone(pwm[4])

    def test_button_rows_keep_configured_order_and_allow_unassigned_actions(self) -> None:
        config = JoystickSettings(
            buttons=[
                JoystickButtonConfig(4, "ARM", slot=1),
                JoystickButtonConfig(3, "DISARM", slot=2),
                JoystickButtonConfig(-1, "SET_MODE", slot=6),
            ]
        )

        self.assertEqual([item.slot for item in config.buttons], [1, 2, 6])
        self.assertEqual([item.button for item in config.buttons], [4, 3, -1])

    def test_vehicle_adapter_encodes_and_releases_all_rc_channels(self) -> None:
        calls: list[tuple[str, tuple[object, ...]]] = []
        client = SimpleNamespace(
            endpoint=SimpleNamespace(uri="test"),
            is_connected=True,
            connection=SimpleNamespace(target_system=4, target_component=1),
            call_mav=lambda method, *arguments: calls.append((method, arguments)),
        )
        vehicle = MavlinkVehicleService(client)  # type: ignore[arg-type]
        vehicle._state = VehicleConnectionState(True, "test", 4, 1)

        vehicle.send_rc_override((1100, None, 1900))
        vehicle.release_rc_override()

        method, arguments = calls[0]
        self.assertEqual(method, "rc_channels_override_send")
        self.assertEqual(arguments[:5], (4, 1, 1100, 65535, 1900))
        self.assertEqual(len(arguments), 20)
        self.assertEqual(calls[1][1][2:10], (0,) * 8)
        self.assertEqual(calls[1][1][10:], (65534,) * 10)


if __name__ == "__main__":
    unittest.main()
