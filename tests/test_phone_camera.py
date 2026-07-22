from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.models.settings import PhoneCameraSettings, SettingsModel
from src.services.phone_camera import ScrcpyPhoneCameraBridge


class PhoneCameraTests(unittest.TestCase):
    def test_scrcpy_command_matches_usb_camera_profile(self) -> None:
        config = PhoneCameraSettings(
            enabled=True,
            adb_serial="R5CY81TMA6F",
            video_device="/dev/video20",
            camera_facing="back",
            width=1920,
            height=1080,
            fps=30,
            bitrate_mbps=16,
        )

        command = ScrcpyPhoneCameraBridge.build_command(config, config.adb_serial)

        self.assertEqual(command[0:3], ["scrcpy", "--serial", "R5CY81TMA6F"])
        self.assertIn("--video-source=camera", command)
        self.assertIn("--camera-facing=back", command)
        self.assertIn("--camera-size=1920x1080", command)
        self.assertIn("--camera-fps=30", command)
        self.assertIn("--video-bit-rate=16M", command)
        self.assertIn("--v4l2-sink=/dev/video20", command)
        self.assertIn("--no-playback", command)
        self.assertIn("--no-audio", command)

    def test_explicit_camera_id_takes_precedence_over_facing(self) -> None:
        config = PhoneCameraSettings(camera_id="2")

        command = ScrcpyPhoneCameraBridge.build_command(config, "SERIAL")

        self.assertIn("--camera-id=2", command)
        self.assertFalse(any(item.startswith("--camera-facing=") for item in command))

    def test_adb_devices_parser_handles_authorized_and_offline_devices(self) -> None:
        output = """List of devices attached
R5CY81TMA6F\tdevice
OLD_PHONE\toffline

"""

        devices = ScrcpyPhoneCameraBridge.parse_adb_devices(output)

        self.assertEqual(
            devices,
            {"R5CY81TMA6F": "device", "OLD_PHONE": "offline"},
        )

    def test_configured_adb_device_must_be_authorized(self) -> None:
        def run_command(*_args, **_kwargs):
            return SimpleNamespace(
                stdout="List of devices attached\nR5CY81TMA6F\toffline\n"
            )

        bridge = ScrcpyPhoneCameraBridge(
            PhoneCameraSettings(adb_serial="R5CY81TMA6F"),
            run_command=run_command,
        )

        with self.assertRaisesRegex(ConnectionError, "hazır değil"):
            bridge._resolve_adb_serial()

    def test_phone_settings_round_trip_in_nested_settings(self) -> None:
        model = SettingsModel.from_dict(
            {
                "backend": {
                    "phone_camera": {
                        "enabled": True,
                        "adb_serial": "R5CY81TMA6F",
                        "video_device": "/dev/video20",
                    }
                }
            }
        )

        self.assertTrue(model.backend.phone_camera.enabled)
        self.assertEqual(model.backend.phone_camera.adb_serial, "R5CY81TMA6F")
        self.assertEqual(
            model.to_dict()["backend"]["phone_camera"]["video_device"],
            "/dev/video20",
        )

    def test_invalid_virtual_video_device_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "/dev/videoN"):
            PhoneCameraSettings(video_device="/tmp/camera")


if __name__ == "__main__":
    unittest.main()
