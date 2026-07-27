from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.ui.i18n import connection_status_text, t
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.video import VideoStreamStats
from src.models.connections import ConnectionStatus
from src.services.contracts import CommandService, TelemetryService, VideoService
from src.services.logging_service import LoggingService
from src.services.battery_alarm import BatteryAlarmService
from src.services.mission_archive import MissionArchiveService
from src.services.planner import PlannerService
from src.services.settings import SettingsService
from src.ui.assets import application_icon
from src.ui.themes.style import ThemePalette, apply_theme
from src.ui.views.archive_dialog import MissionArchiveDialog
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.attitude import AttitudeIndicator
from src.ui.widgets.common import BatteryMeter, PanelFrame, SignalMeter, StatusChip
from src.ui.widgets.connection_popup import ConnectionPopup
from src.ui.widgets.control_panels import CommandPanel, TelemetryPanel
from src.ui.widgets.logs import EventLogPanel
from src.ui.widgets.map_panel import MapPanel
from src.ui.widgets.parameters import ParametersPanel
from src.ui.widgets.title_bar import AppTitleBar
from src.ui.widgets.video import VideoPanel


class MainWindow(QMainWindow):
    shutdown_requested = pyqtSignal()

    def __init__(
        self,
        *,
        log_service: LoggingService,
        settings_service: SettingsService,
        telemetry_service: TelemetryService,
        planner_service: PlannerService,
        command_service: CommandService,
        parameter_service,
        video_service: VideoService,
        connection_service,
        tile_cache_service,
        archive_service: MissionArchiveService,
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
        self.video_service = video_service
        self.connection_service = connection_service
        self.tile_cache_service = tile_cache_service
        self.base_dir = base_dir
        self.theme = theme
        self.language = settings_service.current.language
        self.ui_log = log_service.child("UI")
        self.battery_log = log_service.child("BATTERY")
        self.battery_alarm_service = BatteryAlarmService(
            self.battery_log,
            self._request_battery_failsafe,
        )
        self.archive_service = archive_service
        self._last_warning_signature = ""
        self._last_snapshot = telemetry_service.current
        self._serial_access_prompted: set[str] = set()
        self._serial_access_repair_running = False
        self._shutdown_prepared = False

        self.setWindowIcon(application_icon(base_dir))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.resize(1600, 940)
        self.setMinimumSize(1280, 720)
        self._build_ui()
        self.connection_popup = ConnectionPopup(
            self.theme,
            self,
            language=self.language,
        )
        self._connect_signals()
        self.parameters_panel.set_parameters(self.parameter_service.current)
        self.apply_settings(settings_service.current)
        self.ui_log.event(
            "ui.started",
            "Arayuz baslatildi, log akisi aktif",
            severity="success",
        )

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)
        self.title_bar = AppTitleBar(self, self.theme, central)
        root.addWidget(self.title_bar)
        root.addWidget(self._build_main_splitter(), 1)
        root.addWidget(self._build_bottom_status_bar())

    def _build_bottom_status_bar(self) -> QWidget:
        status_bar = QWidget(self)
        status_bar.setObjectName("BottomStatusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(8, 3, 8, 3)
        status_layout.setSpacing(6)

        self.bottom_status = {
            "server": StatusChip("SERVER", self),
            "telemetry": StatusChip("TELEMETRY", self),
            "video": StatusChip("VIDEO", self),
            "autopilot": StatusChip("AUTOPILOT", self),
            "gps": StatusChip("GPS", self),
            "orin": StatusChip("ORIN", self),
        }
        for chip in self.bottom_status.values():
            chip.set_theme(self.theme)
            status_layout.addWidget(chip)
        for channel, chip in self.bottom_status.items():
            chip.clicked.connect(
                lambda checked=False, key=channel, source=chip: self._open_connection_popup(
                    key, source
                )
            )
        status_layout.addStretch(1)

        self.battery_meter = BatteryMeter("BATARYA", self)
        self.battery_meter.set_theme(self.theme)
        self.battery_meter.set_unavailable()
        status_layout.addWidget(self.battery_meter)

        self.link_meters = {
            "command": SignalMeter("KOMUT LINKI", self),
            "bulk": SignalMeter("VIDEO AKTARIMI", self),
        }
        for meter in self.link_meters.values():
            meter.set_theme(self.theme)
            status_layout.addWidget(meter)
        self.link_meters["bulk"].set_unavailable(
            t("video.link.waiting", self.language)
        )

        self.settings_button = QPushButton("⚙", self)
        self.settings_button.setObjectName("SettingsIconButton")
        self.settings_button.setToolTip(t("status.settings", self.language))
        self.settings_button.setProperty("compact", "true")
        self.settings_button.setFixedWidth(42)
        self.settings_button.clicked.connect(self.open_settings)
        status_layout.addWidget(self.settings_button)
        return status_bar

    def _build_main_splitter(self) -> QSplitter:
        self.main_splitter = self._smooth_splitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self._build_sidebar())
        self.main_splitter.addWidget(self._build_workspace())
        self.main_splitter.setStretchFactor(0, 28)
        self.main_splitter.setStretchFactor(1, 72)
        self.main_splitter.setSizes([350, 1250])
        return self.main_splitter

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget(self)
        sidebar.setMinimumWidth(320)
        sidebar.setMaximumWidth(390)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        sidebar_layout.addWidget(self._build_flight_panel(), 4)
        self.telemetry_panel = TelemetryPanel("", "", theme=self.theme, parent=self)
        self.command_panel = CommandPanel(
            "",
            "",
            self,
            task_codes=self.command_service.available_tasks,
        )
        sidebar_layout.addWidget(self.telemetry_panel, 5)
        sidebar_layout.addWidget(self.command_panel, 4)
        return sidebar

    def _build_workspace(self) -> QSplitter:
        self.workspace_splitter = self._smooth_splitter(Qt.Orientation.Vertical)
        self.workspace_splitter.addWidget(self._build_visuals_splitter())
        self.workspace_splitter.addWidget(self._build_bottom_splitter())
        self.workspace_splitter.setStretchFactor(0, 8)
        self.workspace_splitter.setStretchFactor(1, 4)
        self.workspace_splitter.setSizes([590, 295])
        return self.workspace_splitter

    def _build_visuals_splitter(self) -> QSplitter:
        self.visuals_splitter = self._smooth_splitter(Qt.Orientation.Horizontal)

        self.map_panel = MapPanel(
            "",
            "",
            theme=self.theme,
            language=self.language,
            base_dir=self.base_dir,
            planner_service=self.planner_service,
            settings_service=self.settings_service,
            tile_cache_service=self.tile_cache_service,
            parent=self,
        )
        self.video_panel = VideoPanel(
            "",
            "",
            theme=self.theme,
            language=self.language,
            parent=self,
        )
        self.visuals_splitter.addWidget(self.map_panel)
        self.visuals_splitter.addWidget(self.video_panel)
        self.visuals_splitter.setStretchFactor(0, 1)
        self.visuals_splitter.setStretchFactor(1, 1)
        self.visuals_splitter.setSizes([760, 760])
        return self.visuals_splitter

    def _build_bottom_splitter(self) -> QSplitter:
        self.bottom_splitter = self._smooth_splitter(Qt.Orientation.Horizontal)
        self.log_panel = EventLogPanel("", "", "", self)
        self.log_panel.setMinimumWidth(460)
        self.bottom_splitter.addWidget(self.log_panel)

        self.parameters_panel = ParametersPanel("", "", "", self)
        self.parameters_panel.setMinimumWidth(320)
        self.bottom_splitter.addWidget(self.parameters_panel)
        self.bottom_splitter.setStretchFactor(0, 64)
        self.bottom_splitter.setStretchFactor(1, 36)
        self.bottom_splitter.setSizes([900, 500])
        return self.bottom_splitter

    def _smooth_splitter(self, orientation: Qt.Orientation) -> QSplitter:
        splitter = QSplitter(orientation, self)
        splitter.setChildrenCollapsible(False)
        splitter.setOpaqueResize(False)
        splitter.setHandleWidth(5)
        self._apply_splitter_style(splitter)
        return splitter

    def _apply_splitter_style(self, splitter: QSplitter) -> None:
        splitter.setStyleSheet(
            f"""
            QSplitter::handle {{
                background: {self.theme.border};
            }}
            QSplitter::handle:horizontal {{
                width: 5px;
                margin: 0 2px;
            }}
            QSplitter::handle:vertical {{
                height: 5px;
                margin: 2px 0;
            }}
            QSplitter::handle:hover {{
                background: {self.theme.accent};
            }}
            """
        )

    def _build_flight_panel(self) -> PanelFrame:
        self.flight_panel = PanelFrame("", "", self)
        self.flight_panel.setMinimumWidth(300)
        self.attitude_indicator = AttitudeIndicator(self.theme, self.flight_panel)
        self.flight_panel.body_layout.addWidget(self.attitude_indicator, 1)
        return self.flight_panel

    def _connect_signals(self) -> None:
        self.telemetry_service.telemetry_updated.connect(self._on_telemetry)
        self.video_service.frame_ready.connect(self.video_panel.set_frame)
        self.video_service.stats_updated.connect(self._on_video_stats)
        self.video_panel.reconnect_requested.connect(self._reconnect_video_from_panel)
        self.command_panel.command_requested.connect(self.command_service.issue_command)
        self.map_panel.command_requested.connect(self.command_service.issue_command)
        self.command_service.command_dispatched.connect(self._on_command_dispatched)
        self.command_service.command_completed.connect(self._on_command_completed)
        self.map_panel.storage_requested.connect(self._handle_map_storage_request)
        self.map_panel.info_generated.connect(
            lambda severity, message: self.log_service.log("MAP", message, severity)
        )

        self.log_service.entry_emitted.connect(self.log_panel.add_entry)
        self.parameters_panel.parameter_changed.connect(self._on_parameter_changed)
        self.parameter_service.parameters_changed.connect(self.parameters_panel.set_parameters)
        self.parameters_panel.apply_requested.connect(self._on_parameters_apply)
        self.parameters_panel.reset_requested.connect(self._on_parameters_reset)
        self.settings_service.settings_changed.connect(self.apply_settings)
        self.connection_service.status_changed.connect(self._on_connection_status)
        self.connection_service.operation_started.connect(self._on_connection_operation_started)
        self.connection_service.operation_finished.connect(self._on_connection_operation_finished)
        self.connection_popup.reconnect_requested.connect(self._reconnect_channel)
        self.connection_popup.disconnect_requested.connect(self._disconnect_channel)
        self.connection_popup.settings_requested.connect(self._open_settings_from_popup)

    def _on_command_dispatched(self, code: str, parameters: dict) -> None:
        if parameters.get("battery_failsafe") is True:
            return
        self.command_panel.command_started(code)
        self.map_panel.command_started(code, parameters)

    def _on_command_completed(self, result) -> None:
        if result.request.source == "battery-failsafe":
            self.battery_log.event(
                (
                    "battery.failsafe_confirmed"
                    if result.accepted
                    else "battery.failsafe_failed"
                ),
                (
                    "Düşük batarya RTL failsafe modu doğrulandı"
                    if result.accepted
                    else f"Düşük batarya RTL failsafe modu uygulanamadı: {result.message}"
                ),
                severity="success" if result.accepted else "danger",
                details={
                    "mode": "RTL",
                    "result_message": result.message,
                },
            )
            return
        self.command_panel.command_completed(result)
        self.map_panel.command_completed(result)
        if (
            result.request.code == "ARM"
            and not result.accepted
            and self._last_snapshot.autopilot_link
            and not self._last_snapshot.armed
            and self._confirm_force_arm(result)
        ):
            self.ui_log.event(
                "vehicle.force_arm_requested",
                "Operatör normal ARM hatasından sonra zorla ARM komutunu onayladı",
                severity="warning",
                details={"normal_arm_error": result.message},
            )
            self.command_service.issue_command(
                "FORCE_ARM",
                {
                    "operator_confirmation": True,
                    "normal_arm_error": result.message,
                },
            )

    def _request_battery_failsafe(self, mode: str) -> object:
        return self.command_service.issue_command(
            "APPLY_MODE",
            {
                "mode": mode,
                "battery_failsafe": True,
            },
            source="battery-failsafe",
        )

    def _confirm_force_arm(self, result) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(t("command.force_arm.title", self.language))
        dialog.setText(t("command.force_arm.warning", self.language))
        dialog.setInformativeText(
            t("command.force_arm.reason", self.language).format(reason=result.message)
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        yes_button = dialog.button(QMessageBox.StandardButton.Yes)
        no_button = dialog.button(QMessageBox.StandardButton.No)
        if yes_button is not None:
            yes_button.setText(t("command.force_arm.confirm", self.language))
            yes_button.setProperty("role", "danger")
        if no_button is not None:
            no_button.setText(t("command.force_arm.cancel", self.language))
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def apply_settings(self, settings: SettingsModel) -> None:
        self.language = settings.language
        app = QApplication.instance()
        if app:
            self.theme = apply_theme(app, settings.theme)
        for splitter_name in (
            "main_splitter",
            "workspace_splitter",
            "visuals_splitter",
            "bottom_splitter",
        ):
            splitter = getattr(self, splitter_name, None)
            if splitter is not None:
                self._apply_splitter_style(splitter)
        if hasattr(self.planner_service, "set_default_radii"):
            self.planner_service.set_default_radii(
                {
                    "takeoff": settings.default_radius_takeoff,
                    "waypoint": settings.default_radius_waypoint,
                    "loiter": settings.default_radius_loiter,
                    "rtl": settings.default_radius_rtl,
                    "land": settings.default_radius_land,
                    "hss": settings.default_radius_hss,
                }
            )
        for meter in self.link_meters.values():
            meter.set_theme(self.theme)
        self.battery_meter.set_theme(self.theme)
        for chip in self.bottom_status.values():
            chip.set_theme(self.theme)
        self.connection_popup.set_theme(self.theme)
        self.connection_popup.set_language(self.language)
        self.title_bar.set_theme(self.theme)
        self.telemetry_panel.set_theme(self.theme)
        self.attitude_indicator.set_theme(self.theme)
        self.map_panel.set_theme(self.theme)
        self.video_panel.set_theme(self.theme)
        self.map_panel.retranslate(self.language)
        self._retranslate_ui()
        self.battery_meter.set_alarm(
            self.battery_alarm_service.evaluate(
                self._last_snapshot,
                settings.backend.battery_alarm,
            )
        )

    def _retranslate_ui(self) -> None:
        app = QApplication.instance()
        if app:
            app.setApplicationName(t("app.title", self.language))
        self.setWindowTitle(t("app.title", self.language))
        self.title_bar.set_title(t("app.title", self.language))
        self.settings_button.setToolTip(t("status.settings", self.language))

        self.telemetry_panel.set_title(
            t("panel.telemetry", self.language),
            t("panel.telemetry.subtitle", self.language),
        )
        self.telemetry_panel.set_language(self.language)
        self.telemetry_panel.set_status_labels(self._status_labels())
        self.map_panel.set_title(
            t("panel.map", self.language),
            t("panel.map.subtitle", self.language),
        )
        video_subtitle = f"{t('panel.video.subtitle', self.language)} | {t('panel.video.meta', self.language)}"
        self.video_panel.set_title(
            t("panel.video", self.language),
            video_subtitle,
        )
        self.video_panel.set_language(self.language)
        self.flight_panel.set_title(
            t("panel.flight", self.language),
            t("panel.flight.subtitle", self.language),
        )
        self.command_panel.set_title(
            t("panel.commands", self.language),
            t("panel.commands.subtitle", self.language),
        )
        self.command_panel.set_language(self.language)
        self.log_panel.set_title(
            t("panel.logs", self.language),
            t("panel.logs.subtitle", self.language),
        )
        self.log_panel.clear_button.setText(t("log.clear", self.language))
        self.log_panel.set_language(self.language)
        self.parameters_panel.set_title(
            t("panel.parameters", self.language),
            t("panel.parameters.subtitle", self.language),
        )
        self.parameters_panel.search_input.setPlaceholderText(t("search.placeholder", self.language))
        self.parameters_panel.set_language(self.language)
        self.link_meters["command"].set_label(t("status.command_link", self.language))
        self.link_meters["bulk"].set_label(t("status.payload_link", self.language))
        self.battery_meter.set_label(t("status.battery", self.language))
        self.battery_meter.set_language(self.language)
        self.bottom_status["server"].set_label(t("status.server", self.language))
        self.bottom_status["telemetry"].set_label(t("status.telemetry", self.language))
        self.bottom_status["video"].set_label(t("status.video", self.language))
        self.bottom_status["autopilot"].set_label(t("status.autopilot", self.language))
        self.bottom_status["gps"].set_label(t("status.gps", self.language))
        self.bottom_status["orin"].set_label(t("status.orin", self.language))
        if self.link_meters["bulk"]._value <= 0:
            self.link_meters["bulk"].set_unavailable(
                t("video.link.waiting", self.language)
            )
        for chip in self.bottom_status.values():
            chip.setToolTip(t("connection.tooltip.open", self.language))

    def _on_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        if self._shutdown_prepared:
            return
        self._last_snapshot = snapshot
        self.telemetry_panel.update_snapshot(snapshot)
        self.command_panel.update_snapshot(snapshot)
        self.attitude_indicator.update_snapshot(snapshot)
        self.map_panel.update_snapshot(snapshot)
        self._update_flight_metrics(snapshot)

        link_freshness_s = float(
            self.settings_service.current.backend.mavlink.heartbeat_timeout
        )
        link_quality_available = snapshot.has_fresh_link_quality(link_freshness_s)
        packet_text = f"↓ {snapshot.packets_in} ↑ {snapshot.packets_out}"
        battery_alarm_active = self.battery_alarm_service.evaluate(
            snapshot,
            self.settings_service.current.backend.battery_alarm,
        )
        if (
            snapshot.telemetry_link
            and snapshot.autopilot_link
            and snapshot.battery_available
        ):
            self.battery_meter.set_battery(
                snapshot.battery_percent,
                snapshot.battery_voltage,
                snapshot.battery_current_amps,
                snapshot.battery_consumed_mah,
            )
            self.battery_meter.set_alarm(battery_alarm_active)
        else:
            self.battery_meter.set_unavailable()
        if snapshot.telemetry_link and snapshot.autopilot_link:
            self.link_meters["command"].set_value(
                snapshot.link_quality if link_quality_available else 0,
                packet_text,
            )
        else:
            self.link_meters["command"].set_unavailable(packet_text)
        warning_signature = "|".join(snapshot.warnings)
        if warning_signature and warning_signature != self._last_warning_signature:
            self.log_service.log("FLT", ", ".join(snapshot.warnings), "warning")
        self._last_warning_signature = warning_signature

    def _on_video_stats(self, stats: VideoStreamStats) -> None:
        if self._shutdown_prepared:
            return
        self.video_panel.set_stats(stats)
        if not stats.connected:
            detail = (
                t(
                    "video.link.frames",
                    self.language,
                    frames=stats.frames_received,
                )
                if stats.frames_received
                else t("video.link.waiting", self.language)
            )
            self.link_meters["bulk"].set_unavailable(detail)
            return
        bitrate = (
            f"{stats.bitrate_kbps / 1000.0:.1f} Mbps"
            if stats.bitrate_kbps >= 1000.0
            else f"{stats.bitrate_kbps:.0f} kbps"
        )
        self.link_meters["bulk"].set_value(
            stats.quality_percent,
            f"↓ {bitrate} · {stats.fps:.1f} FPS",
        )

    def _on_connection_status(self, status: ConnectionStatus) -> None:
        if self._shutdown_prepared:
            return
        chip = self.bottom_status.get(status.channel)
        if chip is not None:
            state = "success" if status.connected else "danger"
            chip.set_state(state)
            chip.set_label(t(f"status.{status.channel}", self.language))
            summary, detail = connection_status_text(status, self.language)
            chip.setToolTip(
                f"{summary}\n{detail}\n{t('connection.tooltip.open', self.language)}"
            )
        if status.channel == "video" and not status.connected:
            self.link_meters["bulk"].set_unavailable(
                t("video.link.waiting", self.language)
            )
        if self.connection_popup.isVisible() and self.connection_popup.channel == status.channel:
            self.connection_popup.refresh_status(status)
        self._offer_serial_access_repair(status)

    def _offer_serial_access_repair(self, status: ConnectionStatus) -> None:
        if status.channel != "telemetry":
            return
        device = str(status.options.get("serial_device", status.endpoint)).strip()
        if not bool(status.options.get("serial_permission_required", False)):
            if device:
                self._serial_access_prompted.discard(device)
            return
        if not device or device in self._serial_access_prompted:
            return
        self._serial_access_prompted.add(device)

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(t("serial.permission.title", self.language))
        dialog.setText(
            t("serial.permission.message", self.language, device=device)
        )
        dialog.setInformativeText(
            t("serial.permission.explanation", self.language)
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        yes_button = dialog.button(QMessageBox.StandardButton.Yes)
        no_button = dialog.button(QMessageBox.StandardButton.No)
        if yes_button is not None:
            yes_button.setText(t("serial.permission.confirm", self.language))
            yes_button.setProperty("role", "warning")
        if no_button is not None:
            no_button.setText(t("command.force_arm.cancel", self.language))

        if dialog.exec() != QMessageBox.StandardButton.Yes:
            self.ui_log.event(
                "serial.access_repair_rejected",
                "Operatör seri port erişim onarımını reddetti",
                severity="warning",
                details={"channel": status.channel, "device": device},
            )
            return

        self.ui_log.event(
            "serial.access_repair_confirmed",
            "Operatör seri port erişim onarımını onayladı",
            severity="warning",
            details={"channel": status.channel, "device": device},
        )
        self._serial_access_repair_running = True
        if self.connection_popup.isVisible() and self.connection_popup.channel == status.channel:
            self.connection_popup.set_busy(
                True,
                t("connection.busy.serial_permission", self.language),
            )
        self.connection_service.repair_serial_access(status.channel)

    def _open_connection_popup(self, channel: str, chip: StatusChip) -> None:
        status = self.connection_service.status(channel)
        self.connection_popup.show_status(status)
        self.connection_popup.adjustSize()
        origin = chip.mapToGlobal(QPoint(0, 0))
        screen = chip.screen().availableGeometry()
        x = min(max(screen.left() + 6, origin.x()), screen.right() - self.connection_popup.width() - 6)
        y = max(screen.top() + 6, origin.y() - self.connection_popup.height() - 7)
        self.connection_popup.move(x, y)
        self.connection_popup.show()
        self.connection_popup.raise_()

    def _reconnect_channel(self, channel: str, options: dict[str, object]) -> None:
        if channel == "telemetry":
            endpoint = str(options.get("endpoint", "")).strip()
            if endpoint:
                self._serial_access_prompted.discard(endpoint)
                try:
                    self._serial_access_prompted.discard(str(Path(endpoint).resolve()))
                except OSError:
                    pass
        self.connection_popup.set_busy(True)
        self.connection_service.reconnect(channel, options)

    def _reconnect_video_from_panel(self) -> None:
        backend = self.settings_service.current.backend
        self.connection_service.reconnect(
            "video",
            {
                "endpoint": backend.video_source_uri,
                "source_type": backend.video_source_type,
                "codec": backend.video_codec,
                "latency_ms": backend.video_latency_ms,
                "frame_timeout_s": backend.video_frame_timeout_s,
            },
        )

    def _disconnect_channel(self, channel: str) -> None:
        self.connection_popup.set_busy(
            True,
            t("connection.busy.disconnecting", self.language),
        )
        self.connection_service.disconnect(channel)

    def _on_connection_operation_started(self, channel: str) -> None:
        if channel == "video":
            self.video_panel.set_reconnect_busy(True)
        if self.connection_popup.isVisible() and self.connection_popup.channel == channel:
            self.connection_popup.set_busy(True)

    def _on_connection_operation_finished(
        self,
        channel: str,
        success: bool,
        message: str,
        status: ConnectionStatus,
    ) -> None:
        if channel == "video":
            self.video_panel.set_reconnect_busy(False)
        self._on_connection_status(status)
        if self.connection_popup.isVisible() and self.connection_popup.channel == channel:
            localized_summary, _detail = connection_status_text(status, self.language)
            self.connection_popup.finish_operation(
                success,
                localized_summary if success else message,
                status,
            )
        if self._serial_access_repair_running and channel == "telemetry":
            self._serial_access_repair_running = False
            if not success:
                QMessageBox.warning(
                    self,
                    t("serial.permission.title", self.language),
                    t(
                        "serial.permission.failed",
                        self.language,
                        message=message,
                    ),
                )

    def _open_settings_from_popup(self) -> None:
        self.connection_popup.close()
        self.open_settings()

    def _on_parameter_changed(self, key: str, value: object) -> None:
        del key, value

    def _on_parameters_apply(self, changes: dict[str, object]) -> None:
        self.parameter_service.update_values(changes)

    def _on_parameters_reset(self) -> None:
        return

    def _handle_map_storage_request(self, action: str) -> None:
        if action == "archive":
            self._open_mission_archive()
            return

        if action == "save":
            self._open_mission_archive()
            return

        if action == "load":
            self._open_mission_archive()

    def open_settings(self) -> None:
        previous = self.settings_service.current
        dialog = SettingsDialog(previous, self.language, self.theme, self)
        dialog.clear_cache_button.clicked.connect(self.settings_service.clear_cache)
        if dialog.exec():
            updated = dialog.to_settings()
            cache_changed = self._requires_cache_rebuild(previous, updated)
            self.settings_service.save(updated)
            if cache_changed:
                self.map_panel.rebuild_cache_profile()
            self.ui_log.event(
                "settings.updated",
                "Ayarlar güncellendi",
                severity="success",
            )

    def prepare_shutdown(self) -> None:
        if self._shutdown_prepared:
            return
        self._shutdown_prepared = True
        self.attitude_indicator.shutdown()
        self.battery_meter.set_alarm(False)
        self.connection_popup.hide()
        self.map_panel.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.prepare_shutdown()
        self.shutdown_requested.emit()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.sync_window_state()

    def _status_labels(self) -> dict[str, str]:
        return {
            "telemetry": t("status.telemetry", self.language),
            "video": t("status.video", self.language),
            "autopilot": t("status.autopilot", self.language),
            "server": t("status.server", self.language),
            "gps": t("status.gps", self.language),
        }

    def _update_flight_metrics(self, snapshot: TelemetrySnapshot) -> None:
        del snapshot

    def _open_mission_archive(self) -> None:
        dialog = MissionArchiveDialog(
            archive_service=self.archive_service,
            language=self.language,
            theme=self.theme,
            parent=self,
        )
        if not dialog.exec() or dialog.selected_path is None or dialog.selected_action is None:
            return
        if dialog.selected_action == "save":
            self._save_mission_plan(dialog.selected_path)
            return
        if dialog.selected_action == "load":
            self._load_mission_plan(dialog.selected_path)

    def _save_mission_plan(self, path: Path) -> None:
        state = getattr(self.planner_service, "state", None)
        if state is None:
            self.ui_log.event(
                "archive.no_plan",
                "Kaydedilecek görev planı bulunamadı",
                severity="warning",
            )
            return
        try:
            saved_path = self.archive_service.save_plan(path, state)
        except OSError as exc:
            self.ui_log.event(
                "archive.save_failed",
                f"Görev planı kaydedilemedi: {exc}",
                severity="danger",
                details={"error": str(exc)},
            )
            return
        self.ui_log.event(
            "archive.saved",
            f"Görev planı kaydedildi: {saved_path.name}",
            severity="success",
            details={"name": saved_path.name},
        )

    def _load_mission_plan(self, path: Path) -> None:
        try:
            state = self.archive_service.load_plan(path)
        except (OSError, ValueError, TypeError) as exc:
            self.ui_log.event(
                "archive.load_failed",
                f"Görev planı yüklenemedi: {exc}",
                severity="danger",
                details={"error": str(exc)},
            )
            return
        if hasattr(self.planner_service, "load_state"):
            self.planner_service.load_state(state)
        self.settings_service.patch(map_layer=state.base_layer)
        self.ui_log.event(
            "archive.loaded",
            f"Görev planı yüklendi: {path.name}",
            severity="success",
            details={"name": path.name},
        )

    def _requires_cache_rebuild(self, previous: SettingsModel, updated: SettingsModel) -> bool:
        return (
            previous.cache_path != updated.cache_path
            or previous.cache_limit_mb != updated.cache_limit_mb
            or previous.cache_enabled != updated.cache_enabled
        )
