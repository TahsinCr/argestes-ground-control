from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.core.dependency import DependencyContainer
from src.core.events import Subscription
from src.core.mavlink import MavlinkClient, MavlinkEndpoint
from src.models.mission import MissionState
from src.services.catalogs import configured_orin_commands, default_parameters
from src.services.contracts.archive import MissionArchiveRepository
from src.services.contracts.commands import CommandService
from src.services.contracts.competition import CompetitionService
from src.services.contracts.connections import ConnectionControlService
from src.services.contracts.logging import LogService
from src.services.contracts.kamikaze import KamikazeQrService
from src.services.contracts.mission import MissionService
from src.services.contracts.orin import OrinService
from src.services.contracts.parameters import ParameterService
from src.services.contracts.settings import SettingsRepository
from src.services.contracts.telemetry import TelemetryService
from src.services.contracts.target_lock import TargetLockService, TargetLockVisionService
from src.services.contracts.tile_cache import TileCacheService
from src.services.contracts.vehicle import VehicleService
from src.services.contracts.video import (
    PhoneCameraBridgeService,
    QrCodeService,
    VideoService,
)
from src.services.facade import BackendFacade
from src.services.phone_camera import ScrcpyPhoneCameraBridge
from src.services.competition import TeknofestCompetitionService
from src.services.logging_service import LoggingService, ServiceLogHandler
from src.services.mission_archive import MissionArchiveService
from src.services.orchestration.commands import DispatchCommandService
from src.services.orchestration.connections import BackendConnectionControlService
from src.services.orchestration.kamikaze import KamikazeQrWorkflow
from src.services.orchestration.target_lock import TargetLockWorkflow
from src.services.processing.mission import MissionStateService
from src.services.processing.parameters import MutableParameterService, VehicleParameterService
from src.services.processing.telemetry import (
    MockTelemetryService,
    MutableTelemetryService,
    VehicleTelemetryService,
)
from src.services.processing.target_lock import TargetLockVideoService
from src.services.processing.video import WeChatQrVideoService
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer
from src.services.vehicle import (
    MavlinkOrinService,
    MavlinkVehicleService,
    NullOrinService,
    NullVehicleService,
    SimulatedOrinService,
)
from src.services.video import (
    AutoReconnectVideoService,
    GStreamerVideoService,
    MutableVideoService,
)


@dataclass(slots=True)
class RuntimeServices:
    container: DependencyContainer
    log_service: LoggingService
    settings_service: SettingsService
    vehicle_service: VehicleService
    telemetry_service: TelemetryService
    mission_service: MissionService
    orin_service: OrinService
    command_service: CommandService
    parameter_service: ParameterService
    video_service: VideoService
    phone_camera_service: PhoneCameraBridgeService
    kamikaze_service: KamikazeQrService
    target_lock_service: TargetLockService
    tile_cache_service: TileCacheServer
    competition_service: CompetitionService
    connection_service: ConnectionControlService
    archive_service: MissionArchiveService
    facade: BackendFacade
    _subscriptions: list[Subscription] = field(default_factory=list, repr=False)
    _python_handler: ServiceLogHandler | None = field(default=None, repr=False)
    _started: bool = field(default=False, repr=False)
    _starting: bool = field(default=False, repr=False)
    _stop_requested: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
    )
    _lifecycle_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
    )
    _stop_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started or self._starting:
                return
            self._starting = True
            self._stop_requested.clear()
        self._install_python_logging()
        try:
            self._start_services()
        finally:
            cancelled = self._stop_requested.is_set()
            with self._lifecycle_lock:
                self._starting = False
                self._started = not cancelled
            if cancelled:
                self._stop_services()

    def _start_services(self) -> None:
        for name, service in self._service_start_order():
            if self._stop_requested.is_set():
                return
            try:
                service.start()
            except Exception as exc:
                self.log_service.event(
                    "RUNTIME",
                    f"runtime.{name}_start_failed",
                    f"{name} servisi başlatılamadı: {exc}",
                    severity="danger",
                )
        settings = self.settings_service.current
        if (
            not self._stop_requested.is_set()
            and not settings.backend.mock_mode
            and settings.backend.mavlink.auto_connect
        ):
            try:
                self.vehicle_service.start()
            except Exception as exc:
                self.log_service.event(
                    "VEHICLE",
                    "vehicle.connect_failed",
                    f"Uçuş cihazı bağlantısı kurulamadı: {exc}",
                    severity="danger",
                    details={"endpoint": settings.backend.mavlink.endpoint},
                )

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._started and not self._starting:
                return
            self._stop_requested.set()
            self._started = False
        self._stop_services()

    def _stop_services(self) -> None:
        with self._stop_lock:
            for name, service in self._service_stop_order():
                try:
                    service.stop()
                except Exception as exc:
                    self.log_service.event(
                        "RUNTIME",
                        f"runtime.{name}_stop_failed",
                        f"{name} servisi durdurulamadı: {exc}",
                        severity="danger",
                    )
            self._remove_python_logging()

    def _service_start_order(self):
        return (
            ("tile_cache", self.tile_cache_service),
            ("mission", self.mission_service),
            ("phone_camera", self.phone_camera_service),
            ("video", self.video_service),
            ("telemetry", self.telemetry_service),
            ("competition", self.competition_service),
            ("parameters", self.parameter_service),
            ("orin", self.orin_service),
            ("connections", self.connection_service),
            ("target_lock", self.target_lock_service),
            ("kamikaze", self.kamikaze_service),
        )

    def _service_stop_order(self):
        return (
            ("kamikaze", self.kamikaze_service),
            ("target_lock", self.target_lock_service),
            ("connections", self.connection_service),
            ("competition", self.competition_service),
            ("parameters", self.parameter_service),
            ("orin", self.orin_service),
            ("telemetry", self.telemetry_service),
            ("mission", self.mission_service),
            ("vehicle", self.vehicle_service),
            ("video", self.video_service),
            ("phone_camera", self.phone_camera_service),
            ("tile_cache", self.tile_cache_service),
        )

    close = stop

    def _install_python_logging(self) -> None:
        if self._python_handler is not None:
            return
        self._python_handler = self.log_service.create_python_handler("PYTHON")
        self._python_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logging.getLogger().addHandler(self._python_handler)

    def _remove_python_logging(self) -> None:
        if self._python_handler is None:
            return
        logging.getLogger().removeHandler(self._python_handler)
        self._python_handler.close()
        self._python_handler = None


def create_runtime_services(base_dir: Path) -> RuntimeServices:
    log_service = LoggingService(log_path=base_dir / "data" / "logs" / "yki.jsonl")
    settings_service = SettingsService(base_dir=base_dir, logger=log_service.child("SETTINGS"))
    settings = settings_service.current
    try:
        orin_command_codes = configured_orin_commands(base_dir / "config.json")
    except (OSError, ValueError) as exc:
        orin_command_codes = ()
        log_service.event(
            "ORIN",
            "orin.command_catalog_failed",
            f"Orin komut kataloğu yüklenemedi: {exc}",
            severity="danger",
            details={"config_path": str(base_dir / "config.json")},
        )
    else:
        if orin_command_codes:
            log_service.event(
                "ORIN",
                "orin.command_catalog_loaded",
                f"{len(orin_command_codes)} yapılandırılmış Orin komutu yüklendi",
                severity="success",
                details={"commands": list(orin_command_codes)},
            )

    mavlink_client: MavlinkClient | None = None
    if not settings.backend.mock_mode:
        mavlink_settings = settings.backend.mavlink
        endpoint = MavlinkEndpoint(
            uri=mavlink_settings.endpoint,
            baud=mavlink_settings.baud,
            heartbeat_timeout=float(mavlink_settings.heartbeat_timeout),
            dialect=mavlink_settings.dialect,
            source_system=mavlink_settings.source_system,
            source_component=mavlink_settings.source_component,
            autoreconnect=mavlink_settings.autoreconnect,
        )
        mavlink_client = MavlinkClient(endpoint)
        vehicle_service: VehicleService = MavlinkVehicleService(
            mavlink_client,
            log_service.child("VEHICLE"),
            operation_timeout=float(mavlink_settings.operation_timeout),
            default_mission_altitude=float(settings.backend.mission.default_altitude_m),
        )
        telemetry_service: TelemetryService = VehicleTelemetryService(
            vehicle_service,
            log_service.child("TELEMETRY"),
        )
        parameter_service: ParameterService = VehicleParameterService(
            default_parameters(),
            vehicle_service,
            log_service.child("PARAMETER"),
            write_timeout=float(mavlink_settings.operation_timeout),
            refresh_on_connect=mavlink_settings.sync_parameters_on_connect,
        )
        if settings.backend.orin.simulate_connected:
            orin_service = SimulatedOrinService(
                settings.backend.orin,
                log_service.child("ORIN"),
            )
        else:
            orin_service = MavlinkOrinService(
                mavlink_client,
                vehicle_service,
                settings.backend.orin,
                log_service.child("ORIN"),
            )
    else:
        vehicle_service = NullVehicleService()
        telemetry_service = (
            MockTelemetryService(log_service.child("TELEMETRY"))
            if settings.backend.mock_mode
            else MutableTelemetryService(log_service.child("TELEMETRY"))
        )
        parameter_service = MutableParameterService(default_parameters())
        orin_service = (
            SimulatedOrinService(
                settings.backend.orin,
                log_service.child("ORIN"),
            )
            if settings.backend.orin.simulate_connected
            else NullOrinService()
        )

    mission_service = MissionStateService(
        log_service.child("MISSION"),
        initial_state=None if settings.backend.mock_mode else MissionState(),
        default_radii=settings.backend.mission.radii,
        vehicle=vehicle_service if mavlink_client is not None else None,
        sync_from_vehicle_on_connect=settings.backend.mavlink.sync_mission_on_connect,
    )
    command_service = DispatchCommandService(
        vehicle_service,
        parameter_service,
        log_service.child("COMMAND"),
        mission=mission_service,
        orin=orin_service,
        configured_orin_tasks=orin_command_codes,
    )
    phone_camera_service = ScrcpyPhoneCameraBridge(
        settings.backend.phone_camera,
        log_service.child("PHONE_CAMERA"),
    )
    qr_service = _create_qr_video_service(settings, log_service, base_dir)
    if not isinstance(qr_service, QrCodeService):
        raise TypeError("Video servisi QR işleme sözleşmesini sağlamıyor")
    video_service = TargetLockVideoService(
        qr_service,
        base_dir / "data" / "models" / "target_lock" / "yolo26s.pt",
        telemetry_service,
        log_service.child("TARGET_LOCK"),
    )
    tile_cache_service = TileCacheServer(
        cache_path=settings.backend.cache.path,
        enabled=settings.backend.cache.enabled,
        limit_mb=settings.backend.cache.limit_mb,
    )
    archive_service = MissionArchiveService(base_dir)
    competition_service = TeknofestCompetitionService(
        settings.backend.competition,
        telemetry_service,
        mission_service,
        vehicle_service,
        log_service.child("TEKNOFEST"),
    )
    connection_service = BackendConnectionControlService(
        settings=settings_service,
        vehicle=vehicle_service,
        telemetry=telemetry_service,
        video=video_service,
        phone_camera=phone_camera_service,
        competition=competition_service,
        orin=orin_service,
        logger=log_service.child("CONNECTION"),
    )
    kamikaze_service = KamikazeQrWorkflow(
        command_service,
        qr_service,
        competition_service,
        log_service.child("KAMIKAZE"),
    )
    target_lock_service = TargetLockWorkflow(
        command_service,
        video_service,
        competition_service,
        log_service.child("TARGET_LOCK"),
    )

    container = DependencyContainer()
    container.instance(LogService, log_service)
    container.instance(SettingsRepository, settings_service)
    container.instance(VehicleService, vehicle_service)
    container.instance(TelemetryService, telemetry_service)
    container.instance(MissionService, mission_service)
    container.instance(OrinService, orin_service)
    container.instance(CommandService, command_service)
    container.instance(ParameterService, parameter_service)
    container.instance(VideoService, video_service)
    container.instance(PhoneCameraBridgeService, phone_camera_service)
    container.instance(QrCodeService, qr_service)
    container.instance(KamikazeQrService, kamikaze_service)
    container.instance(TargetLockVisionService, video_service)
    container.instance(TargetLockService, target_lock_service)
    container.instance(TileCacheService, tile_cache_service)
    container.instance(CompetitionService, competition_service)
    container.instance(ConnectionControlService, connection_service)
    container.instance(MissionArchiveRepository, archive_service)
    if mavlink_client is not None:
        container.instance(MavlinkClient, mavlink_client)

    facade = BackendFacade(
        telemetry=container.resolve(TelemetryService),
        competition=container.resolve(CompetitionService),
        mission=container.resolve(MissionService),
        orin=container.resolve(OrinService),
        commands=container.resolve(CommandService),
        parameters=container.resolve(ParameterService),
        settings=container.resolve(SettingsRepository),
        target_lock=container.resolve(TargetLockService),
        target_lock_vision=container.resolve(TargetLockVisionService),
    )
    runtime = RuntimeServices(
        container=container,
        log_service=log_service,
        settings_service=settings_service,
        vehicle_service=vehicle_service,
        telemetry_service=telemetry_service,
        mission_service=mission_service,
        orin_service=orin_service,
        command_service=command_service,
        parameter_service=parameter_service,
        video_service=video_service,
        phone_camera_service=phone_camera_service,
        kamikaze_service=kamikaze_service,
        target_lock_service=target_lock_service,
        tile_cache_service=tile_cache_service,
        competition_service=competition_service,
        connection_service=connection_service,
        archive_service=archive_service,
        facade=facade,
    )

    def apply_settings(updated) -> None:
        mission_service.set_default_radii(updated.backend.mission.radii)
        vehicle_service.set_default_mission_altitude(
            updated.backend.mission.default_altitude_m
        )
        tile_cache_service.configure(
            cache_path=updated.backend.cache.path,
            enabled=updated.backend.cache.enabled,
            limit_mb=updated.backend.cache.limit_mb,
        )
        competition_service.configure(updated.backend.competition)
        orin_service.configure(updated.backend.orin)
        phone_camera_service.configure(updated.backend.phone_camera)

    runtime._subscriptions.append(settings_service.changed.subscribe(apply_settings))
    return runtime


def _create_qr_video_service(
    settings,
    log_service: LoggingService,
    base_dir: Path,
) -> VideoService:
    backend = settings.backend
    if backend.mock_mode or backend.video_source_type == "mock":
        resilient_source: VideoService = MutableVideoService(
            log_service.child("VIDEO")
        )
    else:
        source = GStreamerVideoService(
            backend.video_source_type,
            backend.video_source_uri,
            log_service.child("VIDEO"),
            latency_ms=backend.video_latency_ms,
            codec=backend.video_codec,
            frame_timeout_s=backend.video_frame_timeout_s,
        )
        resilient_source = AutoReconnectVideoService(
            source,
            log_service.child("VIDEO"),
            retry_interval_s=max(1.0, float(backend.video_frame_timeout_s)),
        )
    return WeChatQrVideoService(
        resilient_source,
        base_dir / "data" / "models" / "wechat_qrcode",
        log_service.child("QR"),
    )
