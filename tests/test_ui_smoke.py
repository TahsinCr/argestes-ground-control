from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.models.commands import CommandRequest, CommandResult
from src.models.connections import ConnectionStatus
from src.models.mission import MapOverlay
from src.models.telemetry import TelemetrySnapshot
from src.models.video import VideoStreamStats
from src.ui.adapters.runtime import create_qt_runtime_services
from src.ui.config.i18n import t
from src.ui.themes.manager import apply_theme
from src.ui.views.main_window import MainWindow


ROOT = Path(__file__).resolve().parents[1]


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["yki-smoke-tests"])

    def test_main_window_starts_with_the_new_backend_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            runtime = create_qt_runtime_services(base_dir)
            theme = apply_theme(self.app, runtime.settings_service.current.theme)
            window = MainWindow(
                log_service=runtime.log_service,
                settings_service=runtime.settings_service,
                telemetry_service=runtime.telemetry_service,
                mission_service=runtime.mission_service,
                command_service=runtime.command_service,
                parameter_service=runtime.parameter_service,
                video_service=runtime.video_service,
                connection_service=runtime.connection_service,
                tile_cache_service=runtime.tile_cache_service,
                archive_service=runtime.archive_service,
                base_dir=ROOT,
                theme=theme,
            )
            window.show()
            runtime.start()
            for _attempt in range(20):
                if window.map_panel._js_ready:
                    break
                QTest.qWait(100)
            self.assertTrue(window.isVisible())
            self.assertTrue(window.map_panel._js_ready)
            self.assertIsNotNone(window.map_panel)
            self.assertGreater(window.parameters_panel.table.rowCount(), 0)
            self.assertEqual(window.bottom_status["video"].state, "danger")
            self.assertIn(theme.danger, window.bottom_status["video"].text_label.styleSheet())
            self.assertEqual(window.bottom_status["orin"].state, "danger")

            map_scripts: list[str] = []
            original_run_script = window.map_panel._run_script
            window.map_panel._run_script = map_scripts.append
            waiting_position = TelemetrySnapshot.zero().copy(
                telemetry_link=True,
                autopilot_link=True,
            )
            window.map_panel.update_snapshot(waiting_position)
            self.assertIn('"active": false', map_scripts[-1])
            first_position = waiting_position.copy(
                latitude=39.9,
                longitude=32.8,
                position_valid=True,
                gps_satellites=14,
                gps_fix="3D FIX",
                gps_hdop=0.82,
                local_north_m=145.25,
                local_east_m=-32.5,
                local_down_m=-114.6,
            )
            window.map_panel.update_snapshot(first_position)
            self.assertIn('"active": true', map_scripts[-1])
            self.assertIn('"gpsSatellites": 14', map_scripts[-1])
            self.assertIn('"gpsHdop": 0.82', map_scripts[-1])
            self.assertIn('"localPositionAvailable": true', map_scripts[-1])
            self.assertIn('"localNorth": 145.25', map_scripts[-1])
            self.assertIn('"localEast": -32.5', map_scripts[-1])
            self.assertIn('"localDown": -114.6', map_scripts[-1])
            jumped_position = first_position.copy(latitude=-35.36, longitude=149.16)
            window.map_panel.update_snapshot(jumped_position)
            self.assertIn('"resetTrack": true', map_scripts[-1])
            window.map_panel._run_script = original_run_script

            telemetry_chip = window.bottom_status["telemetry"]
            window._on_connection_status(
                ConnectionStatus(
                    "telemetry",
                    True,
                    "Canlı MAVLink telemetrisi alınıyor",
                    options={
                        "link_quality": 91,
                        "link_quality_available": True,
                    },
                )
            )
            self.assertEqual(
                telemetry_chip.text_label.text(),
                t("status.telemetry", window.language),
            )
            self.assertNotIn("%", telemetry_chip.text_label.text())
            packet_snapshot = TelemetrySnapshot.zero()
            packet_snapshot = packet_snapshot.copy(
                telemetry_link=True,
                autopilot_link=True,
                link_quality=93,
                link_quality_source="mavlink_packets",
                link_quality_received_at=packet_snapshot.timestamp,
                packets_in=391,
                packets_out=8,
            )
            window._on_telemetry(packet_snapshot)
            self.assertEqual(window.link_meters["command"].value.text(), "93%")
            self.assertEqual(window.link_meters["command"].detail.text(), "↓ 391 ↑ 8")
            self.assertEqual(window.battery_meter.value.text(), "--%")
            battery_snapshot = packet_snapshot.copy(
                battery_percent=76,
                battery_voltage=15.8,
                battery_current_amps=7.4,
                battery_consumed_mah=940,
            )
            window._on_telemetry(battery_snapshot)
            self.assertEqual(window.battery_meter.value.text(), "76%")
            self.assertEqual(window.battery_meter.detail.text(), "15.8 V · 7.4 A")
            self.assertIn("940 mAh", window.battery_meter.toolTip())
            self.assertEqual(
                window.link_meters["bulk"].label.text(),
                "Video Aktarımı",
            )
            window._on_video_stats(
                VideoStreamStats(
                    connected=True,
                    quality_percent=92,
                    frames_received=120,
                    fps=29.7,
                    bitrate_kbps=2420.0,
                    width=1280,
                    height=720,
                    frame_age_s=0.0,
                )
            )
            self.assertEqual(window.link_meters["bulk"].value.text(), "92%")
            self.assertEqual(
                window.link_meters["bulk"].detail.text(),
                "↓ 2.4 Mbps · 29.7 FPS",
            )
            telemetry_chip.set_state("success")
            self.assertIn(theme.success, telemetry_chip.text_label.styleSheet())
            telemetry_chip.set_state("danger")
            self.assertIn(theme.danger, telemetry_chip.text_label.styleSheet())

            window._open_connection_popup("telemetry", telemetry_chip)
            self.assertTrue(window.connection_popup.isVisible())
            self.assertEqual(window.connection_popup.channel, "telemetry")
            window.connection_popup.close()

            orin_chip = window.bottom_status["orin"]
            window._open_connection_popup("orin", orin_chip)
            self.assertTrue(window.connection_popup.isVisible())
            self.assertEqual(window.connection_popup.channel, "orin")
            self.assertIn("ORIN", window.connection_popup.title_label.text())
            self.assertTrue(window.connection_popup.baud_input.isVisible())
            self.assertEqual(
                window.connection_popup.endpoint_input.text(),
                runtime.settings_service.current.backend.mavlink.endpoint,
            )
            window.connection_popup.close()

            runtime.facade.upsert_mission_item(
                MapOverlay(
                    "external-rtl",
                    "rtl",
                    "External RTL",
                    [[39.9, 32.8]],
                    "#f4b942",
                    75,
                )
            )
            QTest.qWait(100)
            self.assertIn("external-rtl", window.map_panel._overlay_cache)

            window.map_panel._on_overlay_changed(
                {
                    "overlay_id": "ui-land",
                    "overlay_type": "land",
                    "label": "UI Land",
                    "points": [[39.91, 32.81]],
                    "radius_m": 35,
                    "color": "#2f7ed8",
                }
            )
            self.assertIn(
                "ui-land",
                {item.overlay_id for item in runtime.facade.mission_snapshot().overlays},
            )

            altitude_applied: list[object] = []
            window.map_panel._view.page().runJavaScript(
                "window.ykiMap.upsertOverlay({"
                "overlay_id:'altitude-takeoff',overlay_type:'takeoff',label:'TKF 1',"
                "points:[[39.915,32.815,85]],radius_m:35,color:'#78b7ff',"
                "metadata:{route_order:1}});"
                "openPointDialog('altitude-takeoff');"
                "document.getElementById('point-altitude-input').value='125';"
                "applyPointDialog();"
                "[...document.querySelectorAll('.overlay-label')]"
                ".some((label) => label.textContent.includes('125 m'));",
                altitude_applied.append,
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if altitude_applied:
                    break
            self.assertEqual(altitude_applied, [True])
            altitude_overlay = next(
                item
                for item in runtime.facade.mission_snapshot().overlays
                if item.overlay_id == "altitude-takeoff"
            )
            self.assertEqual(altitude_overlay.points[0][2], 125.0)

            transfer_scripts: list[str] = []
            original_run_script = window.map_panel._run_script
            window.map_panel._run_script = transfer_scripts.append
            current_overlays = [
                overlay.to_dict()
                for overlay in runtime.facade.mission_snapshot().overlays
            ]
            window.map_panel.command_started("MISSION_UPLOAD", {"overlays": current_overlays})
            window.map_panel.command_completed(
                CommandResult(
                    CommandRequest("MISSION_UPLOAD", {"overlays": current_overlays}),
                    True,
                    "2 görev öğesi uçuş cihazı tarafından kabul edildi",
                )
            )
            self.assertTrue(any('"success"' in script for script in transfer_scripts))
            runtime.facade.upsert_mission_item(
                MapOverlay(
                    "new-waypoint",
                    "waypoint",
                    "WP 2",
                    [[39.92, 32.82]],
                    "#74d99f",
                    35,
                )
            )
            QTest.qWait(50)
            self.assertTrue(any('"dirty"' in script for script in transfer_scripts))
            window.map_panel._run_script = original_run_script

            force_requests: list[tuple[str, dict]] = []
            window._last_snapshot = TelemetrySnapshot.zero().copy(
                telemetry_link=True,
                autopilot_link=True,
                armed=False,
            )
            window._confirm_force_arm = lambda _result: True  # type: ignore[method-assign]
            window.command_service.issue_command = (  # type: ignore[method-assign]
                lambda code, params=None: force_requests.append((code, params or {}))
            )
            failed_arm = CommandResult(
                CommandRequest("ARM"),
                False,
                "Pre-arm kontrolü reddetti",
            )
            window._on_command_completed(failed_arm)
            self.assertEqual(force_requests[0][0], "FORCE_ARM")
            self.assertTrue(force_requests[0][1]["operator_confirmation"])
            self.assertEqual(
                force_requests[0][1]["normal_arm_error"],
                "Pre-arm kontrolü reddetti",
            )

            force_requests.clear()
            window._confirm_force_arm = lambda _result: False  # type: ignore[method-assign]
            window._on_command_completed(failed_arm)
            self.assertEqual(force_requests, [])

            window.close()
            runtime.stop()
            window.deleteLater()
            QTest.qWait(100)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
