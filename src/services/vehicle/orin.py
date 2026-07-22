from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import count
from typing import Any

from src.core.events import EventStream, Subscription
from src.core.mavlink import (
    MavlinkApplicationChannel,
    MavlinkApplicationPacket,
    MavlinkClient,
)
from src.models.orin import (
    OrinCommandReceipt,
    OrinConnectionState,
    OrinLinkConfig,
    OrinMessage,
)
from src.models.vehicle import VehicleConnectionState
from src.services.contracts.orin import OrinService
from src.services.contracts.vehicle import VehicleService
from src.services.logging_service import ServiceLogger


@dataclass(slots=True)
class _PendingCommand:
    event: threading.Event
    started_monotonic: float
    response: MavlinkApplicationPacket | None = None
    error: str = ""


class NullOrinService(OrinService):
    def __init__(self) -> None:
        self._state = OrinConnectionState()
        self._changed = EventStream[OrinConnectionState]()
        self._received = EventStream[OrinMessage]()

    @property
    def connected(self) -> bool:
        return False

    @property
    def state(self) -> OrinConnectionState:
        return self._state

    @property
    def changed(self) -> EventStream[OrinConnectionState]:
        return self._changed

    @property
    def received(self) -> EventStream[OrinMessage]:
        return self._received

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def configure(self, config: OrinLinkConfig) -> None:
        del config

    def issue(self, packet_type: str, payload=None, *, timeout=None) -> OrinCommandReceipt:
        del packet_type, payload, timeout
        raise ConnectionError("Orin MAVLink uygulama kanalı hazır değil")


class SimulatedOrinService(OrinService):
    """Gerçek Orin devreye alınana kadar açıkça işaretli geçici adaptör."""

    def __init__(
        self,
        config: OrinLinkConfig | None = None,
        logger: ServiceLogger | None = None,
    ) -> None:
        self._config = config or OrinLinkConfig(simulate_connected=True)
        self._logger = logger
        self._changed = EventStream[OrinConnectionState]()
        self._received = EventStream[OrinMessage]()
        self._request_ids = count(1)
        self._started = False
        self._lock = threading.RLock()
        self._state = OrinConnectionState(
            alive=False,
            simulated=True,
            endpoint="simulation://orin",
            system_id=1,
            component_id=self._config.component_id,
            health="simulated",
            version="temporary-yki-simulation",
            capabilities=("task.start", "kamikaze.qr"),
        )

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._state.alive

    @property
    def state(self) -> OrinConnectionState:
        with self._lock:
            return replace(self._state)

    @property
    def changed(self) -> EventStream[OrinConnectionState]:
        return self._changed

    @property
    def received(self) -> EventStream[OrinMessage]:
        return self._received

    def configure(self, config: OrinLinkConfig) -> None:
        with self._lock:
            self._config = config
            self._state = replace(
                self._state,
                component_id=config.component_id,
            )
        self._publish()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            now = datetime.now(UTC)
            self._state = replace(
                self._state,
                alive=True,
                last_heartbeat_at=now,
                last_packet_at=now,
                last_error="",
            )
        self._publish()
        self._event(
            "orin.simulation_started",
            "Orin geçici simülasyon bağlantısı etkin",
            severity="warning",
            details={"endpoint": "simulation://orin"},
        )

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._state = replace(self._state, alive=False, active_task="")
        self._publish()

    def issue(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> OrinCommandReceipt:
        del timeout
        with self._lock:
            if not self._started or not self._state.alive:
                raise ConnectionError("Geçici Orin simülasyonu çalışmıyor")
            request_id = next(self._request_ids)
            body = dict(payload or {})
            task = str(body.get("task", "")).strip().upper()
            now = datetime.now(UTC)
            self._state = replace(
                self._state,
                active_task=task or self._state.active_task,
                packets_out=self._state.packets_out + 1,
                last_packet_at=now,
            )
        self._publish()
        self._event(
            "orin.simulated_command",
            f"Orin komutu geçici simülasyonda kabul edildi: {task or packet_type}",
            severity="warning",
            details={
                "packet_type": packet_type,
                "task": task,
                "request_id": request_id,
                "simulated": True,
            },
        )
        return OrinCommandReceipt(
            request_id=request_id,
            accepted=True,
            message="Geçici Orin simülasyonu komutu kabul etti",
            response={
                "accepted": True,
                "task": task,
                "active_task": task,
                "execution": "simulated",
                "simulated": True,
            },
            round_trip_ms=0.0,
        )

    def _publish(self) -> None:
        self._changed.publish_isolated(self.state)

    def _event(
        self,
        action: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, object] | None = None,
    ) -> None:
        if self._logger:
            self._logger.event(
                action,
                message,
                severity=severity,
                details=details or {},
            )


class MavlinkOrinService(OrinService):
    """RFD/Pixhawk rotası üzerinden araç üstü Orin uygulama kanalını yönetir."""

    HEARTBEAT_TYPES = frozenset({"system.heartbeat", "system.pong"})
    RESPONSE_TYPES = frozenset({"system.ack", "system.error"})

    def __init__(
        self,
        client: MavlinkClient,
        vehicle: VehicleService,
        config: OrinLinkConfig | None = None,
        logger: ServiceLogger | None = None,
    ) -> None:
        self._client = client
        self._vehicle = vehicle
        self._config = config or OrinLinkConfig()
        self._logger = logger
        self._changed = EventStream[OrinConnectionState]()
        self._received = EventStream[OrinMessage]()
        self._state = OrinConnectionState(
            endpoint=client.endpoint.uri,
            component_id=self._config.component_id,
        )
        self._channel: MavlinkApplicationChannel | None = None
        self._subscriptions: list[Subscription] = []
        self._pending: dict[int, _PendingCommand] = {}
        self._lock = threading.RLock()
        self._started = False
        self._last_packet_monotonic = 0.0
        self._last_heartbeat_monotonic = 0.0
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._state.alive and self._vehicle.connected)

    @property
    def state(self) -> OrinConnectionState:
        with self._lock:
            return replace(self._state)

    @property
    def changed(self) -> EventStream[OrinConnectionState]:
        return self._changed

    @property
    def received(self) -> EventStream[OrinMessage]:
        return self._received

    def configure(self, config: OrinLinkConfig) -> None:
        restart = False
        with self._lock:
            if config == self._config:
                return
            restart = self._started
        if restart:
            self.stop()
        with self._lock:
            self._config = config
            self._state = replace(self._state, component_id=config.component_id)
        if restart:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._started or not self._config.enabled:
                return
            self._channel = self._create_channel()
            channel = self._channel
            self._started = True
        self._subscriptions.extend(
            [
                channel.packets.subscribe(self._on_packet),
                channel.errors.subscribe(self._on_channel_error),
                self._vehicle.connection_changed.subscribe(self._on_vehicle_changed),
            ]
        )
        try:
            channel.start()
        except Exception:
            for subscription in self._subscriptions:
                subscription.cancel()
            self._subscriptions.clear()
            with self._lock:
                self._channel = None
                self._started = False
            raise
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watch_health,
            name="OrinHeartbeatWatchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        self._publish()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            channel = self._channel
            self._channel = None
            pending = tuple(self._pending.values())
            self._pending.clear()
        self._watchdog_stop.set()
        thread = self._watchdog_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._watchdog_thread = None
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        if channel is not None:
            channel.stop()
        for item in pending:
            item.error = "Orin servisi durduruldu"
            item.event.set()
        self._mark_unavailable("Orin servisi kapalı")

    def issue(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> OrinCommandReceipt:
        if not self._vehicle.connected:
            raise ConnectionError("Telemetri hattı bağlı değil; Orin'e komut gönderilemez")
        if not self.connected:
            raise ConnectionError("Orin heartbeat alınmıyor; komut gönderilmedi")
        wait_timeout = float(timeout or self._config.command_timeout_s)
        if wait_timeout <= 0:
            raise ValueError("Orin komut zaman aşımı pozitif olmalı")
        packet_id = secrets.randbits(32) or 1
        pending = _PendingCommand(threading.Event(), time.monotonic())
        with self._lock:
            channel = self._channel
            if not self._started or channel is None:
                raise ConnectionError("Orin uygulama kanalı çalışmıyor")
            self._pending[packet_id] = pending
        try:
            packet = channel.send(
                packet_type,
                payload,
                packet_id=packet_id,
                target_system=self._target_system(),
                target_component=self._config.component_id,
            )
            with self._lock:
                self._state = replace(self._state, packets_out=self._state.packets_out + 1)
            self._event(
                "orin.command_sent",
                f"Orin komutu gönderildi: {packet.packet_type}",
                details={"packet_id": packet.packet_id, "packet_type": packet.packet_type},
                correlation_id=str(packet.packet_id),
            )
            if not pending.event.wait(wait_timeout):
                raise TimeoutError(
                    f"Orin komut onayı {wait_timeout:g} saniyede alınamadı: {packet.packet_type}"
                )
            if pending.error:
                raise ConnectionError(pending.error)
            response = pending.response
            if response is None:
                raise TimeoutError("Orin komut cevabı boş döndü")
            accepted = bool(response.payload.get("accepted", response.packet_type != "system.error"))
            message = str(response.payload.get("message", "Orin komutu kabul edildi"))
            round_trip_ms = max(0.0, (time.monotonic() - pending.started_monotonic) * 1000.0)
            with self._lock:
                self._state = replace(self._state, round_trip_ms=round_trip_ms)
            receipt = OrinCommandReceipt(
                packet.packet_id,
                accepted,
                message,
                dict(response.payload),
                round_trip_ms,
            )
            execution = str(response.payload.get("execution", "acknowledged"))
            task = str(
                response.payload.get("task")
                or response.payload.get("active_task")
                or (payload or {}).get("task", "")
            ).strip().upper()
            event_action = (
                "orin.script_completed"
                if accepted and execution == "completed"
                else "orin.script_failed"
                if execution in {"failed", "timeout", "start_failed", "not_configured"}
                else "orin.command_acknowledged"
                if accepted
                else "orin.command_rejected"
            )
            event_message = message
            if event_action == "orin.script_completed":
                event_message = f"Orin scripti başarıyla tamamlandı: {task or packet.packet_type}"
            elif event_action == "orin.script_failed":
                event_message = f"Orin scripti başarısız: {task or packet.packet_type} · {message}"
            self._event(
                event_action,
                event_message,
                severity="success" if accepted else "danger",
                details={
                    "packet_id": packet.packet_id,
                    "packet_type": packet.packet_type,
                    "round_trip_ms": round_trip_ms,
                    "task": task,
                    "execution": execution,
                    "exit_code": response.payload.get("exit_code"),
                    "stdout": response.payload.get("stdout", ""),
                    "stderr": response.payload.get("stderr", ""),
                },
                correlation_id=str(packet.packet_id),
            )
            return receipt
        finally:
            with self._lock:
                self._pending.pop(packet_id, None)

    def _create_channel(self) -> MavlinkApplicationChannel:
        return MavlinkApplicationChannel(
            self._client,
            network_id=self._config.network_id,
            message_type=self._config.message_type,
            target_component=self._config.component_id,
            local_system=self._client.endpoint.source_system,
            local_component=self._client.endpoint.source_component,
            source_components=self._config.component_id,
            fragment_timeout=max(5.0, self._config.heartbeat_timeout_s),
        )

    def _on_packet(self, packet: MavlinkApplicationPacket) -> None:
        if packet.source_system not in {None, 0, self._target_system()}:
            return
        now = datetime.now(UTC)
        payload = packet.payload
        with self._lock:
            was_alive = self._state.alive
            self._last_packet_monotonic = time.monotonic()
            is_heartbeat = packet.packet_type in self.HEARTBEAT_TYPES
            if is_heartbeat:
                self._last_heartbeat_monotonic = self._last_packet_monotonic
            heartbeat_at = (
                now if is_heartbeat else self._state.last_heartbeat_at
            )
            capabilities_value = payload.get("capabilities", self._state.capabilities)
            capabilities = (
                tuple(str(item) for item in capabilities_value)
                if isinstance(capabilities_value, (list, tuple))
                else self._state.capabilities
            )
            self._state = replace(
                self._state,
                alive=True if is_heartbeat else self._state.alive,
                endpoint=self._client.endpoint.uri,
                system_id=int(packet.source_system or self._target_system()),
                component_id=int(packet.source_component or self._config.component_id),
                health=str(payload.get("health", self._state.health or "ok")),
                active_task=str(payload.get("active_task", self._state.active_task)),
                version=str(payload.get("version", self._state.version)),
                uptime_s=float(payload.get("uptime_s", self._state.uptime_s)),
                capabilities=capabilities,
                last_heartbeat_at=heartbeat_at,
                last_packet_at=now,
                packets_in=self._state.packets_in + 1,
                last_error="",
            )
            request_id = self._response_request_id(packet)
            pending = self._pending.get(request_id) if request_id is not None else None
            if pending is not None and packet.packet_type in self.RESPONSE_TYPES:
                pending.response = packet
                pending.event.set()
            state = replace(self._state)
        self._changed.publish_isolated(state)
        self._received.publish_isolated(
            OrinMessage(
                packet.packet_type,
                packet.payload,
                packet.packet_id,
                int(packet.source_system or 0),
                int(packet.source_component or 0),
                now,
            )
        )
        if not was_alive and state.alive:
            self._event(
                "orin.connected",
                f"Orin heartbeat alındı ({state.device_id})",
                severity="success",
                details=state.to_dict(),
            )

    @staticmethod
    def _response_request_id(packet: MavlinkApplicationPacket) -> int | None:
        value = packet.payload.get("request_id")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _on_channel_error(self, error: Exception) -> None:
        with self._lock:
            self._state = replace(self._state, last_error=str(error))
        self._publish()
        self._event(
            "orin.packet_invalid",
            f"Orin uygulama paketi reddedildi: {error}",
            severity="warning",
        )

    def _on_vehicle_changed(self, state: VehicleConnectionState) -> None:
        if not state.connected:
            self._mark_unavailable("Telemetri hattı kapalı")
            return
        with self._lock:
            self._state = replace(
                self._state,
                endpoint=state.endpoint,
                system_id=state.system_id,
            )
        self._publish()

    def _watch_health(self) -> None:
        while not self._watchdog_stop.wait(0.5):
            with self._lock:
                alive = self._state.alive
                age = time.monotonic() - self._last_heartbeat_monotonic
                timeout = self._config.heartbeat_timeout_s
            if alive and (not self._vehicle.connected or age > timeout):
                reason = (
                    "Telemetri hattı kapalı"
                    if not self._vehicle.connected
                    else f"Orin heartbeat {age:.1f} saniyedir alınmıyor"
                )
                self._mark_unavailable(reason)

    def _mark_unavailable(self, reason: str) -> None:
        with self._lock:
            was_alive = self._state.alive
            if not was_alive and self._state.last_error == reason:
                return
            self._state = replace(
                self._state,
                alive=False,
                health="offline",
                last_error=reason,
            )
            state = replace(self._state)
        self._changed.publish_isolated(state)
        if was_alive:
            self._event(
                "orin.disconnected",
                reason,
                severity="warning",
                details={"device_id": state.device_id},
            )

    def _target_system(self) -> int:
        state_system = int(self._vehicle.state.system_id)
        if state_system:
            return state_system
        try:
            return int(self._client.connection.target_system)
        except ConnectionError:
            return 1

    def _publish(self) -> None:
        self._changed.publish_isolated(self.state)

    def _event(
        self,
        action: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        if self._logger:
            self._logger.event(
                action,
                message,
                severity=severity,
                details=details,
                device_id=self.state.device_id,
                correlation_id=correlation_id,
            )
