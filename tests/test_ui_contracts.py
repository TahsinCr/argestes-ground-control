from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QSpinBox, QWidget

from src.models.commands import CommandRequest, CommandResult
from src.models.connections import ConnectionStatus
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.ui.config.commands import AUTONOMOUS_COMMANDS, FLIGHT_MODES, PROFILE_FIELDS
from src.ui.config.i18n import t
from src.ui.config.map import MAP_TOOLS
from src.ui.config.settings_schema import iter_fields
from src.ui.themes.palette import DARK
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.control_panels import CommandPanel, TelemetryPanel
from src.ui.widgets.connection_popup import ConnectionPopup
from src.ui.widgets.title_bar import FramelessResizeController


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
        self.assertFalse(panel.arm_button.isEnabled())
        self.assertFalse(panel.disarm_button.isEnabled())
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(telemetry_link=True, autopilot_link=True)
        )
        self.assertTrue(panel.arm_button.isEnabled())
        self.assertFalse(panel.disarm_button.isEnabled())
        self.assertEqual(panel.arm_button.text(), "ARM")
        self.assertIn("DISARMED", panel.disarm_button.text())
        self.assertTrue(panel.disarm_button.property("currentState"))
        self.assertEqual(panel.disarm_button.property("role"), "danger")
        self.assertIsNone(panel.arm_button.property("role"))
        requested: list[tuple[str, dict]] = []
        panel.command_requested.connect(lambda code, params: requested.append((code, params)))
        panel._request_arm(True)
        self.assertEqual(requested[-1][0], "ARM")
        self.assertFalse(panel._armed)
        self.assertEqual(panel.arm_button.text(), "ARM…")
        self.assertTrue(panel.arm_button.property("pendingState"))
        self.assertEqual(panel.arm_button.property("role"), "primary")
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
        self.assertFalse(panel.arm_button.isEnabled())
        self.assertTrue(panel.disarm_button.isEnabled())
        self.assertIn("ARMED", panel.arm_button.text())
        self.assertTrue(panel.arm_button.property("currentState"))
        self.assertEqual(panel.arm_button.property("role"), "success")
        self.assertIsNone(panel.disarm_button.property("role"))
        self.assertIn("doğrulandı", panel.command_status.text())
        panel._request_arm(False)
        self.assertEqual(requested[-1][0], "DISARM")
        self.assertFalse(hasattr(panel, "rtl_button"))
        self.assertFalse(hasattr(panel, "start_stop_toggle"))
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_force_arm_pending_state_is_explicit_and_failure_resets_it(self) -> None:
        panel = CommandPanel("", "")
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(telemetry_link=True, autopilot_link=True)
        )
        panel.command_started("FORCE_ARM")
        self.assertEqual(panel.arm_button.text(), "ARM…")
        self.assertTrue(panel.arm_button.property("pendingState"))
        panel.command_completed(
            CommandResult(
                CommandRequest("FORCE_ARM", {"operator_confirmation": True}),
                False,
                "Zorla ARM reddedildi",
            )
        )
        self.assertEqual(panel.arm_button.text(), "ARM")
        self.assertFalse(panel.arm_button.property("pendingState"))
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

    def test_telemetry_summary_shows_air_and_ground_speed_together(self) -> None:
        panel = TelemetryPanel("", "", DARK)
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(air_speed=27.5, ground_speed=24.25)
        )
        speed = panel.cards["airspeed"]
        self.assertEqual(speed.title_label.text(), "IAS / GS")
        self.assertEqual(speed.value_label.text(), "IAS 27.5 m/s")
        self.assertEqual(speed.detail_label.text(), "GS 24.2 m/s")
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
        ):
            self.assertIn(method, html)
        self.assertIn("payload.positionValid", html)
        self.assertIn("payload.resetTrack", html)
        self.assertIn('id="vehicle-readout"', html)
        self.assertIn('id="gps-summary"', html)
        self.assertIn('id="gps-satellites"', html)
        self.assertIn('id="gps-hdop"', html)
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
