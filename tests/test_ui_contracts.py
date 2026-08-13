from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from src.models.commands import CommandRequest, CommandResult
from src.models.connections import ConnectionStatus
from src.models.logs import LogEntry
from src.models.mission import MapContact
from src.models.onboard import ACTUATOR_TEST_TASK, OnboardMissionState
from src.models.parameters import Parameter
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.video import VideoStreamStats
from src.ui.config.commands import AUTONOMOUS_COMMANDS, FLIGHT_MODES, PROFILE_FIELDS
from src.ui.i18n import t
from src.ui.config.map import MAP_TOOLS
from src.ui.config.settings_schema import iter_fields
from src.ui.themes.palette import DARK
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.attitude import AttitudeIndicator
from src.ui.widgets.control_panels import CommandPanel, TelemetryPanel
from src.ui.widgets.common import BatteryMeter, SignalMeter
from src.ui.widgets.battery_popup import BatterySettingsPopup
from src.ui.widgets.connection_popup import ConnectionPopup
from src.ui.widgets.logs import EventLogPanel
from src.ui.widgets.parameters import ParametersPanel
from src.ui.widgets.map_panel import MapPanel, MapWebBridge
from src.ui.widgets.title_bar import FramelessResizeController
from src.ui.widgets.video import VideoPanel


ROOT = Path(__file__).resolve().parents[1]


class UiContractTests(unittest.TestCase):
    def test_map_escapes_backend_labels_before_inserting_html(self) -> None:
        source = (ROOT / "src/ui/web/map.html").read_text(encoding="utf-8")
        self.assertIn("function escapeHtml(value)", source)
        self.assertIn("escapeHtml(layer.overlayData.label)", source)
        self.assertIn("escapeHtml(data.label || data.contact_id || 'Temas')", source)

    def test_map_hot_paths_are_bounded_and_incremental(self) -> None:
        source = (ROOT / "src/ui/web/map.html").read_text(encoding="utf-8")
        self.assertIn("const CURSOR_UPDATE_INTERVAL_MS = 50", source)
        self.assertIn("state.cursorTimer = window.setTimeout", source)
        self.assertIn("entry.trail.addLatLng", source)
        self.assertIn("updateContactTrail(entry, trailPoints)", source)

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
            "double": QDoubleSpinBox,
            "text": QLineEdit,
        }
        for field in fields:
            self.assertIsInstance(dialog._widgets[field.path], expected_widget_types[field.kind])
        dialog._widgets["ui.theme"].setCurrentIndex(1)
        self.assertEqual(dialog.to_settings().ui.theme, "light")
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_settings_dialog_validates_battery_voltage_fields_as_one_profile(self) -> None:
        dialog = SettingsDialog(SettingsModel(), "tr", DARK)
        full = dialog._widgets["backend.battery_telemetry.full_voltage"]
        minimum = dialog._widgets["backend.battery_telemetry.reserve_voltage"]
        minimum_percent = dialog._widgets[
            "backend.battery_telemetry.reserve_percent"
        ]
        full.setValue(14.0)
        minimum.setValue(13.0)
        minimum_percent.setValue(12.0)

        updated = dialog.to_settings().backend.battery_telemetry

        self.assertEqual(updated.full_voltage, 14.0)
        self.assertEqual(updated.reserve_voltage, 13.0)
        self.assertEqual(updated.reserve_percent, 12.0)
        full.setValue(12.0)
        with self.assertRaises(ValueError):
            dialog.to_settings()
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_battery_meter_blinks_only_while_alarm_is_active(self) -> None:
        meter = BatteryMeter("BATARYA")
        meter.set_theme(DARK)
        meter.set_battery(14, 14.2, 3.5)
        meter.set_alarm(True)
        self.assertTrue(meter.alarm_active)
        self.assertTrue(meter._alarm_timer.isActive())
        self.assertIn(DARK.danger, meter.value.styleSheet())

        first_style = meter.value.styleSheet()
        meter._toggle_alarm_phase()
        self.assertNotEqual(meter.value.styleSheet(), first_style)

        meter.set_alarm(False)
        self.assertFalse(meter.alarm_active)
        self.assertFalse(meter._alarm_timer.isActive())
        meter.close()
        meter.deleteLater()
        self.app.processEvents()

    def test_battery_meter_opens_a_complete_compact_settings_editor(self) -> None:
        meter = BatteryMeter("BATARYA")
        meter.resize(240, 32)
        meter.show()
        clicks: list[bool] = []
        meter.clicked.connect(lambda: clicks.append(True))
        QTest.mouseClick(meter, Qt.MouseButton.LeftButton)
        self.assertEqual(clicks, [True])

        popup = BatterySettingsPopup(DARK, language="tr")
        settings = SettingsModel()
        popup.show_settings(
            settings,
            TelemetrySnapshot.zero().copy(
                battery_percent=10,
                battery_voltage=14.8,
            ),
        )
        self.assertEqual(popup.full_voltage.value(), 16.8)
        self.assertEqual(popup.minimum_voltage.value(), 14.8)
        self.assertEqual(popup.minimum_percent.value(), 10.0)
        self.assertIn("14.80 V", popup.detail_label.text())

        saved: list[dict[str, object]] = []
        popup.save_requested.connect(saved.append)
        popup.minimum_percent.setValue(12.0)
        popup.save_button.click()
        self.assertEqual(saved[-1]["reserve_percent"], 12.0)
        popup.close()
        popup.deleteLater()
        meter.close()
        meter.deleteLater()
        self.app.processEvents()

    def test_signal_meter_does_not_restyle_for_packet_counter_updates(self) -> None:
        meter = SignalMeter("TELEMETRY")
        meter.set_theme(DARK)
        meter.set_value(80, "↓ 1 ↑ 1")
        meter.set_metric_tooltip("Delivery 80%\nRF 75%")
        self.assertEqual(meter.label.toolTip(), "Delivery 80%\nRF 75%")
        self.assertEqual(meter.toolTip(), "")
        self.assertEqual(meter.value.toolTip(), "")
        self.assertEqual(meter.detail.toolTip(), "")
        restyle_calls = 0
        original_restyle = meter._restyle

        def count_restyle() -> None:
            nonlocal restyle_calls
            restyle_calls += 1
            original_restyle()

        meter._restyle = count_restyle
        meter.set_value(80, "↓ 2 ↑ 1")
        meter.set_value(81, "↓ 3 ↑ 2")
        self.assertEqual(restyle_calls, 0)
        self.assertEqual(meter.detail.text(), "↓ 3 ↑ 2")

        meter.set_value(30, "↓ 4 ↑ 2")
        self.assertEqual(restyle_calls, 1)
        meter.close()
        meter.deleteLater()
        self.app.processEvents()

    def test_log_panel_translates_searches_and_filters_existing_entries(self) -> None:
        panel = EventLogPanel("", "", "")
        panel.set_language("en")
        self.assertEqual(panel.level_filter.height(), 30)
        self.assertFalse(panel.level_filter.itemIcon(0).isNull())
        self.assertEqual(panel.level_filter.minimumWidth(), 142)
        panel.add_entry(
            LogEntry(
                "COMMAND",
                "AUTO komutu gönderildi",
                severity="info",
                action="command.sent",
                details={"code": "AUTO"},
            )
        )
        panel.add_entry(
            LogEntry(
                "VEHICLE",
                "Uçuş cihazı bağlantısı kaybedildi",
                severity="danger",
                action="vehicle.link_lost",
            )
        )
        self.assertEqual(panel.table.rowCount(), 2)
        self.assertEqual(panel.table.item(0, 3).text(), "AUTO command sent")
        self.assertIsNone(panel.table.cellWidget(0, 1))
        self.assertEqual(panel.table.item(0, 1).text(), "Info")

        panel.search_input.setText("flight-controller")
        self.assertEqual(panel.table.rowCount(), 1)
        self.assertIn("link lost", panel.table.item(0, 3).text().lower())

        panel.search_input.clear()
        panel.add_entry(LogEntry("TEST", "Debug record", severity="debug"))
        panel.add_entry(LogEntry("TEST", "Successful record", severity="success"))
        panel.add_entry(LogEntry("TEST", "Warning record", severity="warning"))

        panel.level_filter.setCurrentIndex(panel.level_filter.findData("info"))
        self.assertEqual(panel.table.rowCount(), 4)
        visible_levels = {
            panel.table.item(row, 1).text()
            for row in range(panel.table.rowCount())
        }
        self.assertNotIn("Debug", visible_levels)
        self.assertEqual(
            visible_levels,
            {"Info", "Success", "Warning", "Error"},
        )

        panel.level_filter.setCurrentIndex(panel.level_filter.findData("warning"))
        self.assertEqual(panel.table.rowCount(), 2)

        panel.level_filter.setCurrentIndex(panel.level_filter.findData("danger"))
        self.assertEqual(panel.table.rowCount(), 1)
        self.assertEqual(panel.table.item(0, 2).text(), "VEHICLE")

        panel.set_language("tr")
        self.assertIn("kaybedildi", panel.table.item(0, 3).text())
        self.assertEqual(panel.search_input.placeholderText(), "Loglarda ara…")
        self.assertEqual(panel.clear_button.text(), "Logu Temizle")

        panel.clear_button.click()
        self.assertEqual(panel.table.rowCount(), 0)
        self.assertEqual(panel._entries, [])
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_log_panel_only_follows_new_entries_when_already_at_bottom(self) -> None:
        panel = EventLogPanel("", "", "")
        panel.resize(720, 260)
        panel.show()
        for index in range(40):
            panel.add_entry(LogEntry("TEST", f"Log {index}"))
        self.app.processEvents()

        scrollbar = panel.table.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(scrollbar.maximum() - 1)
        previous_scroll = scrollbar.value()
        self.assertFalse(panel._follow_latest)
        panel.add_entry(LogEntry("TEST", "Yukarıdayken gelen kayıt"))
        self.app.processEvents()
        self.assertEqual(scrollbar.value(), previous_scroll)
        self.assertLess(scrollbar.value(), scrollbar.maximum())

        scrollbar.setValue(scrollbar.maximum())
        self.assertTrue(panel._follow_latest)
        panel.add_entry(LogEntry("TEST", "En alttayken gelen kayıt"))
        self.app.processEvents()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_log_panel_does_not_rebuild_every_time_history_is_full(self) -> None:
        panel = EventLogPanel("", "", "")
        panel.MAX_ENTRIES = 5
        for index in range(5):
            panel.add_entry(LogEntry("TEST", f"Log {index}"))
        rebuilds = 0
        original_rebuild = panel._rebuild_table

        def count_rebuild(*args, **kwargs) -> None:
            nonlocal rebuilds
            rebuilds += 1
            original_rebuild(*args, **kwargs)

        panel._rebuild_table = count_rebuild
        panel.add_entry(LogEntry("TEST", "Newest"))

        self.assertEqual(rebuilds, 0)
        self.assertEqual(len(panel._entries), 5)
        self.assertEqual(panel._entries[-1].message, "Newest")
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_parameter_panel_appends_streamed_rows_without_rebuilding(self) -> None:
        panel = ParametersPanel("", "", "")
        first = Parameter("FIRST", 1.0, "-", "vehicle", "")
        second = Parameter("SECOND", 2.0, "-", "vehicle", "")
        panel.set_parameters([first])
        rebuilds = 0
        original_rebuild = panel._rebuild_table

        def count_rebuild(*args, **kwargs) -> None:
            nonlocal rebuilds
            rebuilds += 1
            original_rebuild(*args, **kwargs)

        panel._rebuild_table = count_rebuild
        panel.set_parameters([first, second])
        panel.set_parameters(
            [Parameter("FIRST", 3.0, "-", "vehicle", ""), second]
        )

        self.assertEqual(rebuilds, 0)
        self.assertEqual(panel.table.rowCount(), 2)
        self.assertEqual(panel.table.item(0, 1).text(), "3.0")
        self.assertEqual(panel.table.item(0, 2).text(), "—")
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

    def test_parameter_panel_updates_default_column_without_rebuilding(self) -> None:
        panel = ParametersPanel("", "", "")
        panel.set_parameters([Parameter("ARSPD_FBW_MIN", 17.0, "m/s", "flight", "")])
        rebuilds = 0
        original_rebuild = panel._rebuild_table

        def count_rebuild(*args, **kwargs) -> None:
            nonlocal rebuilds
            rebuilds += 1
            original_rebuild(*args, **kwargs)

        panel._rebuild_table = count_rebuild
        panel.set_parameters(
            [
                Parameter(
                    "ARSPD_FBW_MIN",
                    17.0,
                    "m/s",
                    "flight",
                    "",
                    default_value=12.0,
                )
            ]
        )

        self.assertEqual(rebuilds, 0)
        self.assertEqual(panel.table.columnCount(), 5)
        self.assertEqual(panel.table.item(0, 2).text(), "12.0")
        self.assertFalse(panel.table.item(0, 2).flags() & Qt.ItemFlag.ItemIsEditable)
        panel.close()
        panel.deleteLater()
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

    def test_map_bridge_ignores_malformed_web_payloads(self) -> None:
        bridge = MapWebBridge()
        actions: list[dict] = []
        bridge.map_action.connect(actions.append)

        bridge.mapAction("{not-json")
        bridge.mapAction("[]")
        self.assertEqual(actions, [])

        bridge.mapAction('{"type":"goto","latitude":39.9}')
        self.assertEqual(actions[-1]["type"], "goto")
        bridge.deleteLater()
        self.app.processEvents()

    def test_map_contact_transport_keeps_only_visible_trail_points(self) -> None:
        contact = MapContact(
            "competitor-1",
            "Competitor",
            39.9,
            32.8,
            trail_points=[[39.0 + index / 1000, 32.0] for index in range(160)],
            metadata={"altitude_m": 120.0},
        )

        payload = MapPanel._contact_payload(contact, 40)

        self.assertEqual(len(payload["trail_points"]), 40)
        self.assertEqual(payload["trail_points"][0], contact.trail_points[-40])
        self.assertEqual(payload["metadata"], {"altitude_m": 120.0})

    def test_frameless_cursor_clear_does_not_reenter_global_event_filter(self) -> None:
        window = QWidget()
        window.resize(640, 480)
        window.show()
        controller = FramelessResizeController(window, window)
        self.assertEqual(len(controller._edge_handles), 8)
        right_handle = next(
            handle
            for handle, edges in controller._edge_handles
            if edges == Qt.Edge.RightEdge
        )
        self.assertEqual(right_handle.width(), controller._border)
        self.assertEqual(right_handle.x(), window.width() - controller._border)

        start_geometry = window.geometry()
        controller._resizing = True
        controller._resize_edges = Qt.Edge.RightEdge
        controller._start_pos = start_geometry.topRight()
        controller._start_geometry = start_geometry
        controller._queue_resize(controller._start_pos + QPoint(80, 0))
        self.assertEqual(window.geometry(), start_geometry)
        controller._flush_pending_resize()
        self.assertEqual(window.width(), start_geometry.width() + 80)
        controller._finish_manual_resize()

        controller._cursor_active = True
        controller._clear_cursor()
        self.assertFalse(controller._cursor_active)
        controller._remove_filter()
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_command_panel_is_built_from_command_definitions(self) -> None:
        panel = CommandPanel("", "")
        self.assertEqual(panel.mode_combo.count(), len(FLIGHT_MODES))
        self.assertIn("AUTOLAND", FLIGHT_MODES)
        self.assertIn("RTL", FLIGHT_MODES)
        self.assertNotIn("QLAND", FLIGHT_MODES)
        self.assertEqual(panel.mode_combo.itemText(panel.mode_combo.findData("AUTOLAND")), "LAND")
        self.assertEqual(set(panel.profile_inputs), {field.key for field in PROFILE_FIELDS})
        self.assertEqual(panel.task_list.count(), len(AUTONOMOUS_COMMANDS))
        actuator_row = next(
            row
            for row in range(panel.task_list.count())
            if panel.task_list.item(row).data(Qt.ItemDataRole.UserRole)
            == "ACTUATOR_TEST"
        )
        self.assertEqual(panel.task_list.item(actuator_row).text(), "Aktüatör Testi")
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
        self.assertIn("tamamlandı", panel.command_status.text())
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
        self.assertIn("self._display_values['air_speed']", source)
        self.assertIn("self._display_values['ground_speed']", source)

    def test_flight_dynamics_smooths_samples_and_holds_short_gaps(self) -> None:
        indicator = AttitudeIndicator(DARK)
        first = TelemetrySnapshot.zero().copy(
            telemetry_link=True,
            autopilot_link=True,
            roll=-10.0,
            pitch=0.0,
            heading=359.0,
            air_speed=20.0,
            ground_speed=18.0,
            altitude_agl=100.0,
        )
        indicator.update_snapshot(first)
        self.assertEqual(indicator._display_values["heading"], 359.0)
        self.assertTrue(indicator.has_recent_telemetry)

        indicator.update_snapshot(
            first.copy(
                roll=12.0,
                pitch=8.0,
                heading=1.0,
                air_speed=24.0,
                ground_speed=22.0,
                altitude_agl=120.0,
            )
        )
        indicator._animation_step(0.07)
        self.assertLess(
            abs(AttitudeIndicator._shortest_angle_delta(
                indicator._display_values["heading"],
                1.0,
            )),
            2.0,
        )
        self.assertGreater(indicator._display_values["pitch"], 0.0)
        self.assertLess(indicator._display_values["pitch"], 8.0)
        self.assertGreater(indicator._display_values["altitude_agl"], 100.0)
        self.assertLess(indicator._display_values["altitude_agl"], 120.0)

        target_before_gap = dict(indicator._target_values)
        indicator.update_snapshot(
            TelemetrySnapshot.zero().copy(
                telemetry_link=False,
                autopilot_link=False,
            )
        )
        self.assertEqual(indicator._target_values, target_before_gap)
        self.assertTrue(indicator.has_recent_telemetry)
        indicator.close()
        indicator.deleteLater()
        self.app.processEvents()

    def test_flight_dynamics_keeps_moving_between_telemetry_samples(self) -> None:
        indicator = AttitudeIndicator(DARK)
        indicator._display_values["pitch"] = 2.0
        indicator._target_values["pitch"] = 4.0
        indicator._field_velocities["pitch"] = 20.0
        indicator._field_sample_periods["pitch"] = 0.1
        indicator._field_sample_ages["pitch"] = 0.0

        indicator._animation_step(0.05)
        first_frame = indicator._display_values["pitch"]
        first_target = indicator._projected_target("pitch", 4.0)
        indicator._animation_step(0.05)
        second_frame = indicator._display_values["pitch"]
        second_target = indicator._projected_target("pitch", 4.0)

        self.assertGreater(first_target, 4.0)
        self.assertGreater(second_target, first_target)
        self.assertGreater(second_frame, first_frame)
        self.assertEqual(indicator._animation_timer.interval(), 16)
        self.assertEqual(
            indicator._animation_timer.timerType(),
            Qt.TimerType.PreciseTimer,
        )
        indicator.close()
        indicator.deleteLater()
        self.app.processEvents()

    def test_flight_dynamics_keeps_moving_across_one_hz_sample_period(self) -> None:
        indicator = AttitudeIndicator(DARK)
        indicator._display_values["pitch"] = 0.0
        indicator._target_values["pitch"] = 5.0
        indicator._field_velocities["pitch"] = 4.0
        indicator._field_sample_periods["pitch"] = 1.0
        indicator._field_sample_ages["pitch"] = 0.0

        frames: list[float] = []
        projected_targets: list[float] = []
        for _ in range(20):
            indicator._animation_step(0.05)
            frames.append(indicator._display_values["pitch"])
            projected_targets.append(indicator._projected_target("pitch", 5.0))

        self.assertTrue(
            all(current > previous for previous, current in zip(frames, frames[1:]))
        )
        self.assertTrue(
            all(
                current > previous
                for previous, current in zip(
                    projected_targets,
                    projected_targets[1:],
                )
            )
        )
        self.assertAlmostEqual(indicator._field_sample_ages["pitch"], 1.0)
        self.assertGreater(projected_targets[-1], 5.0)
        self.assertLessEqual(
            max(projected_targets),
            5.0
            + 4.0
            * (
                indicator._MAX_PREDICTION_HORIZON_S
                + indicator._MAX_PREDICTION_BRAKE_TIME_S
            ),
        )
        indicator.close()
        indicator.deleteLater()
        self.app.processEvents()

    def test_flight_dynamics_prioritizes_latest_sample_over_long_smoothing(self) -> None:
        indicator = AttitudeIndicator(DARK)
        indicator._display_values["pitch"] = 0.0
        indicator._target_values["pitch"] = 12.0
        indicator._field_velocities["pitch"] = 0.0
        indicator._field_sample_periods["pitch"] = 1.0

        indicator._animation_step(0.1)

        self.assertGreater(indicator._display_values["pitch"], 9.0)
        self.assertLessEqual(indicator._MAX_SMOOTHING_TIME_S, 0.07)
        indicator.close()
        indicator.deleteLater()
        self.app.processEvents()

    def test_flight_dynamics_learns_first_real_sample_period_directly(self) -> None:
        indicator = AttitudeIndicator(DARK)
        snapshot = TelemetrySnapshot.zero().copy(
            telemetry_link=True,
            autopilot_link=True,
            pitch=1.0,
        )
        indicator.update_snapshot(snapshot)
        indicator._field_last_sample_ms["pitch"] = (
            indicator._frame_clock.elapsed() - 1_000
        )
        indicator.update_snapshot(snapshot.copy(pitch=3.0))

        self.assertTrue(indicator._field_period_initialized["pitch"])
        self.assertAlmostEqual(
            indicator._field_sample_periods["pitch"],
            1.0,
            delta=0.02,
        )
        self.assertGreater(indicator._field_velocities["pitch"], 0.0)
        indicator.close()
        indicator.deleteLater()
        self.app.processEvents()

    def test_unrelated_snapshots_do_not_pause_flight_dynamics(self) -> None:
        indicator = AttitudeIndicator(DARK)
        snapshot = TelemetrySnapshot.zero().copy(
            telemetry_link=True,
            autopilot_link=True,
            pitch=2.0,
        )
        indicator.update_snapshot(snapshot)
        indicator._field_last_sample_ms["pitch"] = (
            indicator._frame_clock.elapsed() - 1_000
        )
        indicator.update_snapshot(snapshot.copy(pitch=6.0))
        indicator._animation_step(0.2)
        age_before = indicator._field_sample_ages["pitch"]
        velocity_before = indicator._field_velocities["pitch"]

        for packet_count in range(1, 8):
            indicator.update_snapshot(snapshot.copy(pitch=6.0, packets_in=packet_count))
            indicator._animation_step(0.05)

        self.assertGreater(indicator._field_sample_ages["pitch"], age_before)
        self.assertEqual(indicator._field_velocities["pitch"], velocity_before)
        self.assertGreater(indicator._display_values["pitch"], 6.0)
        indicator.close()
        indicator.deleteLater()
        self.app.processEvents()

    def test_flight_dynamics_uses_source_timing_and_average_jitter(self) -> None:
        indicator = AttitudeIndicator(DARK)
        started_at = datetime.now(UTC)
        snapshot = TelemetrySnapshot.zero().copy(
            telemetry_link=True,
            autopilot_link=True,
            pitch=0.0,
            attitude_received_at=started_at,
        )
        indicator.update_snapshot(snapshot)
        indicator.update_snapshot(
            snapshot.copy(
                pitch=2.0,
                attitude_received_at=started_at + timedelta(milliseconds=50),
            )
        )
        indicator.update_snapshot(
            snapshot.copy(
                pitch=4.4,
                attitude_received_at=started_at + timedelta(milliseconds=110),
            )
        )

        self.assertAlmostEqual(
            indicator._field_sample_periods["pitch"],
            0.052,
            delta=0.001,
        )
        self.assertGreater(indicator._field_sample_jitter_s["pitch"], 0.0)
        age_before = indicator._field_sample_ages["pitch"]
        indicator.update_snapshot(
            snapshot.copy(
                pitch=4.4,
                packets_in=99,
                attitude_received_at=started_at + timedelta(milliseconds=110),
            )
        )
        self.assertEqual(indicator._field_sample_ages["pitch"], age_before)
        indicator.close()
        indicator.deleteLater()
        self.app.processEvents()

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

    def test_actuator_test_card_is_discovered_and_emits_only_task_start_intent(self) -> None:
        panel = CommandPanel("", "")
        panel.update_snapshot(
            TelemetrySnapshot.zero().copy(
                telemetry_link=True,
                autopilot_link=True,
                armed=False,
            )
        )
        panel.update_onboard_state(
            OnboardMissionState(
                connected=True,
                onboard_version="0.2-beta",
                available_tasks=(ACTUATOR_TEST_TASK,),
            )
        )
        self.assertFalse(panel.actuator_card.isHidden())

        requested: list[tuple[str, dict]] = []
        panel.command_requested.connect(
            lambda code, params: requested.append((code, params))
        )
        actuator_row = next(
            row
            for row in range(panel.task_list.count())
            if panel.task_list.item(row).data(Qt.ItemDataRole.UserRole)
            == "ACTUATOR_TEST"
        )
        panel.task_list.setCurrentRow(actuator_row)
        panel._emit_task_command()
        self.assertEqual(
            requested,
            [("SET_MISSION_TASK", {"task": "ACTUATOR_TEST"})],
        )
        self.assertNotIn("pwm", requested[0][1])
        self.assertNotIn("armed", requested[0][1])

        panel.update_onboard_state(
            OnboardMissionState(
                connected=True,
                active_task=ACTUATOR_TEST_TASK,
                available_tasks=(ACTUATOR_TEST_TASK,),
                mission_id=84,
                phase="running",
                actuator_step="servo-1-high",
                actuator_step_count=1,
                actuator_servo_output=1,
                actuator_pwm=1700,
                actuator_armed=False,
            )
        )
        self.assertTrue(panel.task_apply_button.isEnabled())
        self.assertEqual(panel.task_apply_button.text(), "GÖREVİ YENİDEN BAŞLAT")
        panel._emit_task_command()
        self.assertEqual(
            requested[-1],
            ("RESTART_MISSION_TASK", {"task": "ACTUATOR_TEST"}),
        )
        self.assertIn("84", panel.actuator_detail.text())
        self.assertIn("1700", panel.actuator_detail.text())
        self.assertGreater(panel.actuator_progress.maximum(), 0)

        panel.update_onboard_state(
            OnboardMissionState(
                connected=True,
                active_task=ACTUATOR_TEST_TASK,
                available_tasks=(ACTUATOR_TEST_TASK,),
                mission_id=84,
                phase="succeeded",
                actuator_notification_only=True,
                actuator_servo_output=0,
                actuator_pwm=0,
                actuator_armed=False,
            )
        )
        self.assertIn(
            "Bildirim testi başarılı",
            panel.actuator_status.text(),
        )
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
        self.assertEqual(
            popup.summary_label.text(),
            "●  Canlı MAVLink telemetrisi alınıyor",
        )
        self.assertEqual(popup.reconnect_button.text(), "Yeniden Bağlan")
        self.assertTrue(popup.disconnect_button.isEnabled())
        popup.set_language("en")
        self.assertEqual(popup.title_label.text(), "TELEMETRY CONNECTION")
        self.assertEqual(
            popup.summary_label.text(),
            "●  Receiving live MAVLink telemetry",
        )
        self.assertEqual(popup.reconnect_button.text(), "Reconnect")
        popup.close()
        popup.deleteLater()
        self.app.processEvents()

    def test_video_popup_exposes_and_submits_auto_connect(self) -> None:
        popup = ConnectionPopup(DARK)
        requested: list[tuple[str, dict]] = []
        popup.reconnect_requested.connect(
            lambda channel, options: requested.append((channel, options))
        )
        popup.show_status(
            ConnectionStatus(
                "video",
                False,
                "Video akışı bekliyor",
                endpoint="/dev/video0",
                timeout_s=3,
                options={
                    "source_type": "usb",
                    "codec": "mjpeg",
                    "latency_ms": 50,
                    "auto_connect": True,
                },
            )
        )

        self.assertFalse(popup.auto_connect.isHidden())
        self.assertTrue(popup.auto_connect.isChecked())
        self.assertIn("video akışına", popup.auto_connect.text())
        popup.auto_connect.setChecked(False)
        popup.reconnect_button.click()

        self.assertEqual(requested[-1][0], "video")
        self.assertFalse(requested[-1][1]["auto_connect"])
        popup.close()
        popup.deleteLater()
        self.app.processEvents()

    def test_map_contract_exposes_all_configured_tools_and_backend_api(self) -> None:
        html = (ROOT / "src/ui/web/map.html").read_text(encoding="utf-8")
        panel_source = (ROOT / "src/ui/widgets/map_panel.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("window.ykiMap = {", html)
        self.assertNotIn("window.kuzeyMap", html)
        for method in (
            "setUiConfig(config)",
            "setOverlays(items)",
            "applyStateDelta(delta)",
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
        self.assertIn("(changes.upsertContacts || []).forEach", html)
        self.assertIn("function renderMarkerMotions(now)", html)
        self.assertIn("function startMarkerMotion(", html)
        self.assertIn("state.contacts.forEach((entry) =>", html)
        self.assertIn("Math.min(1200, sampleIntervalMs * 0.9)", html)
        self.assertIn("Math.min(140, sampleIntervalMs * 0.75)", html)
        self.assertEqual(html.count("requestAnimationFrame(renderMarkerMotions)"), 2)
        self.assertIn('"upsertContacts": changed_contacts', panel_source)
        self.assertIn("window.ykiMap.applyStateDelta(", panel_source)
        self.assertNotIn("window.ykiMap.upsertContact(", panel_source)
        self.assertIn("function beginOverlayBatch()", html)
        self.assertIn("function endOverlayBatch()", html)
        self.assertIn("function requestOverlayRefresh()", html)
        self.assertIn("beginOverlayBatch();", html)
        self.assertIn("endOverlayBatch();", html)
        self.assertIn('id="vehicle-readout"', html)
        self.assertIn('id="gps-summary"', html)
        self.assertIn('id="gps-satellites"', html)
        self.assertIn('id="gps-hdop"', html)
        self.assertIn('id="scale-readout"', html)
        self.assertIn("function updateScaleReadout()", html)
        self.assertIn("map.on('resize', updateScaleReadout);", html)
        self.assertNotIn("L.control.scale(", html)
        self.assertIn("container.id = 'mission-mode-toggle'", html)
        self.assertIn("container.appendChild(buildMissionModeToggle());", html)
        self.assertIn("const TOOL_DOCK_MIN_GAP_PX = 4", html)
        self.assertIn("const TOOL_DOCK_MAX_GAP_PX = 32", html)
        self.assertIn("gap: var(--tool-group-gap)", html)
        self.assertIn("function updateToolDockSpacing()", html)
        self.assertIn("sections.length - 1", html)
        self.assertIn("new ResizeObserver(scheduleToolDockSpacing)", html)
        self.assertEqual(html.count("<button data-action="), 4)
        for action in ("goto", "center", "set_home", "delete"):
            self.assertIn(f'<button data-action="{action}"></button>', html)
        for action in ("takeoff", "waypoint", "loiter", "land"):
            self.assertNotIn(f'<button data-action="{action}"></button>', html)
        self.assertIn(
            "function showContextMenu(containerPoint, latlng, overlayId = null)",
            html,
        )
        self.assertIn("contextOverlayId = overlayId;", html)
        self.assertIn("POINT_TYPES.has(selected.overlayData.overlay_type)", html)
        self.assertIn("const deletable = Boolean(", html)
        self.assertIn("['goto', 'center', 'set_home'].forEach", html)
        self.assertIn("selected && !deletable", html)
        self.assertIn("removeOverlayById(contextOverlayId, true);", html)
        self.assertIn("let homeReferenceMarker = null;", html)
        self.assertIn("function createHomeReferenceMarker(latlng)", html)
        self.assertIn("homeReferenceMarker = createHomeReferenceMarker", html)
        self.assertIn("draggable: true", html)
        self.assertIn("state.homeReferenceRollbackLatLng,", html)
        self.assertIn("type: 'set_home_reference'", html)
        self.assertIn("resolveHomeReference(success, latitude = null", html)
        self.assertIn("homeReferenceRollbackLatLng: null", html)
        self.assertIn("homeReferenceCommandedLatLng: null", html)
        self.assertIn("homeReferenceDragging: false", html)
        self.assertIn("state.homeReferenceDragging = true;", html)
        self.assertIn("state.homeReferenceDragging = false;", html)
        self.assertIn("if (state.homeReferenceDragging)", html)
        self.assertIn("function bindHistoryShortcuts()", html)
        self.assertIn("event.shiftKey ? 'history_redo' : 'history_undo'", html)
        self.assertIn("type: 'history_batch_begin'", html)
        self.assertIn("type: 'history_batch_end'", html)
        self.assertIn("prepareHomeHistoryReference(latitude, longitude)", html)
        self.assertIn("type: 'history_home_command'", html)
        self.assertIn("previous_lat: rollback.lat", html)
        self.assertIn("latitude !== null", html)
        self.assertIn("const restorePosition = fallbackIsValid", html)
        self.assertIn("!map.hasLayer(homeReferenceMarker)", html)
        self.assertIn("updateHomeReference(payload || {});", html)
        self.assertIn("button.className = 'mission-mode-button'", html)
        self.assertIn("window.ykiMap.setMissionLoop(!state.missionLoop)", html)
        self.assertIn("refreshMissionRoute();", html)
        self.assertIn("repeatablePoints.push(...layerPoints)", html)
        self.assertIn("!hasTerminalItem", html)
        self.assertIn("let rtlReturnLine = null;", html)
        self.assertIn("if (type === 'rtl')", html)
        self.assertIn("rtlDestination = homeReferenceLatLng();", html)
        self.assertIn("rtlReturnLine.setLatLngs(", html)
        self.assertIn("dashArray: '7 6'", html)
        self.assertIn("syncRtlMarkersToHome();", html)
        self.assertIn("button.innerHTML = iconSvg(mode)", html)
        self.assertIn("type: 'mission_loop_changed'", html)
        self.assertIn("loop: state.missionLoop", html)
        self.assertIn("toggleMissionDialog();", html)
        self.assertIn('class="point-drag-handle"', html)
        self.assertIn('draggable="true"', html)
        self.assertIn("installPointListReordering(container)", html)
        self.assertIn("const routeOrders = new Map(", html)
        self.assertNotIn('input data-field="order"', html)
        self.assertIn("updateGpsSummary(payload || {});", html)
        self.assertIn("state.lastVehicleLatLng.distanceTo(latlng)", html)
        self.assertIn("function installMiddleButtonMapPan()", html)
        self.assertIn("installMiddleButtonMapPan();", html)
        self.assertIn("draggable._onDown(event);", html)
        self.assertIn("if (!isPrimaryPointerEvent(event))", html)
        self.assertIn("function isDraggablePointLayer(layer)", html)
        self.assertIn("normalizeType(layer.overlayData?.overlay_type) !== 'rtl'", html)
        self.assertIn("marker.on('contextmenu'", html)
        self.assertIn("document.addEventListener('mousemove', move, true)", html)
        self.assertIn("window.requestAnimationFrame(renderPendingPosition)", html)
        self.assertGreaterEqual(html.count("bubblingMouseEvents: false"), 4)
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
