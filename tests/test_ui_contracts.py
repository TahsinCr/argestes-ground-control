from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from extensions.battery.ui.meter import BatteryMeter
from extensions.battery.ui.settings_popup import BatterySettingsPopup
from extensions.flight_dynamics.ui.panel import AttitudeIndicator
from extensions.map_autonomy.ui.panel import MapPanel, MapWebBridge
from extensions.parameters.ui.panel import ParametersPanel
from extensions.telemetry_display.ui.panel import TelemetryPanel
from src.container import create_container
from src.extension.host import ExtensionHost
from src.models.logs import LogEntry
from src.models.mission import MapContact
from src.models.parameters import Parameter
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.ui.config.settings_schema import iter_fields
from src.ui.container import UiContainer
from src.ui.themes.manager import apply_theme
from src.ui.themes.palette import DARK, LIGHT
from src.ui.themes.stylesheet import build_stylesheet
from src.ui.views.main_window import MainWindow
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.activity_bar import ActivityBar
from src.ui.widgets.common import SignalMeter
from src.ui.widgets.logs import EventLogPanel
from src.ui.widgets.title_bar import AppTitleBar, FramelessResizeController

ROOT = Path(__file__).resolve().parents[1]


class UiContractTests(unittest.TestCase):
    def test_telemetry_custom_layout_uses_selected_order_and_no_grid_holes(self):
        panel = TelemetryPanel("", "", DARK)
        self.assertGreaterEqual(len(panel.readouts), 50)
        fields = ["battery_voltage", "latitude", "flight_mode"]
        panel.configure_layout({"fields": fields, "layout": "list", "grouped": False})
        grid = panel.section_grids["flight"]
        for row, key in enumerate(fields):
            self.assertIs(grid.itemAtPosition(row, 1).widget(), panel.readouts[key])
        panel.configure_layout({"fields": fields, "columns": 3, "grouped": False})
        for column, key in enumerate(fields):
            self.assertIs(
                grid.itemAtPosition(0, column * 2 + 1).widget(), panel.readouts[key]
            )
        panel.configure_layout({"fields": [], "columns": "bad"})
        self.assertTrue(all(value.isHidden() for value in panel.readouts.values()))
        panel.close()

    def test_telemetry_settings_preserve_field_order_and_unknown_configuration(self):
        from types import SimpleNamespace

        from extensions.telemetry_display.ui.settings import TelemetrySettingsPage
        from src.services.settings import SettingsService

        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            try:
                settings = backend.resolve(SettingsService)
                settings.update_extension(
                    "telemetry_display",
                    config={"fields": ["latitude", "battery_voltage"], "future": 42},
                )
                context = SimpleNamespace(services=backend)
                page = TelemetrySettingsPage(context)
                page.selected.setCurrentRow(0)
                page.move(1)
                page.columns.setValue(3)
                page.grouped.setChecked(False)
                page.apply()
                saved = settings.extension_config("telemetry_display")
                self.assertEqual(saved["fields"], ["battery_voltage", "latitude"])
                self.assertEqual(saved["columns"], 3)
                self.assertFalse(saved["grouped"])
                self.assertEqual(saved["future"], 42)
                page.deleteLater()
                self.app.processEvents()
            finally:
                backend.shutdown()

    def test_frameless_filter_ignores_construction_and_style_events(self):
        window = QWidget()
        controller = FramelessResizeController(window)

        class ConstructingWidget(QWidget):
            def window(self):
                raise AssertionError("Window queried during construction notification")

        probe = ConstructingWidget()
        try:
            for kind in (
                QEvent.Type.StyleChange,
                QEvent.Type.PaletteChange,
                QEvent.Type.ChildAdded,
                QEvent.Type.LayoutRequest,
            ):
                self.assertFalse(controller.eventFilter(probe, QEvent(kind)))
        finally:
            controller._remove_filter()
            window.deleteLater()
            probe.deleteLater()

    def test_initial_workspace_geometry_fills_split_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            ui = UiContainer(backend.container, base_dir=Path(directory), theme=DARK)
            window = ui.resolve(MainWindow)
            try:
                window.resize(1400, 900)
                window.show()
                QTest.qWait(100)
                window._size_default_docks()
                self.app.processEvents()
                panels = window.workspace.panels
                widths = [
                    panels[key].width()
                    for key in (
                        "flight_dynamics.view",
                        "telemetry_display.view",
                        "quick_commands.view",
                    )
                ]
                self.assertTrue(all(300 <= width <= 340 for width in widths), widths)
                self.assertLessEqual(max(widths) - min(widths), 1)
                self.assertGreater(
                    window.primary_dock.height(), panels["logs"].height()
                )
                self.assertAlmostEqual(
                    window.primary_dock.width(), panels["logs"].width(), delta=2
                )
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()
                ui.shutdown()
                backend.shutdown()

    def test_map_hot_toggle_and_joystick_config_preserve_other_panels(self):
        from types import SimpleNamespace

        from extensions.joystick.models import JoystickSettings, JoystickState
        from extensions.joystick.ui.dialog import JoystickDialog

        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            ui = UiContainer(backend.container, base_dir=Path(directory), theme=DARK)
            try:
                window = ui.resolve(MainWindow)
                window.show()
                self.app.processEvents()
                self.assertIsNotNone(window.primary_dock)
                window._apply_extension_change("map_autonomy", False)
                self.app.processEvents()
                self.assertIsNone(window.primary_dock)
                self.assertNotIn("map_autonomy.view", window.workspace.panels)
                self.assertIn("logs", window.workspace.panels)
                window._reset_workspace()
                window._apply_extension_change("map_autonomy", True)
                self.app.processEvents()
                self.assertIsNotNone(window.primary_dock)
                self.assertIn("map_autonomy.view", window.workspace.panels)
                fake = SimpleNamespace(
                    devices=lambda: (),
                    state=JoystickState(),
                    reconnect=lambda: None,
                    disconnect=lambda: None,
                )
                from src.services.settings import SettingsService

                settings = backend.resolve(SettingsService)
                dialog = JoystickDialog(settings, fake, "tr", DARK, window)
                config = JoystickSettings.from_config(
                    {"channels": [{"channel": 1, "control": "axis:0"}]}
                )
                dialog._persist(config)
                self.assertEqual(
                    settings.extension_config("joystick")["channels"][0]["control"],
                    "axis:0",
                )
                dialog.close()
                dialog.deleteLater()
                window.close()
                window.deleteLater()
                self.app.processEvents()
            finally:
                ui.shutdown()
                backend.shutdown()

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["yki-ui-tests"])

    def test_light_theme_uses_contrast_aware_surface_borders(self) -> None:
        stylesheet = build_stylesheet(LIGHT)
        self.assertIn(LIGHT.border_subtle, stylesheet)
        self.assertNotIn("rgba(255,255,255", stylesheet)
        apply_theme(self.app, "light")
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.HighlightedText).name(),
            "#ffffff",
        )
        apply_theme(self.app, "dark")

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
            self.assertIsInstance(
                dialog._widgets[field.path], expected_widget_types[field.kind]
            )
        dialog._widgets["ui.theme"].setCurrentIndex(1)
        self.assertEqual(dialog.to_settings().ui.theme, "light")
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_extension_settings_pages_are_lazy_embedded_and_single_entry(self):
        from PyQt6.QtWidgets import QDialogButtonBox

        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            ui = UiContainer(backend.container, base_dir=Path(directory), theme=DARK)
            window = ui.resolve(MainWindow)
            dialog = SettingsDialog(
                window._settings,
                "tr",
                DARK,
                window,
                extension_host=window.extension_host,
                current_settings=lambda: window.settings_service.current,
            )
            try:
                self.assertFalse(dialog._extension_pages)
                ids = [item.manifest.identifier for item in dialog._extension_items]
                for index, identifier in enumerate(ids, 1):
                    dialog.categories.setCurrentRow(index)
                    self.app.processEvents()
                    page = dialog._extension_pages[index]
                    self.assertFalse(page.isWindow(), identifier)
                    self.assertIs(page.window(), dialog, identifier)
                    self.assertTrue(
                        dialog.buttons.button(
                            QDialogButtonBox.StandardButton.Save
                        ).isHidden()
                    )
                    self.assertEqual(
                        dialog.buttons.button(
                            QDialogButtonBox.StandardButton.Cancel
                        ).text(),
                        "Kapat",
                    )
                dialog.categories.setCurrentRow(0)
                self.assertFalse(
                    dialog.buttons.button(
                        QDialogButtonBox.StandardButton.Save
                    ).isHidden()
                )
                self.assertIn("mavlink", ids)
                protocol_page = dialog._extension_pages[ids.index("mavlink") + 1]
                self.assertIn("backend.mavlink.dialect", protocol_page._widgets)
                labels = [
                    button.text()
                    for button in window.extension_panel.findChildren(QPushButton)
                ]
                self.assertNotIn("Ayarlar", labels)
                self.assertNotIn("Settings", labels)
                self.assertFalse(window.extensions_button.icon().isNull())
                window.settings_service.backend.update_extension(
                    "telemetry_display", config={"fields": ["roll"]}
                )
                self.assertEqual(
                    dialog.to_settings().extensions["telemetry_display"]["config"][
                        "fields"
                    ],
                    ["roll"],
                )
            finally:
                dialog.reject()
                dialog.deleteLater()
                window.close()
                ui.shutdown()
                backend.shutdown()

    def test_category_selection_does_not_change_panel_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            ui = UiContainer(backend.container, base_dir=Path(directory), theme=DARK)
            window = ui.resolve(MainWindow)
            window.show()
            self.app.processEvents()
            before = {
                key: dock.geometry() for key, dock in window.workspace.panels.items()
            }
            window._begin_panel_selection("Tools", "SP_ComputerIcon")
            self.app.processEvents()
            selection = window._panel_selection
            key = next(
                key for key, dock in window.workspace.panels.items() if dock.isVisible()
            )
            surface = next(
                surface
                for surface in selection.surfaces
                if surface.parentWidget() is window.workspace.panels[key]
            )
            surface.click()
            selection.add.click()
            QTest.qWait(40)
            self.assertIsNone(window._panel_selection)
            self.assertEqual(
                window.workspace_views.active["categories"][-1]["panels"], [key]
            )
            self.assertEqual(
                {key: dock.geometry() for key, dock in window.workspace.panels.items()},
                before,
            )
            self.assertTrue(window.activity_bar.isEnabled())
            window.close()
            ui.shutdown()
            backend.shutdown()

    def test_mavlink_extension_settings_preserve_common_and_profile_values(
        self,
    ) -> None:
        from extensions.mavlink.ui.settings_dialog import MavlinkSettingsDialog

        original = SettingsModel()
        dialog = MavlinkSettingsDialog(original, "tr", DARK)
        self.assertIn("backend.mavlink.endpoint", dialog._widgets)
        self.assertNotIn("ui.theme", dialog._widgets)
        dialog._widgets["backend.mavlink.endpoint"].setText("udpin:0.0.0.0:14600")
        result = dialog.to_settings()
        self.assertEqual(result.backend.mavlink.endpoint, "udpin:0.0.0.0:14600")
        self.assertEqual(result.ui, original.ui)
        self.assertEqual(
            result.backend.active_mavlink_connection_id,
            original.backend.active_mavlink_connection_id,
        )
        active = next(
            item
            for item in result.backend.mavlink_connections
            if item.identifier == result.backend.active_mavlink_connection_id
        )
        self.assertEqual(active.endpoint, "udpin:0.0.0.0:14600")
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_teknofest_is_a_status_popup_not_a_workspace_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            ui = UiContainer(backend.container, base_dir=Path(directory), theme=DARK)
            window = ui.resolve(MainWindow)
            try:
                window._apply_extension_change("siha_teknofest_server", True)
                self.app.processEvents()
                self.assertFalse(
                    any(
                        key.startswith("siha_teknofest_server.")
                        for key in window.workspace.panels
                    )
                )
                owner = window._extension_status_by_id[
                    "siha_teknofest_server.connection"
                ]
                owner.indicator.clicked.emit()
                self.app.processEvents()
                self.assertIs(self.app.activePopupWidget(), owner.popup)
                from extensions.siha_teknofest_server.models import (
                    CompetitionServerConfig,
                )

                owner.panel.save_profiles(
                    [
                        CompetitionServerConfig(name="First"),
                        CompetitionServerConfig(name="Second"),
                    ]
                )
                owner.panel.pending.result(timeout=3)
                owner.panel.refresh()
                self.assertEqual(len(owner.panel.profiles), 2)
                self.assertEqual(len(owner.popup._servers), 2)
                owner.popup.close()
                window._apply_extension_change("siha_teknofest_server", False)
                self.assertNotIn(
                    "siha_teknofest_server.connection", window._extension_status_by_id
                )
            finally:
                window.close()
                ui.shutdown()
                window.deleteLater()
                self.app.processEvents()
                backend.shutdown()

    def test_connection_chip_opens_profiles_but_protocol_chips_are_status_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            try:
                ui = UiContainer(
                    backend.container, base_dir=Path(directory), theme=DARK
                )
                window = ui.resolve(MainWindow)
                connection = window._extension_status_by_id[
                    "vehicle_connection.connection"
                ].indicator
                indicators = (
                    window._extension_status_by_id["mavlink.gps"],
                    window._extension_status_by_id["mavlink.autopilot"],
                )
                for indicator in indicators:
                    self.assertEqual(indicator.receivers(indicator.clicked), 0)
                    indicator.clicked.emit()
                self.app.processEvents()
                self.assertIsNone(self.app.activePopupWidget())
                connection.clicked.emit()
                self.app.processEvents()
                owner = window._extension_status_by_id["vehicle_connection.connection"]
                self.assertIsNotNone(owner.editor)
                self.assertIs(self.app.activePopupWidget(), owner.editor.profiles_panel)
                owner.editor.profiles_panel.close()
                window.close()
                ui.shutdown()
                window.deleteLater()
                self.app.processEvents()
            finally:
                backend.shutdown()

    def test_disabled_connection_removes_only_its_connection_chip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = create_container(Path(directory))
            try:
                backend.resolve(ExtensionHost).set_enabled("vehicle_connection", False)
            finally:
                backend.shutdown()
            backend = create_container(Path(directory))
            try:
                ui = UiContainer(
                    backend.container, base_dir=Path(directory), theme=DARK
                )
                window = ui.resolve(MainWindow)
                footer = window.status_bar.layout()
                self.assertFalse(
                    any(
                        key.startswith("vehicle_connection.")
                        for key in window._extension_status_by_id
                    )
                )
                self.assertGreaterEqual(
                    footer.indexOf(window._extension_status_by_id["mavlink.autopilot"]),
                    0,
                )
                self.assertGreaterEqual(
                    footer.indexOf(window._extension_status_by_id["mavlink.gps"]), 0
                )
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
                ui = UiContainer(
                    backend.container, base_dir=Path(directory), theme=DARK
                )
                window = ui.resolve(MainWindow)
                window.show()
                self.app.processEvents()
                self.assertIn("map_autonomy.view", window.workspace.panels)
                self.assertIs(
                    window.workspace.panels["map_autonomy.view"], window.primary_dock
                )
                self.assertTrue(window.activity_bar.view_buttons)
                self.assertNotIn("extensions", window.workspace.panels)
                self.assertIsNotNone(window.log_dock.titleBarWidget())
                window._focus_map()
                self.assertTrue(
                    all(
                        dock.isHidden()
                        for key, dock in window.workspace.panels.items()
                        if key != "map_autonomy.view"
                    )
                )
                self.assertFalse(window.primary_dock.isHidden())
                window._reset_workspace()
                self.assertFalse(window.primary_dock.isHidden())
                self.assertFalse(window.log_dock.isHidden())
                self.assertFalse(window._flight_docks[0].isHidden())
                self.assertTrue(window.parameters_dock.isHidden())
                self.assertFalse(
                    any(
                        action.text() in {"Yerleşimi düzenle", "Edit Layout"}
                        for action in window.title_bar._menu_bar.actions()[3]
                        .menu()
                        .actions()
                    )
                )
                original = window.workspace_views.active_id
                identifier = window.workspace_views.create("Test", "SP_DesktopIcon")
                self.assertTrue(
                    all(dock.isHidden() for dock in window.workspace.panels.values())
                )
                self.assertEqual(window.workspace_views.active_id, identifier)
                window.workspace_views.switch(original)
                self.assertFalse(window.primary_dock.isHidden())
                window.open_extensions()
                self.app.processEvents()
                self.assertTrue(window.extension_dialog.isVisible())
                window.extension_dialog.hide()
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
        self.assertTrue(
            all(not action.menu().actions() for action in title_bar._menu_bar.actions())
        )

        title_bar.set_menu_language("tr")
        self.assertEqual(
            [action.text() for action in title_bar._menu_bar.actions()],
            [
                "Dosya",
                "Düzen",
                "Seçim",
                "Görünüm",
                "Git",
                "Çalıştır",
                "Terminal",
                "Yardım",
            ],
        )
        title_bar.close()
        window.close()
        title_bar.deleteLater()
        window.deleteLater()
        self.app.processEvents()

    def test_activity_bar_separates_views_and_dynamic_categories(self):
        from src.ui.widgets.panel_workspace import PanelWorkspace
        from src.ui.workspace_views import WorkspaceViews

        with tempfile.TemporaryDirectory() as directory:
            workspace = PanelWorkspace(Path(directory) / "layout.json")
            dock = workspace.add_panel(
                "logs", "Logs", QLabel("Logs"), Qt.DockWidgetArea.BottomDockWidgetArea
            )
            workspace.show()
            self.app.processEvents()
            controller = WorkspaceViews(workspace)
            controller.initialize(
                [
                    {
                        "id": "logs",
                        "name": "Loglar",
                        "icon": "SP_FileDialogListView",
                        "panels": ["logs"],
                    }
                ]
            )
            bar = ActivityBar("tr")
            bar.view_requested.connect(controller.switch)
            bar.category_requested.connect(controller.toggle_category)
            controller.changed.connect(lambda: bar.refresh(controller))
            bar.refresh(controller)
            bar.resize(48, 480)
            bar.show()
            self.app.processEvents()
            self.assertEqual(bar.width(), 48)
            self.assertEqual(len(bar.view_buttons), 1)
            bar.view_buttons[controller.active_id].click()
            self.assertTrue(bar.view_buttons[controller.active_id].isChecked())
            self.assertEqual(list(bar.category_buttons), ["logs"])
            bar.category_buttons["logs"].click()
            self.assertTrue(dock.isHidden())
            bar.category_buttons["logs"].click()
            self.assertFalse(dock.isHidden())
            initial = controller.active_id
            controller.create("Boş", "SP_DesktopIcon")
            self.assertEqual(len(bar.view_buttons), 2)
            self.assertFalse(bar.category_buttons)
            bar.view_buttons[initial].click()
            self.assertEqual(list(bar.category_buttons), ["logs"])
            self.assertFalse(dock.isHidden())
            bar.close()
            workspace.close()
            bar.deleteLater()

    def test_settings_dialog_validates_battery_voltage_fields_as_one_profile(self):
        dialog = BatterySettingsPopup(DARK, language="tr")
        dialog.show_settings(SettingsModel(), TelemetrySnapshot.zero())
        dialog.full_voltage.setValue(14.0)
        dialog.minimum_voltage.setValue(13.0)
        dialog.minimum_percent.setValue(12.0)
        payload = SettingsModel().to_dict()
        for key, value in dialog.values().items():
            if key in payload["backend"]["battery_telemetry"]:
                payload["backend"]["battery_telemetry"][key] = value
        updated = SettingsModel.from_dict(payload).backend.battery_telemetry
        self.assertEqual(updated.full_voltage, 14.0)
        self.assertEqual(updated.reserve_voltage, 13.0)
        self.assertEqual(updated.reserve_percent, 12.0)
        payload["backend"]["battery_telemetry"]["full_voltage"] = 12.0
        with self.assertRaises(ValueError):
            SettingsModel.from_dict(payload)
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
            # WebEngine cleanup can delay timers in the shared offscreen event
            # loop. Wait for the signal rather than assuming an exact tick.
            for _ in range(50):
                if show_tooltip.called:
                    break
                QTest.qWait(20)

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
            panel.table.item(row, 1).text() for row in range(panel.table.rowCount())
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
        self.assertEqual(list(panel._entries), [])
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
        with patch.object(EventLogPanel, "MAX_ENTRIES", 5):
            panel = EventLogPanel("", "", "")
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
        panel.set_parameters([Parameter("FIRST", 3.0, "-", "vehicle", ""), second])

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

    def test_idle_mouse_move_cancels_abandoned_window_resize_and_pending_updates(self):
        window = QWidget()
        window.resize(640, 480)
        window.show()
        self.app.processEvents()
        controller = FramelessResizeController(window, window)
        try:
            for edges in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
                before = window.geometry()
                start = window.mapToGlobal(QPoint(0, 120))
                controller._begin_manual_resize(edges, start)
                controller._queue_resize(start + QPoint(80, 0))
                idle = QMouseEvent(
                    QEvent.Type.MouseMove,
                    QPointF(160, 120),
                    QPointF(start + QPoint(160, 0)),
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                QApplication.sendEvent(window, idle)
                QTest.qWait(30)
                self.assertEqual(window.geometry(), before)
                self.assertFalse(controller._resizing)
                self.assertIsNone(controller._pending_resize_pos)
                self.assertFalse(controller._resize_timer.isActive())
        finally:
            controller._remove_filter()
            window.close()
            window.deleteLater()

    def test_release_outside_window_cancels_resize_without_applying_idle_position(self):
        window, outside = QWidget(), QWidget()
        window.resize(640, 480)
        window.show()
        outside.show()
        self.app.processEvents()
        controller = FramelessResizeController(window, window)
        try:
            before = window.geometry()
            start = window.mapToGlobal(QPoint(0, 120))
            controller._begin_manual_resize(Qt.Edge.LeftEdge, start)
            controller._queue_resize(start - QPoint(100, 0))
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(5, 5),
                QPointF(outside.mapToGlobal(QPoint(5, 5))),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(outside, release)
            QTest.qWait(30)
            self.assertEqual(window.geometry(), before)
            self.assertFalse(controller._resizing)
        finally:
            controller._remove_filter()
            for widget in (window, outside):
                widget.close()
                widget.deleteLater()

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
            abs(
                AttitudeIndicator._shortest_angle_delta(
                    indicator._display_values["heading"],
                    1.0,
                )
            ),
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

    def test_flight_dynamics_prioritizes_latest_sample_over_long_smoothing(
        self,
    ) -> None:
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

    def test_telemetry_panel_uses_consistent_flight_readouts_without_speed(
        self,
    ) -> None:
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
        self.assertTrue(panel.readouts["ground_speed"].isHidden())
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
