from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QSpinBox, QWidget

from src.models.commands import CommandRequest, CommandResult
from src.models.connections import ConnectionStatus
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.video import VideoStreamStats
from src.ui.config.commands import AUTONOMOUS_COMMANDS, FLIGHT_MODES, PROFILE_FIELDS
from src.ui.config.i18n import t
from src.ui.config.map import MAP_TOOLS
from src.ui.config.settings_schema import iter_fields
from src.ui.themes.palette import DARK
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.attitude import AttitudeIndicator
from src.ui.widgets.control_panels import CommandPanel, TelemetryPanel
from src.ui.widgets.connection_popup import ConnectionPopup
from src.ui.widgets.title_bar import FramelessResizeController
from src.ui.widgets.video import VideoPanel


ROOT = Path(__file__).resolve().parents[1]


class UiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["yki-ui-tests"])

    def test_settings_dialog_is_fully_schema_driven(self) -> None:
        settings = SettingsModel()
        dialog = SettingsDialog(settings, "tr", DARK)
        fields = tuple(iter_fields())
        self.assertEqual(set(dialog._widgets), {field.path for field in fields})
        expected_widget_types = {
            "combo": QComboBox,
            "check": QCheckBox,
            "spin": QSpinBox,
            "text": QLineEdit,
        }
        for field in fields:
            self.assertIsInstance(dialog._widgets[field.path], expected_widget_types[field.kind])
        dialog._widgets["ui.theme"].setCurrentIndex(1)
        self.assertEqual(dialog.to_settings().ui.theme, "light")
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_video_overlay_exposes_stats_and_fit_fill_modes(self) -> None:
        panel = VideoPanel("", "", theme=DARK, language="tr")
        viewport = panel.viewport
        reconnect_requests: list[bool] = []
        panel.reconnect_requested.connect(lambda: reconnect_requests.append(True))
        viewport.resize(640, 360)
        image = QImage(400, 300, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.black)
        panel.set_frame(image)

        fit_geometry = viewport.frame_geometry()
        self.assertEqual(viewport.display_mode, "fit")
        self.assertTrue(viewport.fit_button.isChecked())
        self.assertEqual(
            (fit_geometry.x(), fit_geometry.y(), fit_geometry.width(), fit_geometry.height()),
            (80, 0, 480, 360),
        )

        viewport.fill_button.click()
        fill_geometry = viewport.frame_geometry()
        self.assertEqual(viewport.display_mode, "fill")
        self.assertTrue(viewport.fill_button.isChecked())
        self.assertEqual(
            (fill_geometry.x(), fill_geometry.y(), fill_geometry.width(), fill_geometry.height()),
            (0, -60, 640, 480),
        )
        self.assertIn("kırp", viewport.fill_button.toolTip().lower())
        viewport.reconnect_button.click()
        self.assertEqual(reconnect_requests, [True])
        self.assertIn("yeniden bağlan", viewport.reconnect_button.toolTip().lower())

        panel.set_stats(
            VideoStreamStats(
                connected=True,
                quality_percent=91,
                frames_received=150,
                fps=29.8,
                bitrate_kbps=2180.0,
                width=1280,
                height=720,
                frame_age_s=0.0,
            )
        )
        self.assertEqual(
            viewport.stats_label.text(),
            "1280×720  ·  29.8 FPS  ·  2.2 Mbps",
        )
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_frameless_cursor_clear_does_not_reenter_global_event_filter(self) -> None:
        window = QWidget()
        window.show()
        controller = FramelessResizeController(window, window)
        controller._cursor_active = True
        controller._clear_cursor()
        self.assertFalse(controller._cursor_active)
        controller._remove_filter()
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_command_panel_is_built_from_command_catalogs(self) -> None:
        panel = CommandPanel("", "")
        self.assertEqual(panel.mode_combo.count(), len(FLIGHT_MODES))
        self.assertIn("AUTOLAND", FLIGHT_MODES)
        self.assertIn("RTL", FLIGHT_MODES)
        self.assertNotIn("QLAND", FLIGHT_MODES)
        self.assertEqual(panel.mode_combo.itemText(panel.mode_combo.findData("AUTOLAND")), "LAND")
        self.assertEqual(set(panel.profile_inputs), {field.key for field in PROFILE_FIELDS})
        self.assertEqual(panel.task_list.count(), len(AUTONOMOUS_COMMANDS))
        self.assertFalse(panel.arm_toggle_button.isEnabled())
        self.assertEqual(panel.arm_toggle_button.text(), "ARM / DISARM")
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(telemetry_link=True, autopilot_link=True)
        )
        self.assertTrue(panel.arm_toggle_button.isEnabled())
        self.assertEqual(panel.arm_toggle_button.text(), "ARM")
        self.assertFalse(panel.arm_toggle_button.property("currentState"))
        self.assertEqual(panel.arm_toggle_button.property("role"), "success")
        requested: list[tuple[str, dict]] = []
        panel.command_requested.connect(lambda code, params: requested.append((code, params)))
        panel.arm_toggle_button.click()
        self.assertEqual(requested[-1][0], "ARM")
        self.assertFalse(panel._armed)
        self.assertEqual(panel.arm_toggle_button.text(), "ARM…")
        self.assertTrue(panel.arm_toggle_button.property("pendingState"))
        self.assertEqual(panel.arm_toggle_button.property("role"), "success")
        panel.command_started("ARM")
        panel.command_completed(CommandResult(CommandRequest("ARM"), True, "ARM doğrulandı"))
        self.assertFalse(panel._armed)
        self.assertIn("ONAY", panel.arm_label.text())
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(
                telemetry_link=True,
                autopilot_link=True,
                armed=True,
            )
        )
        self.assertTrue(panel.arm_toggle_button.isEnabled())
        self.assertEqual(panel.arm_toggle_button.text(), "DISARM")
        self.assertTrue(panel.arm_toggle_button.property("currentState"))
        self.assertEqual(panel.arm_toggle_button.property("role"), "danger")
        self.assertIn("doğrulandı", panel.command_status.text())
        panel.arm_toggle_button.click()
        self.assertEqual(requested[-1][0], "DISARM")
        self.assertEqual(panel.arm_toggle_button.text(), "DISARM…")
        self.assertEqual(panel.arm_toggle_button.property("role"), "danger")
        self.assertFalse(hasattr(panel, "rtl_button"))
        self.assertFalse(hasattr(panel, "start_stop_toggle"))
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_flight_dynamics_compass_uses_cardinal_directions(self) -> None:
        expected = {
            0.0: "N",
            45.0: "NE",
            90.0: "E",
            135.0: "SE",
            180.0: "S",
            225.0: "SW",
            270.0: "W",
            315.0: "NW",
            360.0: "N",
        }
        self.assertEqual(
            {
                heading: AttitudeIndicator._cardinal_direction(heading)
                for heading in expected
            },
            expected,
        )
        source = (ROOT / "src/ui/widgets/attitude.py").read_text(encoding="utf-8")
        self.assertIn('f"AS {self._snapshot.air_speed:0.1f} m/s"', source)
        self.assertIn('f"GS {self._snapshot.ground_speed:0.1f} m/s"', source)

    def test_force_arm_pending_state_is_explicit_and_failure_resets_it(self) -> None:
        panel = CommandPanel("", "")
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(telemetry_link=True, autopilot_link=True)
        )
        panel.command_started("FORCE_ARM")
        self.assertEqual(panel.arm_toggle_button.text(), "ARM…")
        self.assertTrue(panel.arm_toggle_button.property("pendingState"))
        panel.command_completed(
            CommandResult(
                CommandRequest("FORCE_ARM", {"operator_confirmation": True}),
                False,
                "Zorla ARM reddedildi",
            )
        )
        self.assertEqual(panel.arm_toggle_button.text(), "ARM")
        self.assertFalse(panel.arm_toggle_button.property("pendingState"))
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_configured_orin_task_is_shown_and_emitted_from_action_list(self) -> None:
        task_codes = tuple(command.code for command in AUTONOMOUS_COMMANDS) + ("PING_TEST",)
        panel = CommandPanel("", "", task_codes=task_codes)
        ping_row = next(
            row
            for row in range(panel.task_list.count())
            if panel.task_list.item(row).data(Qt.ItemDataRole.UserRole) == "PING_TEST"
        )
        self.assertIn("PING_TEST", panel.task_list.item(ping_row).text())
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(telemetry_link=True, autopilot_link=True)
        )
        requested: list[tuple[str, dict]] = []
        panel.command_requested.connect(lambda code, params: requested.append((code, params)))
        panel.task_list.setCurrentRow(ping_row)
        panel._emit_task_command()
        self.assertEqual(requested[-1][0], "SET_MISSION_TASK")
        self.assertEqual(requested[-1][1]["task"], "PING_TEST")
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_telemetry_panel_uses_consistent_flight_readouts_without_speed(self) -> None:
        panel = TelemetryPanel("", "", DARK)
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(
                air_speed=27.5,
                ground_speed=24.25,
                roll=-12.4,
                pitch=3.2,
                yaw=147.0,
                acceleration_x_mps2=0.35,
                acceleration_y_mps2=-0.22,
                acceleration_z_mps2=9.74,
                latitude=39.925123,
                longitude=32.836456,
                position_valid=True,
                local_north_m=145.25,
                local_east_m=-32.5,
                local_down_m=-114.6,
                altitude_agl=84.2,
                altitude_msl=932.7,
                climb_rate=2.4,
                throttle_percent=47.0,
                flight_mode="AUTO",
                system_status="ACTIVE",
                armed=True,
                ekf_ok=True,
                telemetry_link=True,
                autopilot_link=True,
            )
        )
        self.assertFalse(hasattr(panel, "cards"))
        self.assertNotIn("airspeed", panel.readouts)
        self.assertNotIn("ground_speed", panel.readouts)
        self.assertEqual(panel.readouts["flight_mode"].text(), "AUTO")
        self.assertEqual(panel.readouts["vehicle_state"].text(), "ARMED · ACTIVE")
        self.assertEqual(panel.readouts["altitude_agl"].text(), "84.2 m")
        self.assertEqual(panel.readouts["altitude_msl"].text(), "932.7 m")
        self.assertEqual(panel.readouts["climb_rate"].text(), "+2.4 m/s")
        self.assertEqual(panel.readouts["throttle"].text(), "47%")
        self.assertEqual(panel.readouts["load_factor"].text(), "0.99 g")
        self.assertEqual(panel.readouts["ekf"].text(), "OK")
        self.assertNotIn("gps", panel.readouts)
        self.assertNotIn("sat", panel.readouts)
        self.assertNotIn("link", panel.readouts)
        self.assertNotIn("lat", panel.readouts)
        self.assertNotIn("lon", panel.readouts)
        self.assertNotIn("barometer", panel.readouts)
        self.assertNotIn("temperature", panel.readouts)
        self.assertNotIn("cpu", panel.readouts)
        self.assertNotIn("rc", panel.readouts)
        self.assertEqual(panel.readouts["roll"].text(), "-12.4°")
        self.assertEqual(panel.readouts["pitch"].text(), "+3.2°")
        self.assertEqual(panel.readouts["yaw"].text(), "147°")
        self.assertEqual(panel.readouts["acceleration_x"].text(), "0.35 m/s²")
        self.assertEqual(panel.readouts["acceleration_y"].text(), "-0.22 m/s²")
        self.assertEqual(panel.readouts["acceleration_z"].text(), "9.74 m/s²")
        self.assertNotIn("global_position", panel.readouts)
        self.assertNotIn("local_position", panel.readouts)
        object_names = {label.objectName() for label in panel.readouts.values()}
        roles = {label.property("role") for label in panel.readouts.values()}
        self.assertEqual(object_names, {""})
        self.assertEqual(roles, {"readout-value"})
        panel.resize(320, 240)
        panel.show()
        self.app.processEvents()
        self.assertGreater(panel.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(panel.scroll_area.horizontalScrollBar().maximum(), 0)
        panel.resize(320, 900)
        self.app.processEvents()
        self.assertEqual(panel.scroll_area.verticalScrollBar().maximum(), 0)
        attitude_source = (ROOT / "src/ui/widgets/attitude.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("acceleration_x_mps2", attitude_source)
        self.assertNotIn("_draw_footer", attitude_source)
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_live_connection_status_does_not_overwrite_popup_edits(self) -> None:
        popup = ConnectionPopup(DARK)
        popup.show_status(
            ConnectionStatus(
                "telemetry",
                False,
                "Bağlantı yok",
                endpoint="udp:127.0.0.1:14550",
                timeout_s=10,
                options={"baud": 115200, "auto_connect": False},
                can_disconnect=False,
            )
        )
        self.assertIn("AREF telemetri hattına", popup.auto_connect.text())
        self.assertFalse(popup.auto_connect.isChecked())
        popup.endpoint_input.setText("COM3")
        popup.baud_input.setValue(57_600)

        popup.refresh_status(
            ConnectionStatus(
                "telemetry",
                True,
                "Canlı telemetri",
                endpoint="udp:10.0.0.2:14550",
                timeout_s=3,
                options={"baud": 921600, "auto_connect": True},
                can_disconnect=True,
            )
        )

        self.assertEqual(popup.endpoint_input.text(), "COM3")
        self.assertEqual(popup.baud_input.value(), 57_600)
        self.assertEqual(popup.summary_label.text(), "●  Canlı telemetri")
        self.assertEqual(popup.reconnect_button.text(), "Yeniden Bağlan")
        self.assertTrue(popup.disconnect_button.isEnabled())
        popup.close()
        popup.deleteLater()
        self.app.processEvents()

    def test_map_contract_exposes_all_configured_tools_and_backend_api(self) -> None:
        html = (ROOT / "src/ui/web/map.html").read_text(encoding="utf-8")
        self.assertIn("window.ykiMap = {", html)
        self.assertNotIn("window.kuzeyMap", html)
        for method in (
            "setUiConfig(config)",
            "setOverlays(items)",
            "upsertOverlay(data)",
            "removeOverlayById(id",
            "upsertContact(data)",
            "pushTelemetry(payload)",
            "setMissionTransferState(status, message)",
            "setCommandAvailable(available)",
            "setMissionLoop(loop)",
        ):
            self.assertIn(method, html)
        self.assertIn("payload.positionValid", html)
        self.assertIn("payload.resetTrack", html)
        self.assertIn('id="vehicle-readout"', html)
        self.assertIn('id="gps-summary"', html)
        self.assertIn('id="gps-satellites"', html)
        self.assertIn('id="gps-hdop"', html)
        self.assertIn('id="mission-mode-toggle"', html)
        self.assertIn("type: 'mission_loop_changed'", html)
        self.assertIn("loop: state.missionLoop", html)
        self.assertIn("updateGpsSummary(payload || {});", html)
        self.assertIn("state.lastVehicleLatLng.distanceTo(latlng)", html)
        self.assertIn("function installMiddleButtonMapPan()", html)
        self.assertIn("installMiddleButtonMapPan();", html)
        self.assertIn("draggable._onDown(event);", html)
        self.assertIn("if (!isPrimaryPointerEvent(event))", html)
        self.assertIn('id="point-altitude-input"', html)
        self.assertIn("function altitudeForLayer(layer)", html)
        self.assertIn("...(altitude !== null ? [altitude] : [])", html)
        keys = {tool.key for tool in MAP_TOOLS}
        self.assertTrue({"rtl", "land", "hss", "target", "safe_zone", "no_fly"} <= keys)
        self.assertIn("function isSourceManagedLayer(layer)", html)
        self.assertIn("shouldNotify && isSourceManagedLayer(layer)", html)
        self.assertFalse(next(tool for tool in MAP_TOOLS if tool.key == "hss").creatable)
        self.assertFalse(next(tool for tool in MAP_TOOLS if tool.key == "target").editable)
        self.assertIn('class="tool-button-label"', html)
        self.assertEqual(t("map.tool.send", "tr"), "Görevi Gönder")

    def test_mode_selection_is_not_overwritten_by_old_telemetry(self) -> None:
        panel = CommandPanel("", "")
        manual = TelemetrySnapshot.zero().copy(
            telemetry_link=True,
            autopilot_link=True,
            flight_mode="MANUAL",
        )
        panel.update_snapshot(manual)
        auto_index = panel.mode_combo.findData("AUTO")
        panel.mode_combo.setCurrentIndex(auto_index)
        panel.update_snapshot(manual)
        self.assertEqual(panel.mode_combo.currentData(), "AUTO")

        requested: list[tuple[str, dict]] = []
        panel.command_requested.connect(lambda code, params: requested.append((code, params)))
        panel._emit_mode_update()
        self.assertEqual(requested[-1][0], "APPLY_MODE")
        panel.command_started("APPLY_MODE")
        panel.update_snapshot(manual)
        self.assertEqual(panel.mode_combo.currentData(), "AUTO")
        panel.command_completed(
            CommandResult(
                CommandRequest("APPLY_MODE", {"mode": "AUTO"}),
                True,
                "SET_MODE uçuş cihazında doğrulandı",
            )
        )
        panel.update_snapshot(manual.copy(flight_mode="AUTO"))
        self.assertEqual(panel.mode_combo.currentData(), "AUTO")
        self.assertFalse(panel._mode_selection_dirty)
        panel.close()
        panel.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
