from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, Qt
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

from src.ui.config.i18n import t
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.models.connections import ConnectionStatus
from src.services.contracts import CommandService, MissionService, TelemetryService, VideoService
from src.services.logging_service import LoggingService
from src.services.mission_archive import MissionArchiveService
from src.services.settings import SettingsService
from src.ui.themes.style import ThemePalette, apply_theme
from src.ui.views.archive_dialog import MissionArchiveDialog
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.attitude import AttitudeIndicator
from src.ui.widgets.common import PanelFrame, SignalMeter, StatusChip
from src.ui.widgets.connection_popup import ConnectionPopup
from src.ui.widgets.control_panels import CommandPanel, TelemetryPanel
from src.ui.widgets.logs import EventLogPanel
from src.ui.widgets.map_panel import MapPanel
from src.ui.widgets.parameters import ParametersPanel
from src.ui.widgets.title_bar import AppTitleBar
from src.ui.widgets.video import VideoPanel


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        log_service: LoggingService,
        settings_service: SettingsService,
        telemetry_service: TelemetryService,
        mission_service: MissionService,
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
        self.mission_service = mission_service
        self.command_service = command_service
        self.parameter_service = parameter_service
        self.video_service = video_service
        self.connection_service = connection_service
        self.tile_cache_service = tile_cache_service
        self.base_dir = base_dir
        self.theme = theme
        self.language = settings_service.current.language
        self.ui_log = log_service.child("UI")
        self.archive_service = archive_service
        self._last_warning_signature = ""
        self._last_snapshot = telemetry_service.current
        self._serial_access_prompted: set[str] = set()
        self._serial_access_repair_running = False

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.resize(1600, 940)
        self.setMinimumSize(1380, 840)
        self._build_ui()
        self.connection_popup = ConnectionPopup(self.theme, self)
        self._connect_signals()
        self.parameters_panel.set_parameters(self.parameter_service.current)
        self.apply_settings(settings_service.current)
        self.ui_log.success("Arayuz baslatildi, log akisi aktif")

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

        self.link_meters = {
            "command": SignalMeter("KOMUT LINKI", self),
            "bulk": SignalMeter("YUKSEK BANT", self),
        }
        for meter in self.link_meters.values():
            meter.set_theme(self.theme)
            status_layout.addWidget(meter)

        self.settings_button = QPushButton("⚙", self)
        self.settings_button.setObjectName("SettingsIconButton")
        self.settings_button.setToolTip("Ayarlar")
        self.settings_button.setProperty("compact", "true")
        self.settings_button.setFixedWidth(42)
        self.settings_button.clicked.connect(self.open_settings)
        status_layout.addWidget(self.settings_button)
        return status_bar

    def _build_main_splitter(self) -> QSplitter:
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setHandleWidth(1)
        main_splitter.addWidget(self._build_sidebar())
        main_splitter.addWidget(self._build_workspace())
        main_splitter.setStretchFactor(0, 28)
        main_splitter.setStretchFactor(1, 72)
        main_splitter.setSizes([350, 1250])
        return main_splitter

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
        sidebar_layout.addWidget(self.telemetry_panel, 3)
        sidebar_layout.addWidget(self.command_panel, 4)
        return sidebar

    def _build_workspace(self) -> QWidget:
        workspace = QWidget(self)
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._build_visuals_splitter(), 8)
        workspace_layout.addWidget(self._build_bottom_splitter(), 4)
        return workspace

    def _build_visuals_splitter(self) -> QSplitter:
        visuals_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        visuals_splitter.setChildrenCollapsible(False)
        visuals_splitter.setHandleWidth(1)

        self.map_panel = MapPanel(
            "",
            "",
            theme=self.theme,
            language=self.language,
            base_dir=self.base_dir,
            mission_service=self.mission_service,
            settings_service=self.settings_service,
            tile_cache_service=self.tile_cache_service,
            parent=self,
        )
        self.video_panel = VideoPanel("", "", self)
        visuals_splitter.addWidget(self.map_panel)
        visuals_splitter.addWidget(self.video_panel)
        visuals_splitter.setStretchFactor(0, 1)
        visuals_splitter.setStretchFactor(1, 1)
        visuals_splitter.setSizes([760, 760])
        return visuals_splitter

    def _build_bottom_splitter(self) -> QSplitter:
        bottom_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        bottom_splitter.setChildrenCollapsible(False)
        bottom_splitter.setHandleWidth(1)
        self.log_panel = EventLogPanel("", "", "", self)
        self.log_panel.setMinimumWidth(460)
        bottom_splitter.addWidget(self.log_panel)

        self.parameters_panel = ParametersPanel("", "", "", self)
        self.parameters_panel.setMinimumWidth(320)
        bottom_splitter.addWidget(self.parameters_panel)
        bottom_splitter.setStretchFactor(0, 64)
        bottom_splitter.setStretchFactor(1, 36)
        bottom_splitter.setSizes([900, 500])
        return bottom_splitter

    def _build_flight_panel(self) -> PanelFrame:
        self.flight_panel = PanelFrame("", "", self)
        self.flight_panel.setMinimumWidth(300)
        self.attitude_indicator = AttitudeIndicator(self.theme, self.flight_panel)
        self.flight_panel.body_layout.addWidget(self.attitude_indicator, 1)
        return self.flight_panel

    def _connect_signals(self) -> None:
        self.telemetry_service.telemetry_updated.connect(self._on_telemetry)
        self.video_service.frame_ready.connect(self.video_panel.set_frame)
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
        self.command_panel.command_started(code)
        self.map_panel.command_started(code, parameters)

    def _on_command_completed(self, result) -> None:
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
        if hasattr(self.mission_service, "set_default_radii"):
            self.mission_service.set_default_radii(
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
        for chip in self.bottom_status.values():
            chip.set_theme(self.theme)
        self.connection_popup.set_theme(self.theme)
        self.title_bar.set_theme(self.theme)
        self.telemetry_panel.set_theme(self.theme)
        self.attitude_indicator.set_theme(self.theme)
        self.map_panel.set_theme(self.theme)
        self.map_panel.retranslate(self.language)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(t("app.title", self.language))
        self.title_bar.set_title(t("app.title", self.language))
        self.settings_button.setToolTip(t("status.settings", self.language))

        self.telemetry_panel.set_title(
            t("panel.telemetry", self.language),
            t("panel.telemetry.subtitle", self.language),
        )
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
        self.parameters_panel.set_title(
            t("panel.parameters", self.language),
            t("panel.parameters.subtitle", self.language),
        )
        self.parameters_panel.search_input.setPlaceholderText(t("search.placeholder", self.language))
        self.parameters_panel.set_language(self.language)
        self.link_meters["command"].set_label(t("status.command_link", self.language))
        self.link_meters["bulk"].set_label(t("status.payload_link", self.language))
        self.bottom_status["server"].set_label(t("status.server", self.language))
        self.bottom_status["telemetry"].set_label(t("status.telemetry", self.language))
        self.bottom_status["video"].set_label(t("status.video", self.language))
        self.bottom_status["autopilot"].set_label(t("status.autopilot", self.language))
        self.bottom_status["gps"].set_label(t("status.gps", self.language))
        self.bottom_status["orin"].set_label(t("status.orin", self.language))

    def _on_telemetry(self, snapshot: TelemetrySnapshot) -> None:
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
        bulk_quality = int(min(100, max(0, snapshot.bandwidth_kbps / 120.0 * 100))) if snapshot.video_link else 0
        down_kbps = snapshot.bandwidth_kbps if snapshot.video_link else 0.0
        up_kbps = max(0.0, snapshot.bandwidth_kbps * 0.08) if snapshot.video_link else 0.0
        packet_text = f"↓ {snapshot.packets_in} ↑ {snapshot.packets_out}"
        if snapshot.telemetry_link and snapshot.autopilot_link:
            self.link_meters["command"].set_value(
                snapshot.link_quality if link_quality_available else 0,
                packet_text,
            )
        else:
            self.link_meters["command"].set_unavailable(packet_text)
        self.link_meters["bulk"].set_value(bulk_quality, f"↓ {down_kbps:0.0f} ↑ {up_kbps:0.0f} kbps")

        warning_signature = "|".join(snapshot.warnings)
        if warning_signature and warning_signature != self._last_warning_signature:
            self.log_service.log("FLT", ", ".join(snapshot.warnings), "warning")
        self._last_warning_signature = warning_signature

    def _on_connection_status(self, status: ConnectionStatus) -> None:
        chip = self.bottom_status.get(status.channel)
        if chip is not None:
            state = "success" if status.connected else "danger"
            chip.set_state(state)
            chip.set_label(t(f"status.{status.channel}", self.language))
            chip.setToolTip(f"{status.summary}\n{status.detail}\nTıklayın: bağlantı ayarları")
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
        dialog.setWindowTitle("AREF telemetri erişimi")
        dialog.setText(f"Telemetri seri cihazına erişim izni yok:\n{device}")
        dialog.setInformativeText(
            "Yalnızca mevcut kullanıcıya okuma/yazma ACL izni verilip "
            "telemetri bağlantısı yeniden denensin mi? Sistem yetkilendirme "
            "penceresi açılacaktır; cihaz herkese açılmayacaktır."
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        yes_button = dialog.button(QMessageBox.StandardButton.Yes)
        no_button = dialog.button(QMessageBox.StandardButton.No)
        if yes_button is not None:
            yes_button.setText("Zorla bağlan")
            yes_button.setProperty("role", "warning")
        if no_button is not None:
            no_button.setText("İptal")

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
            self.connection_popup.set_busy(True, "Seri port yetkisi alınıyor…")
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

    def _disconnect_channel(self, channel: str) -> None:
        self.connection_popup.set_busy(True, "Bağlantı kapatılıyor…")
        self.connection_service.disconnect(channel)

    def _on_connection_operation_started(self, channel: str) -> None:
        if self.connection_popup.isVisible() and self.connection_popup.channel == channel:
            self.connection_popup.set_busy(True)

    def _on_connection_operation_finished(
        self,
        channel: str,
        success: bool,
        message: str,
        status: ConnectionStatus,
    ) -> None:
        self._on_connection_status(status)
        if self.connection_popup.isVisible() and self.connection_popup.channel == channel:
            self.connection_popup.finish_operation(success, message, status)
        if self._serial_access_repair_running and channel == "telemetry":
            self._serial_access_repair_running = False
            if not success:
                QMessageBox.warning(
                    self,
                    "AREF telemetri erişimi",
                    f"Seri port erişimi verilemedi veya bağlantı başlatılamadı:\n{message}",
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
            self.ui_log.success("Ayarlar guncellendi")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.telemetry_service.stop()
        self.video_service.stop()
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
        state = getattr(self.mission_service, "state", None)
        if state is None:
            self.log_service.log("MISSION", "Kaydedilecek gorev plani bulunamadi", "warning")
            return
        try:
            saved_path = self.archive_service.save_plan(path, state)
        except OSError as exc:
            self.log_service.log("MISSION", f"Gorev plani kaydedilemedi: {exc}", "danger")
            return
        self.log_service.log("MISSION", f"Gorev plani kaydedildi -> {saved_path.name}", "success")

    def _load_mission_plan(self, path: Path) -> None:
        try:
            state = self.archive_service.load_plan(path)
        except (OSError, ValueError, TypeError) as exc:
            self.log_service.log("MISSION", f"Gorev plani yuklenemedi: {exc}", "danger")
            return
        if hasattr(self.mission_service, "load_state"):
            self.mission_service.load_state(state)
        self.settings_service.patch(map_layer=state.base_layer)
        self.log_service.log("MISSION", f"Gorev plani yuklendi -> {path.name}", "success")

    def _requires_cache_rebuild(self, previous: SettingsModel, updated: SettingsModel) -> bool:
        return (
            previous.cache_path != updated.cache_path
            or previous.cache_limit_mb != updated.cache_limit_mb
            or previous.cache_enabled != updated.cache_enabled
        )
