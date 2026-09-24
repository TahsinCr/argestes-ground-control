from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
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
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.models.connections import ConnectionStatus
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
from src.ui.widgets.title_bar import AppTitleBar


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
        self.theme = theme
        self._settings = settings_service.current
        self.language = self._settings.language
        self._editing_profile: MavlinkConnectionConfig | None = None

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
        self.activity_bar.settings_requested.connect(self.open_settings)
        content_layout.addWidget(self.activity_bar)
        self.main_splitter = self._make_splitter(Qt.Orientation.Horizontal)
        self.sidebar = self._build_sidebar()
        self.workspace = self._build_workspace()
        self.main_splitter.addWidget(self.sidebar)
        self.main_splitter.addWidget(self.workspace)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([320, 1280])
        content_layout.addWidget(self.main_splitter, 1)
        layout.addWidget(content, 1)

        status_bar = QWidget(central)
        status_bar.setObjectName("BottomStatusBar")
        footer = QHBoxLayout(status_bar)
        footer.setContentsMargins(8, 3, 8, 3)
        footer.setSpacing(6)
        self.telemetry_status = StatusChip(t("status.telemetry", self.language), status_bar)
        self.autopilot_status = StatusChip(t("status.autopilot", self.language), status_bar)
        self.gps_status = StatusChip(t("status.gps", self.language), status_bar)
        for chip in (self.telemetry_status, self.autopilot_status, self.gps_status):
            chip.set_theme(self.theme)
            chip.set_state("danger")
            footer.addWidget(chip)
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

    def _make_splitter(self, orientation: Qt.Orientation) -> QSplitter:
        splitter = QSplitter(orientation, self)
        splitter.setChildrenCollapsible(False)
        splitter.setOpaqueResize(False)
        splitter.setHandleWidth(5)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {self.theme.border}; }}"
            f"QSplitter::handle:horizontal {{ width: 5px; margin: 0 2px; }}"
            f"QSplitter::handle:vertical {{ height: 5px; margin: 2px 0; }}"
            f"QSplitter::handle:hover {{ background: {self.theme.accent}; }}"
        )
        return splitter

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget(self.main_splitter)
        sidebar.setMinimumWidth(320)
        sidebar.setMaximumWidth(370)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.flight_panel = PanelFrame("", "", sidebar)
        self.flight_panel.setMinimumWidth(300)
        self.flight_panel.setFixedHeight(205)
        self.attitude_indicator = AttitudeIndicator(self.theme, self.flight_panel)
        self.flight_panel.body_layout.addWidget(self.attitude_indicator, 1)
        layout.addWidget(self.flight_panel)
        self.telemetry_panel = TelemetryPanel("", "", self.theme, sidebar)
        layout.addWidget(self.telemetry_panel)
        self.vehicle_controls = VehicleControlsPanel(
            self.command_service.available_flight_modes,
            language=self.language,
            parent=sidebar,
        )
        self.vehicle_controls.command_requested.connect(self.command_service.issue_command)
        layout.addWidget(self.vehicle_controls, 1)
        return sidebar

    def _build_workspace(self) -> QWidget:
        workspace = self._make_splitter(Qt.Orientation.Vertical)
        workspace.setChildrenCollapsible(True)
        self.map_panel = MapPanel(
            "", "", theme=self.theme, language=self.language,
            base_dir=self.base_dir, planner_service=self.planner_service,
            settings_service=self.settings_service,
            tile_cache_service=self.tile_cache_service, parent=workspace,
        )
        workspace.addWidget(self.map_panel)
        self.bottom_splitter = self._make_splitter(Qt.Orientation.Horizontal)
        self.log_panel = EventLogPanel("", "", "", self.bottom_splitter)
        self.log_panel.setMinimumWidth(460)
        self.parameters_panel = ParametersPanel("", "", "", self.bottom_splitter)
        self.parameters_panel.setMinimumWidth(320)
        self.bottom_splitter.addWidget(self.log_panel)
        self.bottom_splitter.addWidget(self.parameters_panel)
        self.bottom_splitter.setStretchFactor(0, 64)
        self.bottom_splitter.setStretchFactor(1, 36)
        self.bottom_splitter.setSizes([900, 500])
        workspace.addWidget(self.bottom_splitter)
        workspace.setStretchFactor(0, 8)
        workspace.setStretchFactor(1, 4)
        workspace.setSizes([590, 295])
        return workspace

    def _connect_signals(self) -> None:
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
        self.mavlink_popup.move(self.telemetry_status.mapToGlobal(self.telemetry_status.rect().topLeft()))
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
        for splitter in (self.main_splitter, self.workspace, self.bottom_splitter):
            splitter.setStyleSheet(
                f"QSplitter::handle {{ background: {self.theme.border}; }}"
                f"QSplitter::handle:horizontal {{ width: 5px; margin: 0 2px; }}"
                f"QSplitter::handle:vertical {{ height: 5px; margin: 2px 0; }}"
                f"QSplitter::handle:hover {{ background: {self.theme.accent}; }}"
            )
        self.title_bar.set_theme(self.theme)
        self.title_bar.set_title(self.windowTitle())
        self.title_bar.set_menu_language(self.language)
        self.activity_bar.set_language(self.language)
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
        self.map_panel.shutdown()
        self.attitude_indicator.shutdown()

    def closeEvent(self, event) -> None:
        self.prepare_shutdown()
        self.shutdown_requested.emit()
        super().closeEvent(event)


__all__ = ["MainWindow"]
