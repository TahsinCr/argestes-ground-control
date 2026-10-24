"""Common MAVLink connection control and recovery.

This service owns transport recovery above the MAVLink client.  The client and
router remain the single reader/writer pair; recovery deliberately uses the
existing :class:`VehicleService` boundary instead of opening another transport.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict
from typing import Any

from src.core.events import EventBus, Subscription
from src.models.connections import ConnectionStatus
from src.models.settings import MavlinkConnectionConfig, SettingsModel
from src.models.vehicle import VehicleConnectionConfig, VehicleConnectionState
from src.services.abstracts.connections import ConnectionControlService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.logging_service import ServiceLogger


_VEHICLE_CHANNELS = frozenset({"telemetry", "autopilot", "gps"})
_KNOWN_CHANNELS = frozenset(_VEHICLE_CHANNELS)


class VehicleConnectionControlService(ConnectionControlService):
    """Reconnect the single vehicle transport without coupling it to the UI.

    A heartbeat loss first changes vehicle state to disconnected.  If the
    configured MAVLink profile permits recovery, this service then restarts the
    existing vehicle client with exponential backoff.  A user-requested
    disconnect suppresses that loop until an explicit reconnect is requested.
    """

    _INITIAL_RETRY_DELAY_S = 1.0
    _MAX_RETRY_DELAY_S = 10.0

    def __init__(
        self,
        *,
        settings: SettingsRepository,
        vehicle: VehicleService,
        telemetry: TelemetryService,
        logger: ServiceLogger | None = None,
    ) -> None:
        self._settings = settings
        self._vehicle = vehicle
        self._telemetry = telemetry
        self._logger = logger
        self._changed = EventBus[ConnectionStatus]()
        self._subscriptions: list[Subscription] = []
        self._operation_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._recovery_thread: threading.Thread | None = None
        self._next_retry_at: float | None = None
        self._retry_attempt = 0
        self._manual_disconnect = False
        self._initial_connect_pending = False
        self._recovery_inflight = False
        self._generation = 0
        self._protocol_options = self._protocol_signature(self._settings.current)

    @property
    def changed(self) -> EventBus[ConnectionStatus]:
        return self._changed

    def start(self) -> None:
        if self._subscriptions:
            return
        self._stop_event.clear()
        with self._state_lock:
            self._manual_disconnect = False
            self._generation += 1
        self._subscriptions.extend(
            [
                self._vehicle.connection_changed.subscribe(self._on_vehicle_changed),
                self._settings.changed.subscribe(self._on_settings_changed),
            ]
        )
        self._recovery_thread = threading.Thread(
            target=self._recovery_loop,
            name="VehicleConnectionRecovery",
            daemon=True,
        )
        self._recovery_thread.start()
        self._publish_vehicle_channels()
        if self._settings.current.backend.mavlink.auto_connect:
            # Initial connection is intentionally owned here as well.  Runtime
            # must not race this worker by calling VehicleService.start().
            self._schedule_recovery(self._INITIAL_RETRY_DELAY_S, initial=True)

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._state_lock:
            self._generation += 1
            self._manual_disconnect = True
            self._next_retry_at = None
            self._initial_connect_pending = False
        # VehicleService.start can be waiting for a heartbeat while this
        # worker is inside reconnect().  Its stop implementation cancels that
        # pending transport open, rather than leaving a late connection alive.
        self._vehicle.stop()
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        thread = self._recovery_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._recovery_thread = None
        with self._state_lock:
            self._next_retry_at = None
            self._retry_attempt = 0
            self._initial_connect_pending = False
            self._recovery_inflight = False

    def status(self, channel: str) -> ConnectionStatus:
        key = self._normalize(channel)
        if key not in _VEHICLE_CHANNELS:
            return ConnectionStatus(
                key,
                False,
                "Bağlantı bu çalışma kipinde etkin değil",
                "Bu ortak backend yalnız araç MAVLink bağlantısını yönetir.",
                options={"available": False},
                can_disconnect=False,
            )

        settings = self._settings.current.backend.mavlink
        vehicle_state = self._vehicle.state
        snapshot = self._telemetry.current
        connected = self._vehicle.connected
        if key == "telemetry":
            ready = bool(connected and snapshot.telemetry_link)
            summary = (
                "Canlı MAVLink telemetrisi alınıyor"
                if ready
                else "MAVLink telemetrisi bayat veya yok"
            )
            detail = self._telemetry_detail(snapshot, settings.heartbeat_timeout)
        elif key == "autopilot":
            ready = connected
            summary = (
                "Otopilot heartbeat bağlı"
                if ready
                else "Otopilot heartbeat bağlantısı yok"
            )
            detail = (
                f"Cihaz {vehicle_state.device_id} / SYS {vehicle_state.system_id} / "
                f"COMP {vehicle_state.component_id}"
                if ready
                else "Araç heartbeat paketi bekleniyor"
            )
        else:
            ready = bool(
                connected
                and snapshot.has_valid_position
                and snapshot.has_fresh_gps(float(settings.heartbeat_timeout))
            )
            summary = "GPS verisi alınıyor" if ready else "GPS verisi bekleniyor"
            detail = snapshot.gps_fix or "NO FIX"

        profile = self._active_profile()
        return ConnectionStatus(
            key,
            ready,
            summary,
            detail,
            vehicle_state.endpoint or settings.endpoint,
            float(settings.heartbeat_timeout),
            {
                "baud": settings.baud,
                "auto_connect": settings.auto_connect,
                "autoreconnect": settings.autoreconnect,
                "profile_id": profile.identifier,
                "profile_name": profile.name,
                "vehicle_connected": connected,
                "retry_attempt": self._retry_attempt,
                "available": True,
            },
            can_disconnect=connected,
        )

    def reconnect(self, channel: str, options: dict[str, Any]) -> ConnectionStatus:
        key = self._require_vehicle_channel(channel)
        with self._operation_lock:
            with self._state_lock:
                self._manual_disconnect = False
                self._generation += 1
                self._recovery_inflight = True
            config = self._apply_profile_options(options)
            self._event(
                "connection.reconnect_requested", "Araç bağlantısı yeniden başlatılıyor"
            )
            try:
                self._vehicle.reconnect(config)
            except Exception as exc:
                with self._state_lock:
                    self._recovery_inflight = False
                self._event(
                    "connection.reconnect_failed",
                    f"Araç bağlantısı kurulamadı: {exc}",
                    severity="danger",
                )
                self._schedule_recovery(self._retry_delay())
                self._publish_vehicle_channels()
                raise
            with self._state_lock:
                self._recovery_inflight = False
            self._reset_recovery()
            self._publish_vehicle_channels()
            result = self.status(key)
            self._event(
                "connection.reconnect_completed",
                "Araç bağlantısı hazır"
                if result.connected
                else "Araç bağlantısı başlatıldı; telemetri bekleniyor",
                severity="success" if result.connected else "warning",
            )
            return result

    def disconnect(self, channel: str) -> ConnectionStatus:
        key = self._require_vehicle_channel(channel)
        # Do not wait behind a reconnect that is currently waiting for a
        # heartbeat.  Marking manual intent and stopping the transport first
        # makes the in-flight connect cancellable immediately.
        with self._state_lock:
            self._manual_disconnect = True
            self._generation += 1
            self._reset_recovery()
        self._vehicle.stop()
        with self._operation_lock:
            self._event(
                "connection.disconnected",
                "Araç bağlantısı kullanıcı isteğiyle kapatıldı",
            )
            self._publish_vehicle_channels()
            return self.status(key)

    def repair_serial_access(self, channel: str) -> ConnectionStatus:
        self._require_vehicle_channel(channel)
        raise RuntimeError("Seri port erişim onarımı bu ortak backend'de uygulanmıyor")

    def _on_vehicle_changed(self, state: VehicleConnectionState) -> None:
        if state.connected:
            self._reset_recovery()
        elif not self._is_recovery_inflight():
            self._schedule_recovery(self._INITIAL_RETRY_DELAY_S)
        self._publish_vehicle_channels()

    def _on_settings_changed(self, _settings: SettingsModel) -> None:
        signature = self._protocol_signature(_settings)
        changed = signature != self._protocol_options
        self._protocol_options = signature
        if changed and self._vehicle.connected:

            def reconfigure():
                with self._operation_lock:
                    if not self._stop_event.is_set() and self._vehicle.connected:
                        try:
                            self._vehicle.reconnect(self._active_configuration())
                        except Exception as exc:
                            self._event(
                                "connection.protocol_reconfigure_failed",
                                str(exc),
                                severity="danger",
                            )

            threading.Thread(
                target=reconfigure, name="VehicleProtocolUpdate", daemon=True
            ).start()
            return
        if self._vehicle.connected:
            return
        # A user enabling auto-connect while the app is already open should
        # receive one initial attempt; no automatic option means no attempt.
        self._schedule_recovery(
            self._INITIAL_RETRY_DELAY_S,
            initial=bool(_settings.backend.mavlink.auto_connect),
        )
        self._publish_vehicle_channels()

    @staticmethod
    def _protocol_signature(settings):
        config = settings.backend.mavlink
        return (
            config.dialect,
            config.source_system,
            config.source_component,
            config.autoreconnect,
        )

    def _active_configuration(self):
        config = self._settings.current.backend.mavlink
        return VehicleConnectionConfig(
            config.endpoint,
            config.heartbeat_timeout,
            config.baud,
            {
                "dialect": config.dialect,
                "source_system": config.source_system,
                "source_component": config.source_component,
                "autoreconnect": config.autoreconnect,
            },
        )

    def _recovery_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._state_lock:
                retry_at = self._next_retry_at
            if retry_at is None:
                self._wake_event.wait()
                self._wake_event.clear()
                continue
            remaining = retry_at - time.monotonic()
            if remaining > 0:
                self._wake_event.wait(remaining)
                self._wake_event.clear()
                continue
            with self._state_lock:
                if self._next_retry_at != retry_at:
                    continue
                self._next_retry_at = None
            self._attempt_recovery()

    def _attempt_recovery(self) -> None:
        with self._state_lock:
            initial = self._initial_connect_pending
            generation = self._generation
        if (
            self._stop_event.is_set()
            or self._vehicle.connected
            or not self._connection_attempt_allowed(initial)
        ):
            return
        with self._operation_lock:
            if (
                self._stop_event.is_set()
                or self._vehicle.connected
                or not self._connection_attempt_allowed(initial)
            ):
                return
            attempt = self._retry_attempt + 1
            with self._state_lock:
                self._recovery_inflight = True
            self._event(
                "connection.reconnect_attempt",
                f"Araç bağlantısı otomatik olarak yeniden deneniyor ({attempt}. deneme)",
                severity="warning",
                details={"attempt": attempt},
            )
            try:
                self._vehicle.reconnect(self._active_configuration())
            except Exception as exc:
                with self._state_lock:
                    self._retry_attempt = attempt
                    self._initial_connect_pending = False
                    self._recovery_inflight = False
                    obsolete = (
                        generation != self._generation or self._stop_event.is_set()
                    )
                if obsolete:
                    return
                delay = self._retry_delay()
                self._event(
                    "connection.reconnect_retry_scheduled",
                    f"Araç bağlantısı yeniden kurulamadı; {delay:.0f} sn sonra tekrar denenecek: {exc}",
                    severity="warning",
                    details={"attempt": attempt, "delay_s": delay},
                )
                self._schedule_recovery(delay)
            else:
                with self._state_lock:
                    self._recovery_inflight = False
                    obsolete = (
                        generation != self._generation or self._stop_event.is_set()
                    )
                if obsolete:
                    self._vehicle.stop()
                    return
                self._reset_recovery()
                self._event(
                    "connection.reconnect_recovered",
                    "Araç bağlantısı otomatik olarak geri kazanıldı",
                    severity="success",
                )
            self._publish_vehicle_channels()

    def _schedule_recovery(self, delay_s: float, *, initial: bool = False) -> None:
        if not self._connection_attempt_allowed(initial):
            return
        retry_at = time.monotonic() + max(0.0, float(delay_s))
        with self._state_lock:
            self._initial_connect_pending = self._initial_connect_pending or initial
            if self._next_retry_at is None or retry_at < self._next_retry_at:
                self._next_retry_at = retry_at
        self._wake_event.set()

    def _is_recovery_inflight(self) -> bool:
        with self._state_lock:
            return self._recovery_inflight

    def _reset_recovery(self) -> None:
        with self._state_lock:
            self._next_retry_at = None
            self._retry_attempt = 0
            self._initial_connect_pending = False
        self._wake_event.set()

    def _connection_attempt_allowed(self, initial: bool) -> bool:
        if self._manual_disconnect:
            return False
        backend = self._settings.current.backend
        if bool(getattr(backend, "mock_mode", False)):
            return False
        settings = backend.mavlink
        return bool(settings.autoreconnect or (initial and settings.auto_connect))

    def _retry_delay(self) -> float:
        return min(self._MAX_RETRY_DELAY_S, float(2 ** max(0, self._retry_attempt - 1)))

    def _apply_profile_options(
        self, options: dict[str, Any]
    ) -> VehicleConnectionConfig:
        selected: dict[str, Any] = {}

        def mutate(current: SettingsModel) -> SettingsModel:
            mavlink = current.backend.mavlink
            profile_id = str(
                options.get(
                    "profile_id", current.backend.active_mavlink_connection_id
                )
            ).strip()
            existing = next(
                (
                    item
                    for item in current.backend.mavlink_connections
                    if item.identifier == profile_id
                ),
                None,
            )
            profile = MavlinkConnectionConfig(
                identifier=profile_id
                or (
                    existing.identifier
                    if existing is not None
                    else MavlinkConnectionConfig().identifier
                ),
                name=str(
                    options.get(
                        "profile_name",
                        existing.name if existing is not None else "Telemetri",
                    )
                ).strip()
                or "Telemetri",
                endpoint=str(options.get("endpoint", mavlink.endpoint)).strip(),
                baud=int(options.get("baud", mavlink.baud)),
                heartbeat_timeout=int(
                    float(options.get("timeout_s", mavlink.heartbeat_timeout))
                ),
                auto_connect=bool(options.get("auto_connect", mavlink.auto_connect)),
            )
            profiles = [
                profile if item.identifier == profile.identifier else item
                for item in current.backend.mavlink_connections
            ]
            if existing is None:
                profiles.append(profile)
            payload = current.to_dict()
            payload["backend"]["mavlink"] = asdict(profile.to_mavlink(mavlink))
            payload["backend"]["mavlink_connections"] = [
                item.to_dict() for item in profiles
            ]
            payload["backend"]["active_mavlink_connection_id"] = profile.identifier
            selected["profile"] = profile
            selected["mavlink"] = mavlink
            return SettingsModel.from_dict(payload)

        self._settings.update(mutate)
        profile = selected["profile"]
        mavlink = selected["mavlink"]
        return VehicleConnectionConfig(
            profile.endpoint,
            profile.heartbeat_timeout,
            profile.baud,
            {
                "dialect": mavlink.dialect,
                "source_system": mavlink.source_system,
                "source_component": mavlink.source_component,
                "autoreconnect": mavlink.autoreconnect,
            },
        )

    def _active_profile(self) -> MavlinkConnectionConfig:
        backend = self._settings.current.backend
        return next(
            (
                item
                for item in backend.mavlink_connections
                if item.identifier == backend.active_mavlink_connection_id
            ),
            MavlinkConnectionConfig.from_mavlink(backend.mavlink),
        )

    def _publish_vehicle_channels(self) -> None:
        for channel in sorted(_VEHICLE_CHANNELS):
            self._changed.publish(self.status(channel))

    @staticmethod
    def _telemetry_detail(snapshot, timeout_s: float) -> str:
        if snapshot.timestamp is None:
            return "Henüz araç mesajı alınmadı"
        age = max(0.0, time.time() - snapshot.timestamp.timestamp())
        if age > timeout_s:
            return f"Son araç mesajı {age:.1f} sn önce"
        return f"Son araç mesajı {age:.1f} sn önce / paketler ↓{snapshot.packets_in} ↑{snapshot.packets_out}"

    @staticmethod
    def _normalize(channel: str) -> str:
        key = str(channel).strip().lower()
        if key not in _KNOWN_CHANNELS:
            raise ValueError(f"Bilinmeyen bağlantı kanalı: {channel}")
        return key

    def _require_vehicle_channel(self, channel: str) -> str:
        key = self._normalize(channel)
        if key not in _VEHICLE_CHANNELS:
            raise ValueError(
                "Bu işlem yalnız araç MAVLink bağlantısı için kullanılabilir"
            )
        return key

    def _event(
        self,
        action: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, object] | None = None,
    ) -> None:
        if self._logger is not None:
            self._logger.event(action, message, severity=severity, details=details)
