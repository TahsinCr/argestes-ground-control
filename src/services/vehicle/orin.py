from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.events import EventStream, Subscription
from src.core.mavlink import (
    MavlinkApplicationChannel,
    MavlinkApplicationPacket,
    MavlinkApplicationPeer,
    MavlinkApplicationPeerState,
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


def configured_orin_commands(config_path: Path) -> tuple[str, ...]:
    """Validate and return Orin task names configured for the GCS.

    The GCS reads task names only. Command paths and security validation belong
    to the onboard Orin application.
    """

    path = Path(config_path)
    if not path.exists():
        return ()
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Orin config JSON geçersiz: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Orin config kökü bir nesne olmalı")
    commands = payload.get("commands", {})
    if not isinstance(commands, dict):
        raise ValueError("Orin config commands bölümü bir nesne olmalı")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name, command in commands.items():
        name = str(raw_name).strip().upper()
        if not name:
            raise ValueError("Orin komut adı boş olamaz")
        if not isinstance(command, str) and not (
            isinstance(command, list)
            and command
            and all(isinstance(item, str) for item in command)
        ):
            raise ValueError(f"Orin komutu string veya string listesi olmalı: {name}")
        if name not in seen:
            normalized.append(name)
            seen.add(name)
    return tuple(normalized)


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

    @property
    def running(self) -> bool:
        return False

    def probe(self) -> int:
        raise ConnectionError("Orin MAVLink uygulama kanalı hazır değil")

    def issue(self, packet_type: str, payload=None, *, timeout=None) -> OrinCommandReceipt:
        del packet_type, payload, timeout
        raise ConnectionError("Orin MAVLink uygulama kanalı hazır değil")


class MavlinkOrinService(OrinService):
    """Manage the onboard Orin application channel over the RFD/Pixhawk route."""

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
        self._peer: MavlinkApplicationPeer | None = None
        self._subscriptions: list[Subscription] = []
        self._lock = threading.RLock()
        self._started = False

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

    @property
    def running(self) -> bool:
        with self._lock:
            return self._started

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
            self._peer = self._create_peer(channel)
            peer = self._peer
            self._started = True
        self._subscriptions.extend(
            [
                peer.packets.subscribe(self._on_packet),
                peer.changed.subscribe(self._on_peer_changed),
                peer.errors.subscribe(self._on_channel_error),
                self._vehicle.connection_changed.subscribe(self._on_vehicle_changed),
            ]
        )
        try:
            peer.start()
        except Exception:
            for subscription in self._subscriptions:
                subscription.cancel()
            self._subscriptions.clear()
            with self._lock:
                self._channel = None
                self._peer = None
                self._started = False
            raise
        self._publish()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            channel = self._channel
            self._channel = None
            peer = self._peer
            self._peer = None
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        if peer is not None:
            peer.stop()
        elif channel is not None:
            channel.stop()
        self._mark_unavailable("Orin servisi kapalı")

    def probe(self) -> int:
        """Trigger an immediate Orin liveness check over the shared vehicle link."""

        if not self._vehicle.connected:
            raise ConnectionError("Telemetri hattı bağlı değil; Orin yoklanamaz")
        with self._lock:
            peer = self._peer
            started = self._started
        if not started or peer is None:
            raise ConnectionError("Orin uygulama kanalı çalışmıyor")
        packet = peer.probe()
        self._event(
            "orin.probe_sent",
            "Orin bağlantı yoklaması gönderildi",
            details={"packet_id": packet.packet_id},
            correlation_id=str(packet.packet_id),
        )
        return packet.packet_id

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
        with self._lock:
            peer = self._peer
            if not self._started or peer is None:
                raise ConnectionError("Orin uygulama kanalı çalışmıyor")
        exchange = peer.request(
            packet_type,
            payload,
            response_types=self.RESPONSE_TYPES,
            timeout=wait_timeout,
            on_sent=self._log_command_sent,
        )
        packet = exchange.request
        response = exchange.response
        accepted = bool(response.payload.get("accepted", response.packet_type != "system.error"))
        message = str(response.payload.get("message", "Orin komutu kabul edildi"))
        round_trip_ms = exchange.round_trip_ms
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

    def _log_command_sent(self, packet: MavlinkApplicationPacket) -> None:
        self._event(
            "orin.command_sent",
            f"Orin komutu gönderildi: {packet.packet_type}",
            details={"packet_id": packet.packet_id, "packet_type": packet.packet_type},
            correlation_id=str(packet.packet_id),
        )

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

    def _create_peer(
        self,
        channel: MavlinkApplicationChannel,
    ) -> MavlinkApplicationPeer:
        timeout = float(self._config.heartbeat_timeout_s)
        interval = min(3.0, max(0.5, timeout / 3.0))
        if interval >= timeout:
            interval = max(0.1, timeout / 2.0)
        return MavlinkApplicationPeer(
            channel,
            role="yki",
            target_system=self._target_system,
            target_component=lambda: self._config.component_id,
            transport_available=lambda: self._vehicle.connected,
            heartbeat_payload=self._heartbeat_payload,
            heartbeat_interval=interval,
            heartbeat_timeout=timeout,
        )

    def _heartbeat_payload(self) -> dict[str, Any]:
        return {
            "role": "yki",
            "health": "ok",
            "vehicle_system_id": self._target_system(),
        }

    def _on_packet(self, packet: MavlinkApplicationPacket) -> None:
        if packet.source_system not in {None, 0, self._target_system()}:
            return
        now = datetime.now(UTC)
        payload = packet.payload
        with self._lock:
            is_liveness = packet.packet_type in MavlinkApplicationPeer.LIVENESS_TYPES
            capabilities_value = payload.get("capabilities", self._state.capabilities)
            capabilities = (
                tuple(str(item) for item in capabilities_value)
                if isinstance(capabilities_value, (list, tuple))
                else self._state.capabilities
            )
            self._state = replace(
                self._state,
                alive=True if is_liveness else self._state.alive,
                endpoint=self._client.endpoint.uri,
                system_id=int(packet.source_system or self._target_system()),
                component_id=int(packet.source_component or self._config.component_id),
                health=str(payload.get("health", self._state.health or "ok")),
                active_task=str(payload.get("active_task", self._state.active_task)),
                version=str(payload.get("version", self._state.version)),
                uptime_s=float(payload.get("uptime_s", self._state.uptime_s)),
                capabilities=capabilities,
                last_heartbeat_at=now if is_liveness else self._state.last_heartbeat_at,
                last_packet_at=now,
                last_error="",
            )
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

    def _on_peer_changed(self, peer_state: MavlinkApplicationPeerState) -> None:
        heartbeat_at = (
            datetime.fromtimestamp(peer_state.last_liveness_at, UTC)
            if peer_state.last_liveness_at is not None
            else None
        )
        packet_at = (
            datetime.fromtimestamp(peer_state.last_seen_at, UTC)
            if peer_state.last_seen_at is not None
            else None
        )
        with self._lock:
            was_alive = self._state.alive
            self._state = replace(
                self._state,
                alive=bool(peer_state.alive and self._vehicle.connected),
                endpoint=self._client.endpoint.uri,
                system_id=self._target_system(),
                health=(
                    self._state.health
                    if peer_state.alive and self._state.health not in {"unknown", "offline"}
                    else "ok"
                    if peer_state.alive
                    else "offline"
                ),
                last_heartbeat_at=heartbeat_at,
                last_packet_at=packet_at,
                round_trip_ms=peer_state.round_trip_ms,
                packets_in=peer_state.packets_in,
                packets_out=peer_state.packets_out,
                last_error="" if peer_state.alive else peer_state.last_error,
            )
            state = replace(self._state)
        self._changed.publish_isolated(state)
        if not was_alive and state.alive:
            self._event(
                "orin.connected",
                f"Orin heartbeat alındı ({state.device_id})",
                severity="success",
                details=state.to_dict(),
            )
        elif was_alive and not state.alive:
            self._event(
                "orin.disconnected",
                state.last_error or "Orin heartbeat alınamıyor",
                severity="warning",
                details={"device_id": state.device_id},
            )

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
        with self._lock:
            self._state = replace(
                self._state,
                endpoint=state.endpoint,
                system_id=state.system_id,
            )
            peer = self._peer
        if peer is not None:
            peer.refresh_transport()
        if not state.connected:
            self._mark_unavailable("Telemetri hattı kapalı")
            return
        self._publish()

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
