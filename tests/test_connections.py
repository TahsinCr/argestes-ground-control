from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.core.events import EventStream
from src.models.competition import CompetitionConnectionState
from src.models.telemetry import TelemetrySnapshot
from src.models.vehicle import VehicleConnectionState
from src.services.connections import BackendConnectionControlService
from src.services.vehicle.telemetry import MutableTelemetryService
from src.services.settings import SettingsService
from src.services.video import MutableVideoService


class ConnectionHealthTests(unittest.TestCase):
    @staticmethod
    def _competition() -> SimpleNamespace:
        return SimpleNamespace(
            state=CompetitionConnectionState(
                connected=False,
                authenticated=False,
                running=False,
                endpoint="http://127.0.0.1:5000",
                team_number=1,
            ),
            changed=EventStream(),
        )

    def test_statuses_require_fresh_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            vehicle = SimpleNamespace(
                connected=True,
                state=VehicleConnectionState(True, "udp:127.0.0.1:14550", 1, 1, 1, 3),
                connection_changed=EventStream(),
            )
            telemetry = MutableTelemetryService()
            video = MutableVideoService()
            competition = SimpleNamespace(
                state=CompetitionConnectionState(
                    connected=True,
                    authenticated=True,
                    running=True,
                    endpoint="http://127.0.0.1:5000",
                    team_number=1,
                    last_success_at=datetime.now(UTC) - timedelta(seconds=30),
                ),
                changed=EventStream(),
            )
            service = BackendConnectionControlService(
                settings=settings,
                vehicle=vehicle,
                telemetry=telemetry,
                video=video,
                competition=competition,
            )

            stale_at = datetime.now(UTC) - timedelta(seconds=30)
            telemetry.push(
                TelemetrySnapshot.zero().copy(
                    telemetry_link=True,
                    autopilot_link=True,
                    latitude=39.9,
                    longitude=32.8,
                    position_valid=True,
                    gps_satellites=12,
                    gps_fix="3D FIX",
                    timestamp=stale_at,
                    position_received_at=stale_at,
                )
            )
            self.assertTrue(service.status("autopilot").connected)
            self.assertFalse(service.status("telemetry").connected)
            self.assertFalse(service.status("gps").connected)
            self.assertFalse(service.status("server").connected)

            now = datetime.now(UTC)
            telemetry.push(
                telemetry.current.copy(
                    timestamp=now,
                    position_received_at=now,
                    link_quality=82,
                    link_quality_source="rf",
                    link_quality_received_at=now,
                )
            )
            competition.state = CompetitionConnectionState(
                connected=True,
                authenticated=True,
                running=True,
                endpoint="http://127.0.0.1:5000",
                team_number=1,
                last_success_at=now,
            )
            self.assertTrue(service.status("telemetry").connected)
            self.assertTrue(service.status("gps").connected)
            self.assertTrue(service.status("server").connected)
            telemetry_status = service.status("telemetry")
            self.assertTrue(telemetry_status.options["link_quality_available"])
            self.assertEqual(telemetry_status.options["link_quality"], 82)
            self.assertIn("RF hat sağlığı %82", telemetry_status.detail)

    def test_vehicle_popup_settings_are_saved_before_a_failed_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))

            class FailingVehicle:
                connected = False
                state = VehicleConnectionState(False, "old")
                connection_changed = EventStream()

                def reconnect(self, config) -> None:
                    self.config = config
                    raise ConnectionError("test connection failure")

            vehicle = FailingVehicle()
            service = BackendConnectionControlService(
                settings=settings,
                vehicle=vehicle,  # type: ignore[arg-type]
                telemetry=MutableTelemetryService(),
                video=MutableVideoService(),
                competition=self._competition(),
            )

            with self.assertRaisesRegex(ConnectionError, "test connection failure"):
                service.reconnect(
                    "telemetry",
                    {
                        "endpoint": "COM3",
                        "baud": 57_600,
                        "timeout_s": 7,
                        "auto_connect": True,
                    },
                )

            persisted = SettingsService(Path(directory)).current.backend.mavlink
            self.assertEqual(persisted.endpoint, "COM3")
            self.assertEqual(persisted.baud, 57_600)
            self.assertEqual(persisted.heartbeat_timeout, 7)
            self.assertTrue(persisted.auto_connect)
            self.assertEqual(vehicle.config.endpoint, "COM3")

    def test_video_popup_settings_are_saved_before_a_failed_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))

            class FailingVideo(MutableVideoService):
                def configure(self, **_options) -> None:
                    raise ConnectionError("test video failure")

            vehicle = SimpleNamespace(
                connected=False,
                state=VehicleConnectionState(False, ""),
                connection_changed=EventStream(),
            )
            service = BackendConnectionControlService(
                settings=settings,
                vehicle=vehicle,
                telemetry=MutableTelemetryService(),
                video=FailingVideo(),
                competition=self._competition(),
            )

            with self.assertRaisesRegex(ConnectionError, "test video failure"):
                service.reconnect(
                    "video",
                    {
                        "endpoint": "0.0.0.0:5601",
                        "source_type": "udp",
                        "codec": "h265",
                        "latency_ms": 120,
                        "frame_timeout_s": 4,
                        "auto_connect": True,
                    },
                )

            persisted = SettingsService(Path(directory)).current.backend
            self.assertTrue(persisted.video_auto_connect)
            self.assertEqual(persisted.video_source_type, "udp")
            self.assertEqual(persisted.video_source_uri, "0.0.0.0:5601")
            self.assertEqual(persisted.video_latency_ms, 120)
            self.assertEqual(persisted.video_frame_timeout_s, 4)

    def test_video_disconnect_disables_saved_auto_connect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            settings.set_value("backend.video_auto_connect", True)
            video = MutableVideoService()
            video.start()
            vehicle = SimpleNamespace(
                connected=False,
                state=VehicleConnectionState(False, ""),
                connection_changed=EventStream(),
            )
            service = BackendConnectionControlService(
                settings=settings,
                vehicle=vehicle,
                telemetry=MutableTelemetryService(),
                video=video,
                competition=self._competition(),
            )

            status = service.status("video")
            self.assertTrue(status.options["auto_connect"])
            service.disconnect("video")

            self.assertFalse(video.connected)
            self.assertFalse(settings.current.backend.video_auto_connect)

    def test_serial_permission_status_is_structured_for_the_ui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            settings.save(
                settings.current.with_value(
                    "backend.mavlink.endpoint",
                    "/dev/serial/by-id/test-aref",
                )
            )
            vehicle = SimpleNamespace(
                connected=False,
                state=VehicleConnectionState(False, ""),
                connection_changed=EventStream(),
            )
            service = BackendConnectionControlService(
                settings=settings,
                vehicle=vehicle,
                telemetry=MutableTelemetryService(),
                video=MutableVideoService(),
                competition=self._competition(),
            )
            device = Path("/dev/ttyUSB99")

            with (
                patch.object(
                    BackendConnectionControlService,
                    "_serial_device_path",
                    return_value=device,
                ),
                patch("src.services.connections.os.access", return_value=False),
            ):
                status = service.status("telemetry")

            self.assertFalse(status.connected)
            self.assertTrue(status.options["serial_permission_required"])
            self.assertEqual(status.options["serial_device"], str(device))
            self.assertIn("erişim izni yok", status.summary)

    def test_serial_access_repair_uses_user_acl_then_reconnects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            settings.save(
                settings.current.with_value(
                    "backend.mavlink.endpoint",
                    "/dev/serial/by-id/test-aref",
                )
            )

            class Vehicle:
                connected = False
                state = VehicleConnectionState(False, "")
                connection_changed = EventStream()

                def reconnect(self, config) -> None:
                    self.config = config

            vehicle = Vehicle()
            service = BackendConnectionControlService(
                settings=settings,
                vehicle=vehicle,  # type: ignore[arg-type]
                telemetry=MutableTelemetryService(),
                video=MutableVideoService(),
                competition=self._competition(),
            )
            device = Path("/dev/ttyUSB99")
            checks = iter([False])

            with (
                patch.object(service, "_serial_device_path", return_value=device),
                patch.object(service, "_grant_serial_acl") as grant_acl,
                patch(
                    "src.services.connections.os.access",
                    side_effect=lambda *_args: next(checks, True),
                ),
            ):
                service.repair_serial_access("telemetry")

            grant_acl.assert_called_once()
            self.assertEqual(grant_acl.call_args.args[0], device)
            self.assertEqual(vehicle.config.endpoint, "/dev/serial/by-id/test-aref")
            self.assertEqual(vehicle.config.baud, settings.current.backend.mavlink.baud)

    def test_serial_access_repair_rejects_non_device_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "USB/ACM"):
            BackendConnectionControlService._serial_device_path("/dev/null")

    def test_serial_access_grant_never_uses_world_writable_chmod(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch(
                "src.services.connections.shutil.which",
                side_effect=lambda command: f"/usr/bin/{command}",
            ),
            patch(
                "src.services.connections.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            BackendConnectionControlService._grant_serial_acl(
                Path("/dev/ttyUSB7"), "operator"
            )

        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                "/usr/bin/pkexec",
                "/usr/bin/setfacl",
                "-m",
                "u:operator:rw",
                "/dev/ttyUSB7",
            ],
        )
        self.assertNotIn("chmod", command)
        self.assertNotIn("666", command)


if __name__ == "__main__":
    unittest.main()
