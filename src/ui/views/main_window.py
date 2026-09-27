from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.models.connections import ConnectionStatus
from src.extension.host import ExtensionHost
from src.models.settings import MavlinkConnectionConfig, SettingsModel
from src.services.abstracts.commands import CommandService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.parameters import ParameterService
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import LoggingService
from src.services.planner import PlannerService
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer
from src.ui.adapters.services import (
    QtCommandService,
    QtConnectionControlService,
    QtLoggingService,
    QtParameterService,
    QtPlannerService,
    QtSettingsService,
    QtTelemetryService,
    QtTileCacheService,
)
from src.ui.i18n import t
from src.ui.assets import application_icon
from src.ui.themes.style import ThemePalette, apply_theme
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.attitude import AttitudeIndicator
from src.ui.widgets.battery_popup import BatterySettingsPopup
from src.ui.widgets.common import BatteryMeter, PanelFrame, SignalMeter, StatusChip
from src.ui.widgets.control_panels import TelemetryPanel, VehicleControlsPanel
from src.ui.widgets.logs import EventLogPanel
from src.ui.widgets.map_panel import MapPanel
from src.ui.widgets.mavlink_connections_popup import MavlinkConnectionsPopup
from src.ui.widgets.parameters import ParametersPanel
from src.ui.widgets.activity_bar import ActivityBar
from src.ui.widgets.extension_panel import ExtensionPanel
from src.ui.widgets.title_bar import AppTitleBar
from src.ui.widgets.panel_workspace import PanelWorkspace


class MainWindow(QMainWindow):
    shutdown_requested = pyqtSignal()

    def __init__(
        self,
        *,
        log_service: QtLoggingService,
        settings_service: QtSettingsService,
        telemetry_service: QtTelemetryService,
        planner_service: QtPlannerService,
        command_service: QtCommandService,
        parameter_service: QtParameterService,
        connection_service: QtConnectionControlService,
        tile_cache_service: QtTileCacheService,
        base_dir: Path,
        theme: ThemePalette,
        extension_host: ExtensionHost,
    ) -> None:
        super().__init__()
        self.log_service = log_service
        self.settings_service = settings_service
        self.telemetry_service = telemetry_service
        self.planner_service = planner_service
        self.command_service = command_service
        self.parameter_service = parameter_service
        self.connection_service = connection_service
        self.tile_cache_service = tile_cache_service
        self.base_dir = base_dir
        self.extension_host = extension_host
        self.theme = theme
        self._settings = settings_service.current
        self.language = self._settings.language
        self._editing_profile: MavlinkConnectionConfig | None = None
        self._layout_restored = False
        self._workspace_disconnected = False

        self.setWindowTitle(t("app.title", self.language))
        self.setWindowIcon(application_icon(base_dir))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.resize(1600, 940)
        self.setMinimumSize(1152, 648)
        self._build_ui()
        self._connect_signals()
        self.parameters_panel.set_parameters(parameter_service.current)
        self.telemetry_panel.update_snapshot(telemetry_service.current)
        planner_service.set_default_radii(
            {
                "takeoff": self._settings.default_radius_takeoff,
                "waypoint": self._settings.default_radius_waypoint,
                "loiter": self._settings.default_radius_loiter,
                "rtl": self._settings.default_radius_rtl,
                "land": self._settings.default_radius_land,
            }
        )

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)

        self.title_bar = AppTitleBar(self, self.theme, central)
        self.title_bar.set_title(self.windowTitle())
        self.title_bar.enable_main_menu(self.language)
        layout.addWidget(self.title_bar)

        content = QWidget(central)
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.activity_bar = ActivityBar(self.language, content)
        content_layout.addWidget(self.activity_bar)
        self.workspace = PanelWorkspace(
            self.base_dir / "data" / "workspace-layout.json", content,
            language=self.language,
        )
        self._build_sidebar()
        self._build_workspace()
        self.sidebar = QStackedWidget(self.workspace)
        self.sidebar.setMinimumWidth(280)
        self.extension_panel = ExtensionPanel(self.extension_host, self.language, self.sidebar,
                                              apply_change=self._apply_extension_change,
                                              reload_extension=self._reload_extension)
        self.sidebar.addWidget(self.extension_panel)
        self._extension_sidebar_widgets: dict[str, QWidget] = {}
        for contribution in self.extension_host.sidebar_views():
            widget = contribution.factory(self.sidebar)
            if not isinstance(widget, QWidget):
                raise TypeError(f"Extension sidebar {contribution.identifier} must return QWidget")
            self.sidebar.addWidget(widget)
            self._extension_sidebar_widgets[contribution.identifier] = widget
            self.activity_bar.add_extension_view(contribution.identifier, contribution.title)
        self.extension_dock = self.workspace.add_panel(
            "extensions", "Eklentiler", self.sidebar,
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.extension_dock.hide()
        self.activity_bar.sidebar_view_requested.connect(self._show_sidebar_view)
        self.activity_bar.flight_sidebar_toggled.connect(self._toggle_flight_panels)
        self.activity_bar.bottom_panel_toggled.connect(self._on_bottom_panel_toggled)
        self.activity_bar.bottom_panel_side_by_side_requested.connect(
            self._open_bottom_panel_beside
        )
        content_layout.addWidget(self.workspace, 1)
        self.workspace.show()
        layout.addWidget(content, 1)
        self._build_view_menu()

        status_bar = QWidget(central)
        status_bar.setObjectName("BottomStatusBar")
        footer = QHBoxLayout(status_bar)
        footer.setContentsMargins(8, 3, 8, 3)
        footer.setSpacing(6)
        self.telemetry_status = StatusChip(t("status.telemetry", self.language), status_bar)
        self.autopilot_status = StatusChip(t("status.autopilot", self.language), status_bar)
        self.gps_status = StatusChip(t("status.gps", self.language), status_bar)
        self._telemetry_ui_enabled = self.extension_host.is_active("telemetry")
        for chip in (self.telemetry_status, self.autopilot_status, self.gps_status):
            chip.set_theme(self.theme)
            chip.set_state("danger")
            if chip is not self.telemetry_status or self._telemetry_ui_enabled:
                footer.addWidget(chip)
        self._extension_status_widgets: list[QWidget] = []
        self._extension_status_by_id: dict[str, QWidget] = {}
        for contribution in self.extension_host.status_widgets():
            widget = contribution.factory(status_bar, self.theme, self.language)
            if not isinstance(widget, QWidget):
                raise TypeError(f"Extension status {contribution.identifier} must return QWidget")
            footer.addWidget(widget)
            self._extension_status_widgets.append(widget)
            self._extension_status_by_id[contribution.identifier] = widget
        footer.addStretch(1)
        self.command_link_meter = SignalMeter(t("status.command_link", self.language), status_bar)
        self.command_link_meter.set_theme(self.theme)
        self.command_link_meter.set_unavailable()
        self.battery_meter = BatteryMeter(t("status.battery", self.language), status_bar)
        self.battery_meter.set_theme(self.theme)
        self.battery_meter.set_unavailable()
        self.battery_meter.clicked.connect(self._open_battery_popup)
        footer.addWidget(self.battery_meter)
        footer.addWidget(self.command_link_meter)
        self.settings_button = QPushButton("⚙", status_bar)
        self.settings_button.setObjectName("SettingsIconButton")
        self.settings_button.setProperty("compact", "true")
        self.settings_button.setFixedWidth(42)
        self.settings_button.setToolTip(t("status.settings", self.language))
        self.settings_button.clicked.connect(self.open_settings)
        footer.addWidget(self.settings_button)
        layout.addWidget(status_bar)

        self.battery_popup = BatterySettingsPopup(self.theme, self, language=self.language)
        self.battery_popup.save_requested.connect(self._save_battery_settings)
        self.battery_popup.settings_requested.connect(self.open_settings)
        self.mavlink_popup = MavlinkConnectionsPopup(
            self.theme, self, language=self.language
        )
        self._show_mavlink_profiles()

    def _build_sidebar(self) -> None:
        self.flight_panel = PanelFrame("", "", self.workspace)
        self.flight_panel.setMinimumWidth(260)
        self.attitude_indicator = AttitudeIndicator(self.theme, self.flight_panel)
        self.flight_panel.body_layout.addWidget(self.attitude_indicator, 1)
        self.telemetry_panel = TelemetryPanel("", "", self.theme, self.workspace)
        self.vehicle_controls = VehicleControlsPanel(
            self.command_service.available_flight_modes,
            language=self.language,
            parent=self.workspace,
        )
        self.vehicle_controls.command_requested.connect(self.command_service.issue_command)
        area = Qt.DockWidgetArea.LeftDockWidgetArea
        flight = self.workspace.add_panel("flight", "Araç Dinamiği", self.flight_panel, area)
        telemetry = self.workspace.add_panel("telemetry", "Araç Telemetrisi", self.telemetry_panel, area)
        controls = self.workspace.add_panel("commands", "Hızlı Komutlar", self.vehicle_controls, area)
        self.workspace.splitDockWidget(flight, telemetry, Qt.Orientation.Vertical)
        self.workspace.splitDockWidget(telemetry, controls, Qt.Orientation.Vertical)
        self._flight_docks = (flight, telemetry, controls)

    def _show_sidebar_view(self, view: str) -> None:
        if view == "flight":
            self.extension_dock.hide()
            for dock in self._flight_docks:
                dock.show()
            return
        if view:
            for dock in self._flight_docks:
                dock.hide()
        self.extension_dock.setVisible(bool(view))
        if view:
            self.workspace.resizeDocks(
                [self.extension_dock], [320], Qt.Orientation.Horizontal
            )
            self.sidebar.setCurrentWidget(
                self.extension_panel if view == "extensions" else
                self._extension_sidebar_widgets.get(view, self.extension_panel)
            )

    def _toggle_flight_panels(self, visible: bool) -> None:
        for dock in self._flight_docks:
            dock.setVisible(visible)

    def _apply_extension_change(self, identifier: str, enabled: bool) -> None:
        was_active = self.extension_host.is_active(identifier)
        self.extension_host.set_enabled(identifier, enabled)
        try:
            self._sync_extension_ui(identifier)
        except Exception:
            self._remove_extension_ui(identifier)
            self.extension_host.set_enabled(identifier, was_active)
            self._sync_extension_ui(identifier)
            raise

    def _reload_extension(self, identifier: str) -> None:
        self._remove_extension_ui(identifier)
        self.extension_host.reload(identifier)
        self._sync_extension_ui(identifier)

    def _sync_extension_ui(self, identifier: str) -> None:
        self._remove_extension_ui(identifier)
        if not self.extension_host.is_active(identifier):
            if identifier == "telemetry":
                self._telemetry_ui_enabled = False
                self.telemetry_status.hide()
                self.telemetry_status.parentWidget().layout().removeWidget(self.telemetry_status)
            return
        if identifier == "telemetry" and not self._telemetry_ui_enabled:
            self._telemetry_ui_enabled = True
            self.telemetry_status.parentWidget().layout().insertWidget(0, self.telemetry_status)
            self.telemetry_status.show()
        prefix = identifier + "."
        for contribution in self.extension_host.sidebar_views():
            if contribution.identifier.startswith(prefix):
                widget = contribution.factory(self.sidebar)
                if not isinstance(widget, QWidget):
                    raise TypeError("Extension sidebar must return QWidget")
                self.sidebar.addWidget(widget)
                self._extension_sidebar_widgets[contribution.identifier] = widget
                self.activity_bar.add_extension_view(contribution.identifier, contribution.title)
        for contribution in self.extension_host.visual_views():
            if not contribution.identifier.startswith(prefix):
                continue
            widget = contribution.factory(self.workspace, self.theme, self.language)
            if not isinstance(widget, QWidget):
                raise TypeError("Extension visual must return QWidget")
            preference = self._visual_dock_preferences.get(contribution.identifier)
            area = preference[0] if preference else Qt.DockWidgetArea.RightDockWidgetArea
            dock = self.workspace.add_panel(
                contribution.identifier, self._visual_title(contribution.identifier), widget,
                area,
            )
            if preference:
                dock.setFloating(preference[3])
            self._panel_menu.addAction(dock.toggleViewAction())
            self._extension_visual_widgets.append(widget)
            self._extension_visual_by_id[contribution.identifier] = widget
            QTimer.singleShot(
                0,
                lambda panel=dock, size=preference: self._size_visual_dock(panel, size),
            )
        footer = self.telemetry_status.parentWidget().layout()
        for contribution in self.extension_host.status_widgets():
            if not contribution.identifier.startswith(prefix):
                continue
            widget = contribution.factory(self.telemetry_status.parentWidget(), self.theme,
                                          self.language)
            if not isinstance(widget, QWidget):
                raise TypeError("Extension status must return QWidget")
            footer.insertWidget(footer.indexOf(self.battery_meter) - 1, widget)
            self._extension_status_widgets.append(widget)
            self._extension_status_by_id[contribution.identifier] = widget

    def _remove_extension_ui(self, identifier: str) -> None:
        prefix = identifier + "."
        for key, widget in tuple(self._extension_status_by_id.items()):
            if key.startswith(prefix):
                widget.parentWidget().layout().removeWidget(widget)
                widget.hide()
                widget.deleteLater()
                self._extension_status_widgets.remove(widget)
                del self._extension_status_by_id[key]
        for key, widget in tuple(self._extension_visual_by_id.items()):
            if key.startswith(prefix):
                shutdown = getattr(widget, "prepare_shutdown", None)
                if callable(shutdown):
                    shutdown()
                dock = self.workspace.panels.get(key)
                if dock is not None:
                    self._visual_dock_preferences[key] = (
                        self.workspace.dockWidgetArea(dock),
                        dock.width(),
                        dock.height(),
                        dock.isFloating(),
                    )
                    self._panel_menu.removeAction(dock.toggleViewAction())
                self.workspace.remove_panel(key)
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                self._extension_visual_widgets.remove(widget)
                del self._extension_visual_by_id[key]
        for key, widget in tuple(self._extension_sidebar_widgets.items()):
            if key.startswith(prefix):
                if self.sidebar.currentWidget() is widget:
                    self.sidebar.setCurrentWidget(self.extension_panel)
                    self.activity_bar.extensions_button.setChecked(True)
                self.sidebar.removeWidget(widget)
                self.activity_bar.remove_extension_view(key)
                widget.deleteLater()
                del self._extension_sidebar_widgets[key]

    def _build_workspace(self) -> None:
        self.map_panel = MapPanel(
            "", "", theme=self.theme, language=self.language,
            base_dir=self.base_dir, planner_service=self.planner_service,
            settings_service=self.settings_service,
            tile_cache_service=self.tile_cache_service, parent=self.workspace,
        )
        self.workspace.setCentralWidget(self.map_panel)
        self._extension_visual_widgets: list[QWidget] = []
        self._extension_visual_by_id: dict[str, QWidget] = {}
        self._visual_dock_preferences: dict[str, tuple[Qt.DockWidgetArea, int, int, bool]] = {}
        self.log_panel = EventLogPanel("", "", "", self.workspace)
        self.parameters_panel = ParametersPanel("", "", "", self.workspace)
        bottom = Qt.DockWidgetArea.BottomDockWidgetArea
        self.log_dock = self.workspace.add_panel("logs", "Loglar", self.log_panel, bottom)
        self.parameters_dock = self.workspace.add_panel(
            "parameters", "Parametreler", self.parameters_panel, bottom
        )
        self.workspace.tabifyDockWidget(self.log_dock, self.parameters_dock)
        self.parameters_dock.hide()
        for contribution in self.extension_host.visual_views():
            widget = contribution.factory(self.workspace, self.theme, self.language)
            if not isinstance(widget, QWidget):
                raise TypeError(f"Extension visual {contribution.identifier} must return QWidget")
            self.workspace.add_panel(
                contribution.identifier, self._visual_title(contribution.identifier), widget,
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            self._extension_visual_widgets.append(widget)
            self._extension_visual_by_id[contribution.identifier] = widget

    def _on_bottom_panel_toggled(self, panel: str, checked: bool) -> None:
        selected = {
            "logs": self.log_dock,
            "parameters": self.parameters_dock,
        }.get(panel)
        if selected is None:
            return
        if checked:
            other_key = "parameters" if panel == "logs" else "logs"
            other = self.parameters_dock if panel == "logs" else self.log_dock
            self.activity_bar.set_bottom_panel_checked(other_key, False)
            other.hide()
            selected.show()
            selected.raise_()
        else:
            selected.hide()

    def _open_bottom_panel_beside(self, panel: str) -> None:
        selected = {
            "logs": self.log_dock,
            "parameters": self.parameters_dock,
        }.get(panel)
        other = self.parameters_dock if panel == "logs" else self.log_dock
        other_key = "parameters" if panel == "logs" else "logs"
        if selected is None:
            return
        if not other.isVisible():
            self._on_bottom_panel_toggled(panel, True)
            return
        self.activity_bar.set_bottom_panel_checked(panel, True)
        self.activity_bar.set_bottom_panel_checked(other_key, True)
        self.workspace.splitDockWidget(other, selected, Qt.Orientation.Horizontal)
        selected.show()

    def _build_view_menu(self) -> None:
        menu = self.title_bar._menu_bar.actions()[3].menu()
        self._panel_menu = menu.addMenu("Paneller")
        for dock in self.workspace.panels.values():
            self._panel_menu.addAction(dock.toggleViewAction())
        menu.addSeparator()
        self._edit_layout_action = menu.addAction("Yerleşimi düzenle")
        self._edit_layout_action.setCheckable(True)
        self._edit_layout_action.toggled.connect(self._set_layout_edit_mode)
        self._edit_layout_action.setShortcut("Ctrl+Alt+L")
        self._focus_action = menu.addAction("Haritaya odaklan", self._focus_map)
        self._reset_action = menu.addAction("Varsayılan yerleşimi geri yükle", self._reset_workspace)
        self._theme_menu = menu.addMenu("Tema")
        self._dark_action = self._theme_menu.addAction(
            "Koyu", lambda: self._set_theme_choice("dark")
        )
        self._light_action = self._theme_menu.addAction(
            "Açık", lambda: self._set_theme_choice("light")
        )
        self._retranslate_view_menu()

    def _visual_title(self, identifier: str) -> str:
        name = identifier.split(".")[0]
        if name == "camera":
            return "Camera" if self.language == "en" else "Kamera"
        return name.replace("_", " ").title()

    def _retranslate_view_menu(self) -> None:
        english = self.language == "en"
        self._panel_menu.setTitle("Panels" if english else "Paneller")
        self._edit_layout_action.setText(
            "Edit Layout" if english else "Yerleşimi düzenle"
        )
        self._focus_action.setText("Focus Map" if english else "Haritaya odaklan")
        self._reset_action.setText(
            "Reset Layout" if english else "Varsayılan yerleşimi geri yükle"
        )
        self._theme_menu.setTitle("Theme" if english else "Tema")
        self._dark_action.setText("Dark" if english else "Koyu")
        self._light_action.setText("Light" if english else "Açık")
        self.workspace.language = self.language
        labels = {
            "flight": "Flight Dynamics" if english else "Araç Dinamiği",
            "telemetry": "Vehicle Telemetry" if english else "Araç Telemetrisi",
            "commands": "Quick Commands" if english else "Hızlı Komutlar",
            "logs": "Logs" if english else "Loglar",
            "parameters": "Parameters" if english else "Parametreler",
            "extensions": "Extensions" if english else "Eklentiler",
        }
        for key, dock in self.workspace.panels.items():
            dock.setWindowTitle(labels.get(key, self._visual_title(key)))

    def _set_layout_edit_mode(self, enabled: bool) -> None:
        self.workspace.set_edit_mode(enabled)

    def _set_theme_choice(self, theme_name: str) -> None:
        if self._settings.theme == theme_name:
            return
        payload = self._settings.to_dict()
        payload["ui"]["theme"] = theme_name
        self.settings_service.save_async(SettingsModel.from_dict(payload))

    def _focus_map(self) -> None:
        for dock in self.workspace.panels.values():
            dock.hide()
        self.activity_bar.sidebar_button.setChecked(False)
        self.activity_bar.extensions_button.setChecked(False)
        for key in self.activity_bar.buttons:
            self.activity_bar.set_bottom_panel_checked(key, False)

    def _reset_workspace(self) -> None:
        for dock in self.workspace.panels.values():
            dock.setFloating(False)
        for dock in self._flight_docks:
            self.workspace.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
            dock.show()
        self.workspace.splitDockWidget(self._flight_docks[0], self._flight_docks[1], Qt.Orientation.Vertical)
        self.workspace.splitDockWidget(self._flight_docks[1], self._flight_docks[2], Qt.Orientation.Vertical)
        self.workspace.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.workspace.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.parameters_dock)
        self.workspace.tabifyDockWidget(self.log_dock, self.parameters_dock)
        self.log_dock.show()
        self.parameters_dock.hide()
        self.extension_dock.hide()
        for key, dock in self.workspace.panels.items():
            if key not in {"flight", "telemetry", "commands", "logs", "parameters", "extensions"}:
                self.workspace.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
                dock.show()
        self.activity_bar.sidebar_button.setChecked(True)
        self.activity_bar.extensions_button.setChecked(False)
        self.activity_bar.set_bottom_panel_checked("logs", True)
        self.activity_bar.set_bottom_panel_checked("parameters", False)
        self._size_default_docks()
        self.workspace.save_layout()

    def _size_default_docks(self) -> None:
        self.workspace.resizeDocks(
            [self._flight_docks[0], self.log_dock],
            [205, 260],
            Qt.Orientation.Vertical,
        )
        visible_right = [
            dock for key, dock in self.workspace.panels.items()
            if key not in {"flight", "telemetry", "commands", "logs", "parameters", "extensions"}
            and dock.isVisible()
        ]
        if visible_right:
            self.workspace.resizeDocks(
                [self._flight_docks[0], visible_right[0]],
                [320, max(380, self.workspace.width() // 3)],
                Qt.Orientation.Horizontal,
            )

    def _size_visual_dock(
        self,
        dock,
        preference: tuple[Qt.DockWidgetArea, int, int, bool] | None,
    ) -> None:
        if dock not in self.workspace.panels.values() or dock.isFloating():
            return
        area = self.workspace.dockWidgetArea(dock)
        orientation = (
            Qt.Orientation.Horizontal
            if area in (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea)
            else Qt.Orientation.Vertical
        )
        size = preference[1 if orientation == Qt.Orientation.Horizontal else 2] if preference else (
            max(380, self.workspace.width() // 3)
            if orientation == Qt.Orientation.Horizontal else max(240, self.workspace.height() // 3)
        )
        self.workspace.resizeDocks([dock], [size], orientation)

    def _restore_workspace(self) -> None:
        if self._workspace_disconnected:
            return
        if not self.workspace.restore_layout():
            self._size_default_docks()
        self._sync_activity_buttons()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._layout_restored:
            self._layout_restored = True
            QTimer.singleShot(0, self._restore_workspace)

    def _sync_activity_buttons(self) -> None:
        self.activity_bar.sidebar_button.setChecked(
            any(not dock.isHidden() for dock in self._flight_docks)
        )
        self.activity_bar.extensions_button.setChecked(not self.extension_dock.isHidden())
        self.activity_bar.set_bottom_panel_checked("logs", not self.log_dock.isHidden())
        self.activity_bar.set_bottom_panel_checked("parameters", not self.parameters_dock.isHidden())

    def _connect_signals(self) -> None:
        for dock in (*self._flight_docks, self.extension_dock, self.log_dock, self.parameters_dock):
            dock.visibilityChanged.connect(lambda _visible: self._sync_activity_buttons())
        self.telemetry_service.telemetry_updated.connect(self._on_telemetry)
        self.parameter_service.parameters_changed.connect(self.parameters_panel.set_parameters)
        self.parameters_panel.parameter_changed.connect(self._on_parameter_changed)
        self.parameters_panel.apply_requested.connect(self.parameter_service.update_values)
        self.parameters_panel.reset_requested.connect(self.parameter_service.refresh)
        self.map_panel.command_requested.connect(self.command_service.issue_command)
        self.map_panel.info_generated.connect(
            lambda severity, message: self.log_service.log("MAP", message, severity)
        )
        self.log_service.entries_emitted.connect(self.log_panel.add_entries)
        self.connection_service.status_changed.connect(self._on_connection_status)
        self.telemetry_status.clicked.connect(self._open_mavlink_profiles)
        self.autopilot_status.clicked.connect(self._open_mavlink_profiles)
        self.gps_status.clicked.connect(self._open_mavlink_profiles)
        self.mavlink_popup.profiles_saved.connect(self._save_mavlink_profiles)
        self.mavlink_popup.profile_edit_requested.connect(self._edit_mavlink_profile)
        self.mavlink_popup.profile_connect_requested.connect(self._connect_mavlink_profile)
        self.settings_service.settings_changed.connect(self._settings_changed)
        self.command_service.command_dispatched.connect(
            lambda code, parameters: self.vehicle_controls.command_started(code, parameters)
        )
        self.command_service.command_completed.connect(self.vehicle_controls.command_completed)
        self.log_service.reemit_history()

    def _on_telemetry(self, snapshot) -> None:
        self.telemetry_panel.update_snapshot(snapshot)
        self.attitude_indicator.update_snapshot(snapshot)
        self.vehicle_controls.set_flight_modes(
            self.command_service.available_flight_modes
        )
        self.vehicle_controls.update_snapshot(snapshot)
        self.map_panel.update_snapshot(snapshot)
        self.battery_meter.set_battery(
            snapshot.battery_percent,
            snapshot.battery_voltage,
            snapshot.battery_current_amps,
            snapshot.battery_consumed_mah,
        )
        self.autopilot_status.set_state("success" if snapshot.autopilot_link else "danger")
        self.telemetry_status.set_state("success" if snapshot.telemetry_link else "danger")
        self.gps_status.set_state("success" if snapshot.gps_fix not in {"", "NO_GPS", "NO_FIX"} else "warning")
        if snapshot.rc_rssi is None or not snapshot.telemetry_link:
            self.command_link_meter.set_unavailable()
        else:
            self.command_link_meter.set_value(int(snapshot.rc_rssi), "RC RSSI")

    def _on_parameter_changed(self, name: str, value: object) -> None:
        self.parameter_service.update_values({name: value})

    def _on_connection_status(self, status: ConnectionStatus) -> None:
        if status.channel != "telemetry":
            return
        self.telemetry_status.set_state("success" if status.connected else "warning" if status.detail else "danger")
        self.mavlink_popup.update_connection(
            active_identifier=self._settings.backend.active_mavlink_connection_id,
            connected=status.connected,
            running=bool(status.options.get("running", status.connected)),
            detail=status.detail,
        )
        self._show_mavlink_profiles()

    def _open_battery_popup(self) -> None:
        self.battery_popup.show_settings(
            self.settings_service.current,
            self.telemetry_service.current,
        )
        self.battery_popup.adjustSize()
        self.battery_popup.move(
            self.battery_meter.mapToGlobal(
                QPoint(0, -self.battery_popup.height())
            )
        )
        self.battery_popup.show()

    def _save_battery_settings(self, values: dict[str, object]) -> None:
        payload = self.settings_service.current.to_dict()
        telemetry = payload["backend"]["battery_telemetry"]
        alarm = payload["backend"]["battery_alarm"]
        for key in ("full_voltage", "reserve_voltage", "reserve_percent", "use_pixhawk_percent"):
            telemetry[key] = values[key]
        for key in ("warning_mode", "warning_threshold", "failsafe_enabled", "failsafe_mode", "failsafe_threshold"):
            alarm[key] = values[key]
        self.settings_service.save(SettingsModel.from_dict(payload))
        self.battery_popup.set_result(True, "Batarya ayarları kaydedildi")

    def _show_mavlink_profiles(self) -> None:
        backend = self._settings.backend
        status = self.connection_service.status("telemetry")
        self.mavlink_popup.show_profiles(
            tuple(backend.mavlink_connections),
            active_identifier=backend.active_mavlink_connection_id,
            connected=status.connected,
            running=bool(status.options.get("running", status.connected)),
            detail=status.detail,
        )

    def _open_mavlink_profiles(self) -> None:
        self._show_mavlink_profiles()
        self.mavlink_popup.adjustSize()
        anchor = self.telemetry_status if self._telemetry_ui_enabled else self.autopilot_status
        self.mavlink_popup.move(anchor.mapToGlobal(anchor.rect().topLeft()))
        self.mavlink_popup.show()
        self.mavlink_popup.raise_()

    def _connect_mavlink_profile(self, profile: MavlinkConnectionConfig) -> None:
        self._save_mavlink_profiles(tuple(self._settings.backend.mavlink_connections), profile.identifier)
        status = self.connection_service.status("telemetry")
        if status.connected and profile.identifier == self._settings.backend.active_mavlink_connection_id:
            self.connection_service.disconnect("telemetry")
            return
        self.mavlink_popup.set_connecting(profile.identifier)
        self.connection_service.reconnect(
            "telemetry",
            {
                "profile_id": profile.identifier,
                "profile_name": profile.name,
                "endpoint": profile.endpoint,
                "baud": profile.baud,
                "timeout_s": profile.heartbeat_timeout,
                "auto_connect": profile.auto_connect,
            },
        )

    def _edit_mavlink_profile(self, profile: MavlinkConnectionConfig | None) -> None:
        existing = tuple(self._settings.backend.mavlink_connections)
        template = self._settings.backend.mavlink
        profile = profile or MavlinkConnectionConfig(
            name=f"Telemetri {len(existing) + 1}",
            endpoint=template.endpoint,
            baud=template.baud,
            heartbeat_timeout=template.heartbeat_timeout,
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("Telemetri bağlantısı")
        form = QFormLayout(dialog)
        name = QLineEdit(profile.name, dialog)
        endpoint = QLineEdit(profile.endpoint, dialog)
        baud = QSpinBox(dialog); baud.setRange(1200, 4_000_000); baud.setValue(profile.baud)
        timeout = QSpinBox(dialog); timeout.setRange(1, 120); timeout.setValue(profile.heartbeat_timeout)
        form.addRow("İsim", name); form.addRow("MAVLink adresi", endpoint)
        form.addRow("Baud", baud); form.addRow("Zaman aşımı", timeout)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = MavlinkConnectionConfig(
            identifier=profile.identifier,
            name=name.text().strip(),
            endpoint=endpoint.text().strip(),
            baud=baud.value(),
            heartbeat_timeout=timeout.value(),
            auto_connect=profile.auto_connect,
        )
        profiles = [updated if item.identifier == profile.identifier else item for item in existing]
        if not any(item.identifier == updated.identifier for item in existing):
            profiles.append(updated)
        self._save_mavlink_profiles(tuple(profiles), updated.identifier)

    def _save_mavlink_profiles(self, profiles, active_identifier: str | None = None) -> None:
        payload = self._settings.to_dict()
        payload["backend"]["mavlink_connections"] = [item.to_dict() for item in profiles]
        if active_identifier is not None:
            payload["backend"]["active_mavlink_connection_id"] = active_identifier
        try:
            self.settings_service.save_async(SettingsModel.from_dict(payload))
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Telemetri bağlantısı", str(exc))

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self.language, self.theme, self)
        dialog.clear_cache_button.clicked.connect(self.settings_service.clear_cache)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings_service.save_async(dialog.to_settings())

    def _settings_changed(self, settings: SettingsModel) -> None:
        previous = self._settings
        self._settings = settings
        if self._is_mavlink_profile_only_update(previous, settings):
            self._show_mavlink_profiles()
            return
        self.language = settings.language
        self.planner_service.set_default_radii(
            {
                "takeoff": settings.default_radius_takeoff,
                "waypoint": settings.default_radius_waypoint,
                "loiter": settings.default_radius_loiter,
                "rtl": settings.default_radius_rtl,
                "land": settings.default_radius_land,
            }
        )
        self.setWindowTitle(t("app.title", self.language))
        app = QApplication.instance()
        if app is not None:
            self.theme = apply_theme(app, settings.theme)
        self.title_bar.set_theme(self.theme)
        self.title_bar.set_title(self.windowTitle())
        self.title_bar.set_menu_language(self.language)
        self._retranslate_view_menu()
        self.activity_bar.set_language(self.language)
        self.extension_panel.set_language(self.language)
        for widget in (*self._extension_visual_widgets, *self._extension_status_widgets):
            set_theme = getattr(widget, "set_theme", None)
            set_language = getattr(widget, "set_language", None)
            if callable(set_theme):
                set_theme(self.theme)
            if callable(set_language):
                set_language(self.language)
        self.attitude_indicator.set_theme(self.theme)
        self.battery_meter.set_theme(self.theme)
        self.battery_meter.set_label(t("status.battery", self.language))
        self.battery_meter.set_language(self.language)
        self.command_link_meter.set_theme(self.theme)
        self.command_link_meter.set_label(t("status.command_link", self.language))
        for chip in (self.telemetry_status, self.autopilot_status, self.gps_status):
            chip.set_theme(self.theme)
        self.battery_popup.set_theme(self.theme)
        self.mavlink_popup.set_theme(self.theme)
        self.map_panel.set_theme(self.theme)
        self.map_panel.retranslate(self.language)
        self.telemetry_panel.set_theme(self.theme)
        self.telemetry_panel.set_language(self.language)
        self.vehicle_controls.set_language(self.language)
        self.log_panel.set_language(self.language)
        self.parameters_panel.set_language(self.language)
        self.title_bar.set_title(self.windowTitle())
        self.settings_button.setToolTip(t("status.settings", self.language))
        self.telemetry_status.set_label(t("status.telemetry", self.language))
        self.autopilot_status.set_label(t("status.autopilot", self.language))
        self.gps_status.set_label(t("status.gps", self.language))
        self.battery_popup.set_language(self.language)
        self.mavlink_popup.set_language(self.language)
        self._show_mavlink_profiles()

    @staticmethod
    def _is_mavlink_profile_only_update(
        previous: SettingsModel,
        current: SettingsModel,
    ) -> bool:
        if previous is current:
            return False
        old_data = previous.to_dict()
        new_data = current.to_dict()
        old_backend = old_data.get("backend", {})
        new_backend = new_data.get("backend", {})
        old_profiles = (
            old_backend.get("mavlink_connections"),
            old_backend.get("active_mavlink_connection_id"),
        )
        new_profiles = (
            new_backend.get("mavlink_connections"),
            new_backend.get("active_mavlink_connection_id"),
        )
        for payload in (old_data, new_data):
            backend = payload.get("backend", {})
            backend.pop("mavlink_connections", None)
            backend.pop("active_mavlink_connection_id", None)
        return old_profiles != new_profiles and old_data == new_data

    def prepare_shutdown(self) -> None:
        if not getattr(self, "_workspace_disconnected", False):
            self._workspace_disconnected = True
            for dock in (
                *self._flight_docks, self.extension_dock,
                self.log_dock, self.parameters_dock,
            ):
                dock.visibilityChanged.disconnect()
        for widget in self._extension_visual_widgets:
            shutdown = getattr(widget, "prepare_shutdown", None)
            if callable(shutdown):
                shutdown()
        self.map_panel.shutdown()
        self.attitude_indicator.shutdown()

    def closeEvent(self, event) -> None:
        self.workspace.save_layout()
        self.prepare_shutdown()
        self.shutdown_requested.emit()
        super().closeEvent(event)


__all__ = ["MainWindow"]
