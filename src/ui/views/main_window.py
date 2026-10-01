from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.extension.host import ExtensionHost
from src.models.settings import SettingsModel
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
from src.ui.widgets.common import BatteryMeter, PanelFrame, StatusChip
from src.ui.widgets.control_panels import TelemetryPanel, VehicleControlsPanel
from src.ui.widgets.logs import EventLogPanel
from src.ui.widgets.map_panel import MapPanel
from src.ui.widgets.parameters import ParametersPanel
from src.ui.widgets.activity_bar import ActivityBar
from src.ui.widgets.extension_panel import ExtensionPanel
from src.ui.widgets.title_bar import AppTitleBar
from src.ui.widgets.panel_workspace import PanelWorkspace


@dataclass(frozen=True)
class _VisualDockPreference:
    area: Qt.DockWidgetArea
    width: int
    height: int
    floating: bool
    tab_target: str | None = None
    anchor: str | None = None
    orientation: Qt.Orientation = Qt.Orientation.Horizontal
    after_anchor: bool = True


class MainWindow(QMainWindow):
    shutdown_requested = pyqtSignal()
    _WORKSPACE_AREAS = Qt.DockWidgetArea.AllDockWidgetAreas

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
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal, content)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(4)
        self._sidebar_last_width = 320
        self._sidebar_explicitly_visible = False
        self.workspace = PanelWorkspace(
            self.base_dir / "data" / "workspace-layout.json", self.content_splitter,
            language=self.language,
        )
        self._build_sidebar()
        self._build_workspace()
        self.sidebar = QStackedWidget(self.content_splitter)
        self.sidebar.setObjectName("ExtensionSidebar")
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
        self.content_splitter.addWidget(self.sidebar)
        self.content_splitter.addWidget(self.workspace)
        self.content_splitter.setStretchFactor(0, 0)
        self.content_splitter.setStretchFactor(1, 1)
        self.sidebar.hide()
        self.activity_bar.sidebar_view_requested.connect(self._show_sidebar_view)
        self.activity_bar.flight_sidebar_toggled.connect(self._toggle_flight_panels)
        self.activity_bar.panel_visibility_requested.connect(
            self._set_workspace_panel_visibility
        )
        self.activity_bar.bottom_panel_toggled.connect(self._on_bottom_panel_toggled)
        self.activity_bar.bottom_panel_side_by_side_requested.connect(
            self._open_bottom_panel_beside
        )
        content_layout.addWidget(self.content_splitter, 1)
        self.workspace.show()
        layout.addWidget(content, 1)
        self._build_view_menu()

        status_bar = QWidget(central)
        status_bar.setObjectName("BottomStatusBar")
        footer = QHBoxLayout(status_bar)
        footer.setContentsMargins(8, 3, 8, 3)
        footer.setSpacing(6)
        self.status_bar = status_bar
        self.autopilot_status = StatusChip(t("status.autopilot", self.language), status_bar)
        self.gps_status = StatusChip(t("status.gps", self.language), status_bar)
        for chip in (self.autopilot_status, self.gps_status):
            chip.set_theme(self.theme)
            chip.set_state("danger")
            footer.addWidget(chip)
        self._extension_status_widgets: list[QWidget] = []
        self._extension_status_by_id: dict[str, QWidget] = {}
        footer.addStretch(1)
        self.battery_meter = BatteryMeter(t("status.battery", self.language), status_bar)
        self.battery_meter.set_theme(self.theme)
        self.battery_meter.set_unavailable()
        self.battery_meter.clicked.connect(self._open_battery_popup)
        footer.addWidget(self.battery_meter)
        self._status_metrics_anchor = QWidget(status_bar)
        self._status_metrics_anchor.setFixedWidth(0)
        footer.addWidget(self._status_metrics_anchor)
        self.settings_button = QPushButton("⚙", status_bar)
        self.settings_button.setObjectName("SettingsIconButton")
        self.settings_button.setProperty("compact", "true")
        self.settings_button.setFixedWidth(42)
        self.settings_button.setToolTip(t("status.settings", self.language))
        self.settings_button.clicked.connect(self.open_settings)
        footer.addWidget(self.settings_button)
        for contribution in self.extension_host.status_widgets():
            self._add_status_contribution(contribution)
        layout.addWidget(status_bar)

        self.battery_popup = BatterySettingsPopup(self.theme, self, language=self.language)
        self.battery_popup.save_requested.connect(self._save_battery_settings)
        self.battery_popup.settings_requested.connect(self.open_settings)

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
        vehicle_areas = Qt.DockWidgetArea.AllDockWidgetAreas
        flight = self.workspace.add_panel(
            "flight", "Araç Dinamiği", self.flight_panel, area,
            allowed_areas=vehicle_areas, floatable=False,
        )
        telemetry = self.workspace.add_panel(
            "telemetry", "Araç Telemetrisi", self.telemetry_panel, area,
            allowed_areas=vehicle_areas, floatable=False,
        )
        controls = self.workspace.add_panel(
            "commands", "Hızlı Komutlar", self.vehicle_controls, area,
            allowed_areas=vehicle_areas, floatable=False,
        )
        self.workspace.place_panel(flight, telemetry, "below")
        self.workspace.place_panel(telemetry, controls, "below")
        self._flight_docks = (flight, telemetry, controls)
        self.workspace.enable_independent_widths(("flight", "telemetry", "commands"))

    def _show_sidebar_view(self, view: str) -> None:
        if view == "flight":
            self._set_extension_sidebar_visible(False)
            for dock in self._flight_docks:
                dock.show()
            return
        if view:
            for dock in self._flight_docks:
                dock.hide()
            self.sidebar.setCurrentWidget(
                self.extension_panel if view == "extensions" else
                self._extension_sidebar_widgets.get(view, self.extension_panel)
            )
            self._set_extension_sidebar_visible(True)
        else:
            self._set_extension_sidebar_visible(False)

    def _set_extension_sidebar_visible(self, visible: bool) -> None:
        if visible == self._sidebar_explicitly_visible:
            return
        if not visible:
            width = self.sidebar.width()
            if width >= self.sidebar.minimumWidth():
                self._sidebar_last_width = width
            self._sidebar_explicitly_visible = False
            self.sidebar.hide()
            return
        self._sidebar_explicitly_visible = True
        self.sidebar.show()
        width = max(280, self._sidebar_last_width)
        QTimer.singleShot(
            0,
            lambda remembered=width: self.content_splitter.setSizes(
                [remembered, max(1, self.content_splitter.width() - remembered)]
            ),
        )

    def _toggle_flight_panels(self, visible: bool) -> None:
        if visible:
            self._set_extension_sidebar_visible(False)
        for dock in self._flight_docks:
            dock.setVisible(visible)

    def _register_workspace_panel_button(
        self, key: str, title: str, dock
    ) -> None:
        button = self.activity_bar.add_panel_button(key, title)
        button.setChecked(not dock.isHidden())
        dock.visibilityChanged.connect(
            lambda _visible, panel_key=key, panel=dock:
            self.activity_bar.set_panel_checked(panel_key, not panel.isHidden())
        )

    def _set_workspace_panel_visibility(self, key: str, visible: bool) -> None:
        dock = self.workspace.panels.get(key)
        if dock is None:
            return
        dock.setVisible(visible)
        if visible:
            dock.raise_()

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
            return
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
            map_area = self.workspace.dockWidgetArea(self.map_dock)
            if map_area == Qt.DockWidgetArea.NoDockWidgetArea:
                map_area = Qt.DockWidgetArea.RightDockWidgetArea
            area = (
                preference.area
                if preference
                and preference.area != Qt.DockWidgetArea.NoDockWidgetArea
                else map_area
            )
            dock = self.workspace.add_panel(
                contribution.identifier, self._visual_title(contribution.identifier), widget,
                area, allowed_areas=self._WORKSPACE_AREAS,
                floatable=False,
            )
            self._register_workspace_panel_button(
                contribution.identifier, dock.windowTitle(), dock
            )
            if preference and preference.floating:
                dock.setFloating(True)
            elif preference and preference.tab_target in self.workspace.panels:
                self.workspace._tabify_panel(
                    self.workspace.panels[preference.tab_target], dock
                )
            elif preference and preference.anchor in self.workspace.panels:
                anchor = self.workspace.panels[preference.anchor]
                first, second = (
                    (anchor, dock) if preference.after_anchor else (dock, anchor)
                )
                self.workspace.place_panel(
                    first, second,
                    "right" if preference.orientation == Qt.Orientation.Horizontal
                    and preference.after_anchor else
                    "left" if preference.orientation == Qt.Orientation.Horizontal else
                    "below" if preference.after_anchor else "above",
                )
            elif area == map_area:
                self.workspace.place_panel(self.map_dock, dock, "right")
            self._extension_visual_widgets.append(widget)
            self._extension_visual_by_id[contribution.identifier] = widget
            self._refresh_panel_menu()
            QTimer.singleShot(
                0,
                lambda panel=dock, size=preference: self._size_visual_dock(panel, size),
            )
        for contribution in self.extension_host.status_widgets():
            if not contribution.identifier.startswith(prefix):
                continue
            self._add_status_contribution(contribution)

    def _add_status_contribution(self, contribution) -> None:
        widget = contribution.factory(self.status_bar, self.theme, self.language)
        if not isinstance(widget, QWidget):
            raise TypeError("Extension status must return QWidget")
        footer = self.status_bar.layout()
        index = footer.indexOf(self._status_metrics_anchor) if contribution.placement == "metrics" else footer.indexOf(self.gps_status) + 1
        footer.insertWidget(index, widget)
        self._extension_status_widgets.append(widget)
        self._extension_status_by_id[contribution.identifier] = widget

    def _remove_extension_ui(self, identifier: str) -> None:
        prefix = identifier + "."
        for key, widget in tuple(self._extension_status_by_id.items()):
            if key.startswith(prefix):
                shutdown = getattr(widget, "prepare_shutdown", None)
                if callable(shutdown):
                    shutdown()
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
                    self._visual_dock_preferences[key] = self._capture_visual_dock_preference(
                        key, dock
                    )
                self.workspace.remove_panel(key)
                self.activity_bar.remove_panel_button(key)
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                self._extension_visual_widgets.remove(widget)
                del self._extension_visual_by_id[key]
        self._refresh_panel_menu()
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
        self.map_dock = self.workspace.add_panel(
            "map", "Harita", self.map_panel,
            Qt.DockWidgetArea.RightDockWidgetArea,
            allowed_areas=self._WORKSPACE_AREAS,
            floatable=False,
        )
        self._register_workspace_panel_button("map", "Harita", self.map_dock)
        self._extension_visual_widgets: list[QWidget] = []
        self._extension_visual_by_id: dict[str, QWidget] = {}
        self._visual_dock_preferences: dict[str, _VisualDockPreference] = {}
        self.log_panel = EventLogPanel("", "", "", self.workspace)
        self.parameters_panel = ParametersPanel("", "", "", self.workspace)
        bottom = Qt.DockWidgetArea.BottomDockWidgetArea
        self.log_dock = self.workspace.add_panel(
            "logs", "Loglar", self.log_panel, bottom,
            allowed_areas=Qt.DockWidgetArea.AllDockWidgetAreas,
        )
        self.parameters_dock = self.workspace.add_panel(
            "parameters", "Parametreler", self.parameters_panel, bottom,
            allowed_areas=Qt.DockWidgetArea.AllDockWidgetAreas,
        )
        self.workspace.place_panel(self.log_dock, self.parameters_dock, "tab")
        self.parameters_dock.hide()
        for contribution in self.extension_host.visual_views():
            widget = contribution.factory(self.workspace, self.theme, self.language)
            if not isinstance(widget, QWidget):
                raise TypeError(f"Extension visual {contribution.identifier} must return QWidget")
            dock = self.workspace.add_panel(
                contribution.identifier, self._visual_title(contribution.identifier), widget,
                Qt.DockWidgetArea.RightDockWidgetArea,
                allowed_areas=self._WORKSPACE_AREAS,
                floatable=False,
            )
            self._register_workspace_panel_button(
                contribution.identifier, dock.windowTitle(), dock
            )
            self.workspace.place_panel(self.map_dock, dock, "right")
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
        self.workspace.place_panel(other, selected, "right")
        selected.show()

    def _build_view_menu(self) -> None:
        menu = self.title_bar._menu_bar.actions()[3].menu()
        self._panel_menu = menu.addMenu("Paneller")
        self._refresh_panel_menu()
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

    def _refresh_panel_menu(self) -> None:
        if hasattr(self, "_panel_menu"):
            self._populate_panel_actions(self._panel_menu)

    def _populate_panel_actions(self, menu: QMenu) -> None:
        menu.clear()
        english = self.language == "en"
        extension_views = tuple(
            key for key in self.workspace.panels
            if key not in {
                "map", "flight", "telemetry", "commands", "logs", "parameters"
            }
        )
        groups = (
            ("Çalışma alanı" if not english else "Workspace", ("map",)),
            ("Araç" if not english else "Vehicle", ("flight", "telemetry", "commands")),
            ("Alt paneller" if not english else "Bottom panels", ("logs", "parameters")),
            ("Eklenti görünümleri" if not english else "Extension views", extension_views),
        )
        for title, keys in groups:
            actions = [
                self.workspace.panels[key].toggleViewAction()
                for key in keys
                if key in self.workspace.panels
            ]
            if not actions:
                continue
            menu.addSection(title)
            for action in actions:
                menu.addAction(action)

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
            "map": "Map" if english else "Harita",
            "flight": "Flight Dynamics" if english else "Araç Dinamiği",
            "telemetry": "Vehicle Telemetry" if english else "Araç Telemetrisi",
            "commands": "Quick Commands" if english else "Hızlı Komutlar",
            "logs": "Logs" if english else "Loglar",
            "parameters": "Parameters" if english else "Parametreler",
        }
        for key, dock in self.workspace.panels.items():
            dock.setWindowTitle(labels.get(key, self._visual_title(key)))
            if key in self.activity_bar.panel_buttons:
                self.activity_bar.set_panel_title(key, dock.windowTitle())

    def _set_layout_edit_mode(self, enabled: bool) -> None:
        self.workspace.set_edit_mode(enabled)

    def _set_theme_choice(self, theme_name: str) -> None:
        if self._settings.theme == theme_name:
            return
        payload = self._settings.to_dict()
        payload["ui"]["theme"] = theme_name
        self.settings_service.save_async(SettingsModel.from_dict(payload))

    def _focus_map(self) -> None:
        self._set_extension_sidebar_visible(False)
        for dock in self.workspace.panels.values():
            dock.hide()
        self.map_dock.show()
        self.map_dock.raise_()
        self.activity_bar.sidebar_button.setChecked(False)
        self.activity_bar.extensions_button.setChecked(False)
        for key in self.activity_bar.buttons:
            self.activity_bar.set_bottom_panel_checked(key, False)

    def _reset_workspace(self) -> None:
        self._set_extension_sidebar_visible(False)
        for dock in self.workspace.panels.values():
            dock.setFloating(False)
        for dock in self._flight_docks:
            self.workspace.move_panel_to_area(
                dock, Qt.DockWidgetArea.LeftDockWidgetArea
            )
            dock.show()
        self.workspace.place_panel(self._flight_docks[0], self._flight_docks[1], "below")
        self.workspace.place_panel(self._flight_docks[1], self._flight_docks[2], "below")
        self.workspace.move_panel_to_area(
            self.map_dock, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.map_dock.show()
        self.workspace.move_panel_to_area(
            self.log_dock, Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.workspace.move_panel_to_area(
            self.parameters_dock, Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.workspace.place_panel(self.log_dock, self.parameters_dock, "tab")
        self.log_dock.show()
        self.parameters_dock.hide()
        for key, dock in self.workspace.panels.items():
            if key not in {"map", "flight", "telemetry", "commands", "logs", "parameters"}:
                self.workspace.move_panel_to_area(
                    dock, Qt.DockWidgetArea.RightDockWidgetArea
                )
                self.workspace.place_panel(self.map_dock, dock, "right")
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
            if key not in {"map", "flight", "telemetry", "commands", "logs", "parameters"}
            and dock.isVisible()
            and self.workspace.dockWidgetArea(dock)
            == self.workspace.dockWidgetArea(self.map_dock)
        ]
        visual_sizes = [
            max(280, self.workspace.width() // (len(visible_right) + 2))
            for _dock in visible_right
        ]
        self.workspace.resizeDocks(
            [self._flight_docks[0], self.map_dock, *visible_right],
            [320, max(600, self.workspace.width() // 2), *visual_sizes],
            Qt.Orientation.Horizontal,
        )

    def _size_visual_dock(
        self,
        dock,
        preference: _VisualDockPreference | None,
    ) -> None:
        if dock not in self.workspace.panels.values() or dock.isFloating():
            return
        area = self.workspace.dockWidgetArea(dock)
        orientation = (
            Qt.Orientation.Horizontal
            if area in (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea)
            else Qt.Orientation.Vertical
        )
        if preference:
            size = (
                preference.width
                if orientation == Qt.Orientation.Horizontal
                else preference.height
            )
        else:
            size = (
                max(380, self.workspace.width() // 3)
                if orientation == Qt.Orientation.Horizontal
                else max(240, self.workspace.height() // 3)
            )
        self.workspace.resizeDocks([dock], [size], orientation)

    def _capture_visual_dock_preference(
        self, key: str, dock
    ) -> _VisualDockPreference:
        peers = self.workspace.tabifiedDockWidgets(dock)
        tab_target = next(
            (
                panel_key
                for panel_key, panel in self.workspace.panels.items()
                if panel in peers
            ),
            None,
        )
        anchor = None
        orientation = Qt.Orientation.Horizontal
        after_anchor = True
        best_gap: int | None = None
        if tab_target is None and not dock.isFloating():
            area = self.workspace.dockWidgetArea(dock)
            source = dock.geometry()
            for other_key, other in self.workspace.panels.items():
                if (
                    other_key == key
                    or other.isFloating()
                    or not other.isVisible()
                    or self.workspace.dockWidgetArea(other) != area
                ):
                    continue
                rect = other.geometry()
                vertical_overlap = max(
                    0, min(source.bottom(), rect.bottom()) - max(source.top(), rect.top())
                )
                horizontal_overlap = max(
                    0, min(source.right(), rect.right()) - max(source.left(), rect.left())
                )
                if vertical_overlap >= min(source.height(), rect.height()) // 3:
                    is_after = source.center().x() >= rect.center().x()
                    gap = (
                        source.left() - rect.right()
                        if is_after else rect.left() - source.right()
                    )
                    if gap >= -8 and (best_gap is None or gap < best_gap):
                        anchor, orientation, after_anchor, best_gap = (
                            other_key, Qt.Orientation.Horizontal, is_after, gap
                        )
                if horizontal_overlap >= min(source.width(), rect.width()) // 3:
                    is_after = source.center().y() >= rect.center().y()
                    gap = (
                        source.top() - rect.bottom()
                        if is_after else rect.top() - source.bottom()
                    )
                    if gap >= -8 and (best_gap is None or gap < best_gap):
                        anchor, orientation, after_anchor, best_gap = (
                            other_key, Qt.Orientation.Vertical, is_after, gap
                        )
        return _VisualDockPreference(
            area=self.workspace.dockWidgetArea(dock),
            width=dock.width(),
            height=dock.height(),
            floating=dock.isFloating(),
            tab_target=tab_target,
            anchor=anchor,
            orientation=orientation,
            after_anchor=after_anchor,
        )

    def _restore_workspace(self) -> None:
        if self._workspace_disconnected:
            return
        if not self.workspace.restore_layout():
            self._size_default_docks()
            self.workspace.enable_panel_size_restore(capture_current=True)
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
        self.activity_bar.extensions_button.setChecked(self._sidebar_explicitly_visible)
        self.activity_bar.set_bottom_panel_checked("logs", not self.log_dock.isHidden())
        self.activity_bar.set_bottom_panel_checked("parameters", not self.parameters_dock.isHidden())

    def _connect_signals(self) -> None:
        for dock in (*self._flight_docks, self.log_dock, self.parameters_dock):
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
        self.gps_status.set_state("success" if snapshot.gps_fix not in {"", "NO_GPS", "NO_FIX"} else "warning")

    def _on_parameter_changed(self, name: str, value: object) -> None:
        self.parameter_service.update_values({name: value})

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

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self.language, self.theme, self)
        dialog.clear_cache_button.clicked.connect(self.settings_service.clear_cache)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings_service.save_async(dialog.to_settings())

    def _settings_changed(self, settings: SettingsModel) -> None:
        previous = self._settings
        self._settings = settings
        if (previous.ui == settings.ui and previous.backend.mission == settings.backend.mission
                and previous.backend.battery_alarm == settings.backend.battery_alarm
                and previous.backend.battery_telemetry == settings.backend.battery_telemetry):
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
        for chip in (self.autopilot_status, self.gps_status):
            chip.set_theme(self.theme)
        self.battery_popup.set_theme(self.theme)
        self.map_panel.set_theme(self.theme)
        self.map_panel.retranslate(self.language)
        self.telemetry_panel.set_theme(self.theme)
        self.telemetry_panel.set_language(self.language)
        self.vehicle_controls.set_language(self.language)
        self.log_panel.set_language(self.language)
        self.parameters_panel.set_language(self.language)
        self.title_bar.set_title(self.windowTitle())
        self.settings_button.setToolTip(t("status.settings", self.language))
        self.autopilot_status.set_label(t("status.autopilot", self.language))
        self.gps_status.set_label(t("status.gps", self.language))
        self.battery_popup.set_language(self.language)


    def prepare_shutdown(self) -> None:
        if not getattr(self, "_workspace_disconnected", False):
            self._workspace_disconnected = True
            for dock in (
                *self._flight_docks, self.log_dock, self.parameters_dock,
            ):
                dock.visibilityChanged.disconnect()
        for widget in (*self._extension_visual_widgets, *self._extension_status_widgets):
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
