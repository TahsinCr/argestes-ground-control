from __future__ import annotations

import os
import pwd
import re
import shutil
import stat
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.events import EventBus, Subscription
from src.models.competition import CompetitionServerConfig
from src.models.connections import ConnectionStatus
from src.models.vehicle import VehicleConnectionConfig, VehicleConnectionState
from src.services.abstracts.competition import CompetitionService
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.orin import OrinService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.abstracts.video import VideoService
from src.services.logging_service import ServiceLogger


VEHICLE_CHANNELS = frozenset({"telemetry", "autopilot", "gps", "orin"})
ALL_CHANNELS = frozenset({"server", "video", *VEHICLE_CHANNELS})


class BackendConnectionControlService(ConnectionControlService):
    """Execute every connection action independently from UI technology."""

    def __init__(
        self,
        *,
        settings: SettingsRepository,
        vehicle: VehicleService,
        telemetry: TelemetryService,
        video: VideoService,
        competition: CompetitionService,
        orin: OrinService | None = None,
        logger: ServiceLogger | None = None,
    ) -> None:
        self._settings = settings
        self._vehicle = vehicle
        self._telemetry = telemetry
        self._video = video
        self._competition = competition
        self._orin = orin
        self._logger = logger
        self._changed = EventBus[ConnectionStatus]()
        self._subscriptions: list[Subscription] = []
        self._operation_lock = threading.RLock()
        self._health_stop = threading.Event()
        self._health_thread: threading.Thread | None = None

    @property
    def changed(self) -> EventBus[ConnectionStatus]:
        return self._changed

    def start(self) -> None:
        if self._subscriptions:
            return
        self._subscriptions.extend(
            [
                self._vehicle.connection_changed.subscribe(self._on_vehicle_changed),
                self._video.connection_changed.subscribe(lambda _connected: self._publish("video")),
                self._competition.changed.subscribe(lambda _state: self._publish("server")),
            ]
        )
        if self._orin is not None:
            self._subscriptions.append(
                self._orin.changed.subscribe(lambda _state: self._publish("orin"))
            )
        self._health_stop.clear()
        self._health_thread = threading.Thread(
            target=self._watch_health,
            name="ConnectionHealthWatchdog",
            daemon=True,
        )
        self._health_thread.start()
        self._publish_all()

    def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        self._health_stop.set()
        thread = self._health_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._health_thread = None

    def status(self, channel: str) -> ConnectionStatus:
        key = self._normalize(channel)
        settings = self._settings.current
        if key == "server":
            competition = settings.backend.competition
            state = self._competition.state
            success_age = self._age_seconds(state.last_success_at)
            freshness_limit = max(
                competition.timeout_s * 2.0,
                competition.telemetry_period_ms / 1000.0 + competition.timeout_s + 1.0,
            )
            connected = bool(
                state.connected
                and state.authenticated
                and success_age is not None
                and success_age <= freshness_limit
            )
            return ConnectionStatus(
                key,
                connected,
                "TEKNOFEST sunucusu bağlı" if connected else "TEKNOFEST sunucusuna ulaşılamıyor",
                (
                    (
                        f"{state.competitor_count} rakip İHA / "
                        f"{state.hss_count} HSS / "
                        f"QR {'hazır' if state.qr_available else 'bekleniyor'} / "
                        f"SİHA TX {'aktif' if state.telemetry_tx_active else 'bekliyor'} / "
                        f"son yanıt {success_age:.1f} sn önce"
                    )
                    if connected and success_age is not None
                    else (
                        state.last_error
                        or (
                            f"Sunucu yanıtı {success_age:.1f} sn önce; bağlantı bayat"
                            if success_age is not None
                            else "Yarışma sunucusu oturumu kapalı"
                        )
                    )
                ),
                competition.base_url,
                competition.timeout_s,
                {
                    "username": competition.username,
                    "team_number": competition.team_number,
                    "telemetry_period_ms": competition.telemetry_period_ms,
                    "auto_connect": competition.auto_connect,
                    "running": state.running,
                    "authenticated": state.authenticated,
                    "last_success_age_s": success_age,
                    "freshness_limit_s": freshness_limit,
                    "competitor_count": state.competitor_count,
                    "hss_count": state.hss_count,
                    "qr_available": state.qr_available,
                    "telemetry_tx_active": state.telemetry_tx_active,
                    "session_health": state.session_health,
                    "telemetry_health": state.telemetry_health,
                    "clock_health": state.clock_health,
                    "qr_health": state.qr_health,
                    "hss_health": state.hss_health,
                },
                can_disconnect=state.running,
            )
        if key == "video":
            backend = settings.backend
            stats = self._video.stats
            stream_detail = (
                f"{stats.width}x{stats.height} / {stats.fps:.1f} FPS / "
                f"{stats.bitrate_kbps:.0f} kbps / kalite %{stats.quality_percent}"
                if stats.frames_received > 0
                else "Henüz video karesi alınmadı"
            )
            return ConnectionStatus(
                key,
                self._video.connected,
                "Video akışı bağlı" if self._video.connected else "Video akışı bekliyor",
                (
                    f"{backend.video_source_type.upper()} / "
                    f"{backend.video_codec.upper()} / {stream_detail}"
                ),
                backend.video_source_uri,
                float(backend.video_frame_timeout_s),
                {
                    "auto_connect": backend.video_auto_connect,
                    "source_type": backend.video_source_type,
                    "codec": backend.video_codec,
                    "latency_ms": backend.video_latency_ms,
                    "frame_timeout_s": backend.video_frame_timeout_s,
                    **stats.to_dict(),
                },
            )

        snapshot = self._telemetry.current
        state = self._vehicle.state
        connected = self._vehicle.connected
        freshness_limit = float(settings.backend.mavlink.heartbeat_timeout)
        telemetry_age = self._age_seconds(snapshot.timestamp)
        if key == "orin":
            orin_state = self._orin.state if self._orin is not None else None
            heartbeat_age = self._age_seconds(
                orin_state.last_heartbeat_at if orin_state is not None else None
            )
            orin_timeout = float(settings.backend.orin.heartbeat_timeout_s)
            orin_ready = bool(
                connected
                and orin_state is not None
                and orin_state.alive
                and heartbeat_age is not None
                and heartbeat_age <= orin_timeout
            )
            if orin_state is None:
                detail = "Orin servisi bu çalışma kipinde kullanılamıyor"
            elif orin_ready:
                task = orin_state.active_task or "boşta"
                latency = (
                    f" / gecikme {orin_state.round_trip_ms:.0f} ms"
                    if orin_state.round_trip_ms is not None
                    else ""
                )
                remote_log = (
                    f"log açık / kuyruk {orin_state.remote_log_queued}"
                    if orin_state.remote_log_running
                    else "log kapalı"
                )
                detail = (
                    f"Sağlık {orin_state.health.upper()} / görev {task} / "
                    f"mod {orin_state.vehicle_mode or '-'} / {remote_log} / "
                    f"heartbeat {heartbeat_age:.1f} sn önce{latency}"
                )
            else:
                detail = (
                    orin_state.last_error
                    or (
                        f"Son Orin heartbeat {heartbeat_age:.1f} sn önce"
                        if heartbeat_age is not None
                        else "Orin uygulama heartbeat paketi bekleniyor"
                    )
                )
            return ConnectionStatus(
                key,
                orin_ready,
                "Orin çalışıyor" if orin_ready else "Orin heartbeat alınmıyor",
                detail,
                state.endpoint or settings.backend.mavlink.endpoint,
                orin_timeout,
                {
                    **self._vehicle_options(settings),
                    "auto_connect": settings.backend.orin.enabled,
                    "shared_vehicle_link": True,
                    "vehicle_connected": connected,
                    "component_id": settings.backend.orin.component_id,
                    "system_id": settings.backend.orin.system_id,
                    "network_id": settings.backend.orin.network_id,
                    "message_type": settings.backend.orin.message_type,
                    "heartbeat_age_s": heartbeat_age,
                    "orin_heartbeat_timeout_s": orin_timeout,
                    "health": orin_state.health if orin_state is not None else "offline",
                    "service_available": orin_state is not None,
                    "active_task": orin_state.active_task if orin_state is not None else "",
                    "queued_tasks": list(orin_state.queued_tasks) if orin_state is not None else [],
                    "vehicle_mode": orin_state.vehicle_mode if orin_state is not None else "",
                    "onboard_version": orin_state.version if orin_state is not None else "",
                    "remote_log_running": orin_state.remote_log_running if orin_state is not None else False,
                    "remote_log_queued": orin_state.remote_log_queued if orin_state is not None else 0,
                    "remote_log_sent": orin_state.remote_log_sent if orin_state is not None else 0,
                    "remote_log_dropped": orin_state.remote_log_dropped if orin_state is not None else 0,
                    "round_trip_ms": (
                        orin_state.round_trip_ms if orin_state is not None else None
                    ),
                    "packets_in": orin_state.packets_in if orin_state is not None else 0,
                    "packets_out": orin_state.packets_out if orin_state is not None else 0,
                },
                can_disconnect=bool(
                    self._orin is not None
                    and getattr(self._orin, "running", connected)
                ),
            )
        if key == "gps":
            position_age = self._age_seconds(snapshot.position_received_at)
            gps_age = self._age_seconds(snapshot.gps_received_at)
            gps_ready = bool(
                connected
                and snapshot.has_valid_position
                and snapshot.has_fresh_gps(freshness_limit)
            )
            satellite_text = (
                str(snapshot.gps_satellites)
                if snapshot.gps_satellites is not None
                else "--"
            )
            return ConnectionStatus(
                key,
                gps_ready,
                "GPS verisi alınıyor" if gps_ready else "GPS verisi bekleniyor",
                (
                    f"{snapshot.gps_fix} / {satellite_text} uydu / "
                    f"GPS yaşı {gps_age:.1f} sn"
                    if gps_age is not None
                    else f"{snapshot.gps_fix} / {satellite_text} uydu / canlı GPS verisi yok"
                ),
                state.endpoint or settings.backend.mavlink.endpoint,
                float(settings.backend.mavlink.heartbeat_timeout),
                {
                    **self._vehicle_options(settings),
                    "vehicle_connected": connected,
                    "satellites": snapshot.gps_satellites,
                    "gps_fix": snapshot.gps_fix,
                    "gps_age_s": gps_age,
                    "position_valid": snapshot.has_valid_position,
                    "position_age_s": position_age,
                    "freshness_limit_s": freshness_limit,
                },
            )
        if key == "telemetry":
            serial_access = self._serial_access_options(
                settings.backend.mavlink.endpoint
            )
            permission_required = bool(
                serial_access.get("serial_permission_required", False)
            )
            telemetry_ready = bool(
                connected
                and snapshot.telemetry_link
                and telemetry_age is not None
                and telemetry_age <= freshness_limit
            )
            link_quality_age = self._age_seconds(snapshot.link_quality_received_at)
            link_quality_available = snapshot.has_fresh_link_quality(freshness_limit)
            if link_quality_available:
                quality_name = (
                    "RF hat sağlığı"
                    if snapshot.link_quality_source == "rf"
                    else "MAVLink paket teslimi"
                )
                quality_detail = (
                    f"{quality_name} %{snapshot.link_quality} / "
                    f"{link_quality_age:.1f} sn önce"
                )
            else:
                quality_detail = "Paket sağlık ölçümü bekleniyor"
            return ConnectionStatus(
                key,
                telemetry_ready,
                (
                    "Canlı MAVLink telemetrisi alınıyor"
                    if telemetry_ready
                    else (
                        "AREF seri cihazına erişim izni yok"
                        if permission_required
                        else "MAVLink telemetrisi bayat veya yok"
                    )
                ),
                (
                    f"Seri cihaz mevcut fakat kullanıcının okuma/yazma izni yok: "
                    f"{serial_access.get('serial_device', settings.backend.mavlink.endpoint)}"
                    if permission_required
                    else (
                        f"Son araç mesajı {telemetry_age:.1f} sn önce / {quality_detail}"
                        if telemetry_age is not None
                        else f"Henüz araç mesajı alınmadı / {quality_detail}"
                    )
                ),
                state.endpoint or settings.backend.mavlink.endpoint,
                freshness_limit,
                {
                    **self._vehicle_options(settings),
                    **serial_access,
                    "vehicle_connected": connected,
                    "last_message_age_s": telemetry_age,
                    "freshness_limit_s": freshness_limit,
                    "link_quality": snapshot.link_quality,
                    "link_quality_source": snapshot.link_quality_source,
                    "link_quality_age_s": link_quality_age,
                    "link_quality_available": link_quality_available,
                    "radio_local_rssi": snapshot.radio_local_rssi,
                    "radio_remote_rssi": snapshot.radio_remote_rssi,
                    "radio_local_noise": snapshot.radio_local_noise,
                    "radio_remote_noise": snapshot.radio_remote_noise,
                    "radio_tx_buffer": snapshot.radio_tx_buffer,
                    "radio_rx_errors": snapshot.radio_rx_errors,
                    "radio_fixed_packets": snapshot.radio_fixed_packets,
                    "packet_delivery_percent": snapshot.packet_delivery_percent,
                    "estimated_dropped_packets": snapshot.estimated_dropped_packets,
                    "packets_in": snapshot.packets_in,
                    "packets_out": snapshot.packets_out,
                },
            )
        summary = "Otopilot heartbeat bağlı" if connected else "Otopilot heartbeat bağlantısı yok"
        detail = (
            f"Cihaz {state.device_id} / SYS {state.system_id} / COMP {state.component_id}"
            if connected
            else "Telemetri, otopilot ve GPS aynı MAVLink hattını kullanır"
        )
        return ConnectionStatus(
            key,
            connected,
            summary,
            detail,
            state.endpoint or settings.backend.mavlink.endpoint,
            float(settings.backend.mavlink.heartbeat_timeout),
            self._vehicle_options(settings),
        )

    def reconnect(self, channel: str, options: dict[str, Any]) -> ConnectionStatus:
        key = self._normalize(channel)
        with self._operation_lock:
            self._event(
                "connection.reconnect_requested",
                f"{key} bağlantısı yeniden başlatılıyor",
                details={"channel": key},
            )
            try:
                if key == "server":
                    self._reconnect_server(options)
                elif key == "video":
                    self._reconnect_video(options)
                elif key == "orin":
                    self._reconnect_orin(options)
                else:
                    self._reconnect_vehicle(options)
            except Exception as exc:
                self._event(
                    "connection.reconnect_failed",
                    f"{key} bağlantısı kurulamadı: {exc}",
                    severity="danger",
                    details={"channel": key},
                )
                self._publish_related(key)
                raise
            result = self.status(key)
            self._event(
                "connection.reconnect_completed",
                (
                    f"{key} bağlantısı hazır"
                    if result.connected
                    else f"{key} yeniden başlatıldı; veri bekleniyor"
                ),
                severity="success" if result.connected else "warning",
                details={"channel": key, "endpoint": result.endpoint},
            )
            self._publish_related(key)
            return result

    def repair_serial_access(self, channel: str) -> ConnectionStatus:
        """Grant serial-device ACL access only to the current user and reconnect."""

        key = self._normalize(channel)
        if key not in VEHICLE_CHANNELS:
            raise ValueError("Seri port erişim onarımı yalnızca araç bağlantısı içindir")

        settings = self._settings.current
        mavlink = settings.backend.mavlink
        device = self._serial_device_path(mavlink.endpoint)
        username = pwd.getpwuid(os.getuid()).pw_name

        with self._operation_lock:
            self._event(
                "serial.access_repair_requested",
                f"{device} için {username} kullanıcısına seri port erişimi isteniyor",
                severity="warning",
                details={"channel": key, "device": str(device), "user": username},
            )
            if not os.access(device, os.R_OK | os.W_OK):
                try:
                    self._grant_serial_acl(device, username)
                except Exception as exc:
                    self._event(
                        "serial.access_repair_failed",
                        f"Seri port erişim izni verilemedi: {exc}",
                        severity="danger",
                        details={"channel": key, "device": str(device)},
                    )
                    self._publish_related(key)
                    raise
            if not os.access(device, os.R_OK | os.W_OK):
                error = PermissionError(
                    "Yetkilendirme tamamlandı ancak seri cihaz erişimi doğrulanamadı"
                )
                self._event(
                    "serial.access_repair_failed",
                    str(error),
                    severity="danger",
                    details={"channel": key, "device": str(device)},
                )
                self._publish_related(key)
                raise error
            self._event(
                "serial.access_repair_completed",
                f"Seri port erişimi {username} kullanıcısı için etkinleştirildi",
                severity="success",
                details={"channel": key, "device": str(device), "user": username},
            )

        return self.reconnect(
            key,
            {
                "endpoint": mavlink.endpoint,
                "timeout_s": mavlink.heartbeat_timeout,
                "baud": mavlink.baud,
                "auto_connect": mavlink.auto_connect,
            },
        )

    def disconnect(self, channel: str) -> ConnectionStatus:
        key = self._normalize(channel)
        with self._operation_lock:
            if key == "server":
                self._competition.disconnect()
                current = self._settings.current
                if current.backend.competition.auto_connect:
                    self._settings.save(
                        current.with_value("backend.competition.auto_connect", False)
                    )
            elif key == "video":
                self._video.stop()
                current = self._settings.current
                if current.backend.video_auto_connect:
                    self._settings.save(
                        current.with_value("backend.video_auto_connect", False)
                    )
            elif key == "orin":
                if self._orin is not None:
                    self._orin.stop()
                current = self._settings.current
                if current.backend.orin.enabled:
                    self._settings.save(
                        current.with_value("backend.orin.enabled", False)
                    )
            else:
                self._vehicle.stop()
                current = self._settings.current
                if current.backend.mavlink.auto_connect:
                    self._settings.save(
                        current.with_value("backend.mavlink.auto_connect", False)
                    )
            self._event(
                "connection.disconnected",
                f"{key} bağlantısı kullanıcı isteğiyle kapatıldı",
                details={"channel": key},
            )
            self._publish_related(key)
            return self.status(key)

    def _reconnect_orin(self, options: dict[str, Any]) -> None:
        if self._orin is None:
            raise RuntimeError("Orin servisi bu çalışma kipinde kullanılamıyor")
        if not self._vehicle.connected:
            raise ConnectionError(
                "Orin kanalı mevcut araç telemetrisi bağlı olmadan açılamaz"
            )
        current = self._settings.current
        orin = current.backend.orin
        timeout = float(options.get("timeout_s", orin.heartbeat_timeout_s))
        updated = current.with_value("backend.orin.enabled", True)
        updated = updated.with_value("backend.orin.heartbeat_timeout_s", timeout)
        self._settings.save(updated)
        self._orin.configure(updated.backend.orin)
        self._orin.start()
        self._orin.probe()

    def _reconnect_vehicle(self, options: dict[str, Any]) -> None:
        current = self._settings.current
        mavlink = current.backend.mavlink
        endpoint = str(options.get("endpoint", mavlink.endpoint)).strip()
        timeout = float(options.get("timeout_s", mavlink.heartbeat_timeout))
        baud = int(options.get("baud", mavlink.baud))
        auto_connect = bool(options.get("auto_connect", mavlink.auto_connect))
        config = VehicleConnectionConfig(endpoint, timeout, baud)
        updated = current.with_value("backend.mavlink.endpoint", endpoint)
        updated = updated.with_value("backend.mavlink.heartbeat_timeout", int(timeout))
        updated = updated.with_value("backend.mavlink.baud", baud)
        updated = updated.with_value("backend.mavlink.auto_connect", auto_connect)
        self._settings.save(updated)
        self._vehicle.reconnect(config)

    def _reconnect_video(self, options: dict[str, Any]) -> None:
        current = self._settings.current
        backend = current.backend
        source_type = str(options.get("source_type", backend.video_source_type)).strip().lower()
        source_uri = str(options.get("endpoint", backend.video_source_uri)).strip()
        codec = str(options.get("codec", backend.video_codec)).strip().lower()
        latency_ms = int(options.get("latency_ms", backend.video_latency_ms))
        frame_timeout_s = int(
            options.get("frame_timeout_s", backend.video_frame_timeout_s)
        )
        auto_connect = bool(
            options.get("auto_connect", backend.video_auto_connect)
        )
        updated = current.with_value("backend.video_auto_connect", auto_connect)
        updated = updated.with_value("backend.video_source_type", source_type)
        updated = updated.with_value("backend.video_source_uri", source_uri)
        updated = updated.with_value("backend.video_codec", codec)
        updated = updated.with_value("backend.video_latency_ms", latency_ms)
        updated = updated.with_value(
            "backend.video_frame_timeout_s", frame_timeout_s
        )
        self._settings.save(updated)
        self._video.configure(
            source_type=source_type,
            source_uri=source_uri,
            latency_ms=latency_ms,
            codec=codec,
            frame_timeout_s=frame_timeout_s,
        )
        self._video.start()

    def _reconnect_server(self, options: dict[str, Any]) -> None:
        current = self._settings.current
        server = current.backend.competition
        config = CompetitionServerConfig(
            base_url=str(options.get("endpoint", server.base_url)).strip(),
            username=str(options.get("username", server.username)).strip(),
            password=str(options.get("password", "")) or server.password,
            team_number=int(options.get("team_number", server.team_number)),
            timeout_s=float(options.get("timeout_s", server.timeout_s)),
            telemetry_period_ms=int(
                options.get("telemetry_period_ms", server.telemetry_period_ms)
            ),
            auto_connect=bool(options.get("auto_connect", server.auto_connect)),
        )
        self._settings.save(current.with_value("backend.competition", config.to_dict()))
        self._competition.reconnect(config)
        deadline = time.monotonic() + config.timeout_s + 0.5
        while time.monotonic() < deadline:
            state = self._competition.state
            if state.connected:
                return
            if state.last_error:
                raise ConnectionError(state.last_error)
            time.sleep(0.02)
        raise TimeoutError("TEKNOFEST sunucusu bağlantı zaman aşımına uğradı")

    @staticmethod
    def _vehicle_options(settings) -> dict[str, Any]:
        mavlink = settings.backend.mavlink
        return {"baud": mavlink.baud, "auto_connect": mavlink.auto_connect}

    @classmethod
    def _serial_access_options(cls, endpoint: str) -> dict[str, Any]:
        try:
            device = cls._serial_device_path(endpoint)
        except (OSError, ValueError):
            return {"serial_permission_required": False}
        required = not os.access(device, os.R_OK | os.W_OK)
        return {
            "serial_permission_required": required,
            "serial_device": str(device),
        }

    @staticmethod
    def _serial_device_path(endpoint: str) -> Path:
        raw_path = str(endpoint).strip()
        if not raw_path.startswith("/dev/"):
            raise ValueError("Yapılandırılan MAVLink uç noktası bir seri cihaz değil")
        device = Path(raw_path).resolve(strict=True)
        device_stat = device.stat()
        if not stat.S_ISCHR(device_stat.st_mode):
            raise ValueError("MAVLink uç noktası bir karakter cihazı değil")
        if re.fullmatch(r"tty(?:USB|ACM)\d+", device.name) is None:
            raise ValueError(
                "Otomatik erişim onarımı yalnızca USB/ACM seri cihazlarında kullanılabilir"
            )
        return device

    @staticmethod
    def _grant_serial_acl(device: Path, username: str) -> None:
        pkexec = shutil.which("pkexec")
        setfacl = shutil.which("setfacl")
        if pkexec is None or setfacl is None:
            raise RuntimeError("pkexec veya setfacl sistemde bulunamadı")
        try:
            result = subprocess.run(
                [pkexec, setfacl, "-m", f"u:{username}:rw", str(device)],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("Sistem yetkilendirme penceresi zaman aşımına uğradı") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if result.returncode in {126, 127}:
                detail = detail or "Yetkilendirme operatör tarafından iptal edildi"
            raise PermissionError(detail or "Seri port erişim izni verilemedi")

    def _on_vehicle_changed(self, _state: VehicleConnectionState) -> None:
        for channel in VEHICLE_CHANNELS:
            self._publish(channel)

    def _publish_related(self, channel: str) -> None:
        if channel in VEHICLE_CHANNELS:
            for key in VEHICLE_CHANNELS:
                self._publish(key)
            return
        self._publish(channel)

    def _watch_health(self) -> None:
        # Link state changes are published immediately by their services.
        # This periodic pass only expires stale health data, so one pass per
        # second avoids synchronized idle UI wakeups without delaying control.
        while not self._health_stop.wait(1.0):
            self._publish_all()

    def _publish_all(self) -> None:
        for channel in sorted(ALL_CHANNELS):
            self._publish(channel)

    def _publish(self, channel: str) -> None:
        self._changed.publish(self.status(channel))

    @staticmethod
    def _normalize(channel: str) -> str:
        key = channel.strip().lower()
        if key not in ALL_CHANNELS:
            raise ValueError(f"Bilinmeyen bağlantı kanalı: {channel}")
        return key

    @staticmethod
    def _age_seconds(value: datetime | None) -> float | None:
        if value is None or value.tzinfo is None:
            return None
        age = (datetime.now(UTC) - value.astimezone(UTC)).total_seconds()
        return max(0.0, age)

    def _event(
        self,
        action: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, object] | None = None,
    ) -> None:
        if self._logger:
            self._logger.event(action, message, severity=severity, details=details)
