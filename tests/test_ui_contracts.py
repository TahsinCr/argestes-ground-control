from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QLineEdit,
    QMenu,
    QSpinBox,
    QWidget,
)

from src.models.commands import CommandRequest, CommandResult
from src.models.connections import ConnectionStatus
from src.models.logs import LogEntry
from src.models.mission import MapContact
from src.models.parameters import Parameter
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.ui.i18n import t
from src.ui.config.map import MAP_TOOLS
from src.ui.config.settings_schema import iter_fields
from src.ui.themes.palette import DARK
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.attitude import AttitudeIndicator
from src.ui.widgets.control_panels import TelemetryPanel
from src.ui.widgets.common import BatteryMeter, SignalMeter
from src.ui.widgets.battery_popup import BatterySettingsPopup
from src.ui.widgets.logs import EventLogPanel
from src.ui.widgets.parameters import ParametersPanel
from src.ui.widgets.map_panel import MapPanel, MapWebBridge
from src.ui.widgets.activity_bar import ActivityBar
from src.ui.widgets.title_bar import AppTitleBar, FramelessResizeController
from src.container import create_container
from src.extension.host import ExtensionHost
from src.ui.container import UiContainer
from src.ui.views.main_window import MainWindow


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

    def test_disabled_telemetry_removes_only_its_connection_chip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            try:
                backend.resolve(ExtensionHost).set_enabled("telemetry", False)
            finally:
                backend.shutdown()
            backend = create_container(Path(directory))
            try:
                ui = UiContainer(backend.container, base_dir=Path(directory), theme=DARK)
                window = ui.resolve(MainWindow)
                footer = window.telemetry_status.parentWidget().layout()
                self.assertEqual(footer.indexOf(window.telemetry_status), -1)
                self.assertGreaterEqual(footer.indexOf(window.autopilot_status), 0)
                self.assertGreaterEqual(footer.indexOf(window.gps_status), 0)
                window.close()
                ui.shutdown()
                window.deleteLater()
                self.app.processEvents()
            finally:
                backend.shutdown()

    def test_workspace_focus_and_reset_keep_panels_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            try:
                ui = UiContainer(backend.container, base_dir=Path(directory), theme=DARK)
                window = ui.resolve(MainWindow)
                window.show()
                self.app.processEvents()
                self.assertIsNotNone(window.log_dock.titleBarWidget())
                window._focus_map()
                self.assertTrue(all(dock.isHidden() for dock in window.workspace.panels.values()))
                window._reset_workspace()
                self.assertFalse(window.log_dock.isHidden())
                self.assertFalse(window._flight_docks[0].isHidden())
                self.assertTrue(window.parameters_dock.isHidden())
                window._edit_layout_action.trigger()
                self.assertTrue(window.workspace.edit_mode)
                self.assertIsNone(window.log_dock.titleBarWidget())
                window._edit_layout_action.trigger()
                self.assertFalse(window.workspace.edit_mode)
                window.close()
                ui.shutdown()
                window.deleteLater()
                self.app.processEvents()
            finally:
                backend.shutdown()

    def test_main_title_bar_has_localized_empty_application_menus(self) -> None:
        window = QWidget()
        title_bar = AppTitleBar(window, DARK)
        title_bar.enable_main_menu("en")

        self.assertEqual(
            [action.text() for action in title_bar._menu_bar.actions()],
            ["File", "Edit", "Selection", "View", "Go", "Run", "Terminal", "Help"],
        )
        self.assertTrue(all(not action.menu().actions() for action in title_bar._menu_bar.actions()))

        title_bar.set_menu_language("tr")
        self.assertEqual(
            [action.text() for action in title_bar._menu_bar.actions()],
            ["Dosya", "Düzen", "Seçim", "Görünüm", "Git", "Çalıştır", "Terminal", "Yardım"],
        )
        title_bar.close()
        window.close()
        title_bar.deleteLater()
        window.deleteLater()
        self.app.processEvents()

    def test_activity_bar_controls_sidebar_and_bottom_panels(self) -> None:
        bar = ActivityBar("tr")
        views: list[str] = []
        bar.sidebar_view_requested.connect(views.append)
        sidebar_visibility: list[bool] = []
        panel_visibility: list[tuple[str, bool]] = []
        side_by_side: list[str] = []
        bar.flight_sidebar_toggled.connect(sidebar_visibility.append)
        bar.bottom_panel_toggled.connect(
            lambda panel, visible: panel_visibility.append((panel, visible))
        )
        bar.bottom_panel_side_by_side_requested.connect(side_by_side.append)
        bar.resize(48, 400)
        bar.show()
        self.app.processEvents()

        bar.extensions_button.click()
        self.assertEqual(views[-1], "extensions")
        self.assertFalse(bar.sidebar_button.isChecked())
        bar.sidebar_button.click()
        self.assertEqual(views[-1], "flight")
        self.assertFalse(bar.extensions_button.isChecked())
        bar.add_extension_view("example.dashboard", "Dashboard")
        bar.extension_view_buttons["example.dashboard"].click()
        self.assertEqual(views[-1], "example.dashboard")
        self.assertFalse(bar.sidebar_button.isChecked())
        bar.sidebar_button.click()
        sidebar_visibility.clear()

        self.assertEqual(bar.width(), 48)
        self.assertEqual(tuple(bar.buttons), ("logs", "parameters"))
        self.assertEqual(bar.sidebar_button.toolTip(), "Uçuş paneli")
        self.assertTrue(bar.sidebar_button.isChecked())
        self.assertTrue(bar.buttons["logs"].isChecked())
        self.assertFalse(bar.buttons["parameters"].isChecked())
        bar.sidebar_button.click()
        self.assertEqual(sidebar_visibility, [False])
        bar.buttons["parameters"].click()
        self.assertEqual(panel_visibility, [("parameters", True)])
        self.assertFalse(bar.buttons["logs"].isChecked())
        bar.buttons["parameters"].click()
        self.assertEqual(panel_visibility[-1], ("parameters", False))
        with patch.object(
            QMenu,
            "exec",
            new=lambda menu, _position: menu.actions()[0],
        ):
            context_event = QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse, QPoint(1, 1), QPoint(1, 1)
            )
            bar.buttons["parameters"].contextMenuEvent(context_event)
        self.assertEqual(side_by_side, ["parameters"])
        self.assertTrue(bar.buttons["parameters"].isChecked())

        bar.set_language("en")
        self.assertEqual(bar.buttons["parameters"].toolTip(), "Parameters")
        bar.close()
        bar.deleteLater()
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

    def test_signal_meter_refreshes_an_open_title_tooltip(self) -> None:
        meter = SignalMeter("TELEMETRY")
        meter.set_metric_tooltip("RF 75%")

        with (
            patch.object(meter, "_metric_tooltip_is_open", return_value=True),
            patch("src.ui.widgets.common.QToolTip.showText") as show_tooltip,
        ):
            meter.set_metric_tooltip("RF 82%")
            QTest.qWait(120)

        show_tooltip.assert_called_once()
        self.assertEqual(show_tooltip.call_args.args[1], "RF 82%")
        self.assertIs(show_tooltip.call_args.args[2], meter.label)
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
        self.assertFalse(hasattr(panel, "scroll_area"))
        panel.resize(320, 900)
        self.app.processEvents()
        self.assertTrue(panel.content_layout.parentWidget().isVisible())
        panel.close()
        panel.deleteLater()
        self.app.processEvents()

if __name__ == "__main__":
    unittest.main()
