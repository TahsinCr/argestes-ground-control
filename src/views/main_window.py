from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.config.defaults import default_parameters
from src.config.i18n import t
from src.models.settings import SettingsModel
from src.models.telemetry import TelemetrySnapshot
from src.services.base import CommandService, MissionService, TelemetryService, VideoService
from src.services.logging_service import LoggingService
from src.services.mission_archive import MissionArchiveService
from src.services.settings import SettingsService
from src.themes.style import ThemePalette, apply_theme
from src.views.archive_dialog import MissionArchiveDialog
from src.views.settings_dialog import SettingsDialog
from src.widgets.attitude import AttitudeIndicator
from src.widgets.common import PanelFrame, SignalMeter, StatusChip
from src.widgets.control_panels import CommandPanel, TelemetryPanel
from src.widgets.logs import EventLogPanel
from src.widgets.map_panel import MapPanel
from src.widgets.parameters import ParametersPanel
from src.widgets.title_bar import AppTitleBar
from src.widgets.video import VideoPanel


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        log_service: LoggingService,
        settings_service: SettingsService,
        telemetry_service: TelemetryService,
        mission_service: MissionService,
        command_service: CommandService,
        video_service: VideoService,
        base_dir: Path,
        theme: ThemePalette,
    ) -> None:
        super().__init__()
        self.log_service = log_service
        self.settings_service = settings_service
        self.telemetry_service = telemetry_service
        self.mission_service = mission_service
        self.command_service = command_service
        self.video_service = video_service
        self.base_dir = base_dir
        self.theme = theme
        self.language = settings_service.current.language
        self.ui_log = log_service.child("UI")
        self.archive_service = MissionArchiveService(base_dir)
        self._last_warning_signature = ""

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.resize(1600, 940)
        self.setMinimumSize(1380, 840)
        self._build_ui()
        self._connect_signals()
        self.parameters_panel.set_parameters(default_parameters())
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
        }
        for chip in self.bottom_status.values():
            chip.set_theme(self.theme)
            status_layout.addWidget(chip)
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
        self.command_panel = CommandPanel("", "", self)
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
        self.map_panel.storage_requested.connect(self._handle_map_storage_request)
        self.map_panel.info_generated.connect(
            lambda severity, message: self.log_service.log("MAP", message, severity)
        )

        self.log_service.entry_emitted.connect(self.log_panel.add_entry)
        self.parameters_panel.parameter_changed.connect(self._on_parameter_changed)
        self.parameters_panel.apply_requested.connect(self._on_parameters_apply)
        self.parameters_panel.reset_requested.connect(self._on_parameters_reset)
        self.settings_service.settings_changed.connect(self.apply_settings)

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
                    "land": settings.default_radius_land,
                    "hss": settings.default_radius_hss,
                }
            )
        for meter in self.link_meters.values():
            meter.set_theme(self.theme)
        for chip in self.bottom_status.values():
            chip.set_theme(self.theme)
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

    def _on_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self.telemetry_panel.update_snapshot(snapshot)
        self.command_panel.update_snapshot(snapshot)
        self.attitude_indicator.update_snapshot(snapshot)
        self.map_panel.update_snapshot(snapshot)
        self._update_flight_metrics(snapshot)

        command_quality = snapshot.link_quality if snapshot.telemetry_link or snapshot.autopilot_link else 0
        bulk_quality = int(min(100, max(0, snapshot.bandwidth_kbps / 120.0 * 100))) if snapshot.video_link else 0
        down_kbps = snapshot.bandwidth_kbps if snapshot.video_link else 0.0
        up_kbps = max(0.0, snapshot.bandwidth_kbps * 0.08) if snapshot.video_link else 0.0
        packet_text = f"↓ {snapshot.packets_in} ↑ {snapshot.packets_out}"
        self.link_meters["command"].set_value(command_quality, packet_text)
        self.link_meters["bulk"].set_value(bulk_quality, f"↓ {down_kbps:0.0f} ↑ {up_kbps:0.0f} kbps")

        self.bottom_status["server"].set_state("success" if snapshot.server_link else "danger")
        self.bottom_status["telemetry"].set_state("success" if snapshot.telemetry_link else "danger")
        self.bottom_status["video"].set_state("success" if snapshot.video_link else "danger")
        self.bottom_status["autopilot"].set_state("success" if snapshot.autopilot_link else "danger")
        self.bottom_status["gps"].set_state("success" if snapshot.gps_satellites >= 14 else "warning")

        warning_signature = "|".join(snapshot.warnings)
        if warning_signature and warning_signature != self._last_warning_signature:
            self.log_service.log("FLT", ", ".join(snapshot.warnings), "warning")
        self._last_warning_signature = warning_signature

    def _on_parameter_changed(self, key: str, value: object) -> None:
        self.log_service.log("PARAM", f"{key} hazirlandi -> {value}", "info")

    def _on_parameters_apply(self, changes: dict[str, object]) -> None:
        self.command_service.issue_command("PARAM_APPLY", changes)
        self.log_service.log("PARAM", f"{len(changes)} parametre uygulanmak uzere hazirlandi", "success")

    def _on_parameters_reset(self) -> None:
        self.log_service.log("PARAM", "Parametre degisiklikleri sifirlandi", "warning")

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
