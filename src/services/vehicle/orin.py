from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.communication import (
    CallableTransport,
    ContractGateway,
    ExchangeKind,
    GroundLinkService,
    LinkSide,
    PacketType,
    RawResponse,
    RemoteLogBatch,
)

from src.core.events import ErrorPolicy, EventBus, Subscription
from src.core.mavlink import (
    MavlinkApplicationChannel,
    MavlinkApplicationDispatcher,
    MavlinkApplicationHandlerRegistry,
    MavlinkApplicationPacket,
    MavlinkApplicationPeer,
    MavlinkApplicationPeerState,
    MavlinkApplicationResult,
    MavlinkClient,
)
from src.models.orin import (
    OrinCommandReceipt,
    OrinConnectionState,
    OrinHandlerResponse,
    OrinLinkConfig,
    OrinLogRecord,
    OrinMessage,
)
from src.models.vehicle import VehicleConnectionState
from src.services.abstracts.orin import OrinMessageHandler, OrinService
from src.services.abstracts.vehicle import VehicleService
from src.services.logging_service import ServiceLogger


class LogValidationError(ValueError):
    """Remote log payload failed validation before any record was accepted."""


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
        self._changed = EventBus[OrinConnectionState]()
        self._received = EventBus[OrinMessage]()
        self._remote_logs = EventBus[OrinLogRecord]()
        self._remote_log_batches = EventBus[tuple[OrinLogRecord, ...]]()

    @property
    def connected(self) -> bool:
        return False

    @property
    def state(self) -> OrinConnectionState:
        return self._state

    @property
    def changed(self) -> EventBus[OrinConnectionState]:
        return self._changed

    @property
    def received(self) -> EventBus[OrinMessage]:
        return self._received

    @property
    def remote_logs(self) -> EventBus[OrinLogRecord]:
        return self._remote_logs

    @property
    def remote_log_batches(self) -> EventBus[tuple[OrinLogRecord, ...]]:
        return self._remote_log_batches

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

    def send(self, packet_type: str, payload=None) -> OrinMessage:
        del packet_type, payload
        raise ConnectionError("Orin MAVLink uygulama kanalı hazır değil")

    def issue(self, packet_type: str, payload=None, *, timeout=None) -> OrinCommandReceipt:
        del packet_type, payload, timeout
        raise ConnectionError("Orin MAVLink uygulama kanalı hazır değil")

    def register_handler(
        self,
        packet_type: str,
        handler: OrinMessageHandler,
    ) -> Subscription:
        del packet_type, handler
        return Subscription(0, lambda: None)


class MavlinkOrinService(OrinService):
    """Manage the onboard Orin application channel over the RFD/Pixhawk route."""

    RESPONSE_TYPES = frozenset(
        {PacketType.SYSTEM_ACK.value, PacketType.SYSTEM_ERROR.value}
    )

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
        self._changed = EventBus[OrinConnectionState]()
        self._received = EventBus[OrinMessage]()
        self._remote_logs = EventBus[OrinLogRecord]()
        self._remote_log_batches = EventBus[tuple[OrinLogRecord, ...]](
            error_policy=ErrorPolicy.RAISE,
        )
        self._state = OrinConnectionState(
            endpoint=client.endpoint.uri,
            system_id=self._config.system_id,
            component_id=self._config.component_id,
        )
        self._channel: MavlinkApplicationChannel | None = None
        self._peer: MavlinkApplicationPeer | None = None
        self._handlers = MavlinkApplicationHandlerRegistry()
        self._contract_gateway = ContractGateway(side=LinkSide.GROUND)
        self._remote_log_sessions: OrderedDict[str, int] = OrderedDict()
        self._remote_log_handler = self._handlers.register(
            PacketType.LOGS_PUSH.value,
            self._contract_gateway.request_handler(
                PacketType.LOGS_PUSH.value,
                self._handle_remote_logs,
            ),
        )
        self._dispatcher: MavlinkApplicationDispatcher | None = None
        self._subscriptions: list[Subscription] = []
        self._lock = threading.RLock()
        self._started = False
        self._contract_link = GroundLinkService(
            CallableTransport(
                connected=lambda: self.connected,
                notify=self._send_contract_notification,
                request=self._request_contract,
                register_handler=self._register_contract_handler,
            )
        )

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._state.alive and self._vehicle.connected)

    @property
    def state(self) -> OrinConnectionState:
        with self._lock:
            return replace(self._state)

    @property
    def changed(self) -> EventBus[OrinConnectionState]:
        return self._changed

    @property
    def received(self) -> EventBus[OrinMessage]:
        return self._received

    @property
    def remote_logs(self) -> EventBus[OrinLogRecord]:
        return self._remote_logs

    @property
    def remote_log_batches(self) -> EventBus[tuple[OrinLogRecord, ...]]:
        return self._remote_log_batches

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
            self._state = replace(
                self._state,
                system_id=config.system_id,
                component_id=config.component_id,
            )
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
            self._dispatcher = MavlinkApplicationDispatcher(
                peer,
                handlers=self._handlers,
                thread_name="YkiOrinDispatch",
            )
            dispatcher = self._dispatcher
            self._started = True
        self._subscriptions.extend(
            [
                peer.packets.subscribe(self._on_packet),
                peer.changed.subscribe(self._on_peer_changed),
                peer.errors.subscribe(self._on_channel_error),
                dispatcher.errors.subscribe(self._on_channel_error),
                self._vehicle.connection_changed.subscribe(self._on_vehicle_changed),
            ]
        )
        try:
            dispatcher.start()
            peer.start()
        except Exception:
            dispatcher.stop()
            for subscription in self._subscriptions:
                subscription.cancel()
            self._subscriptions.clear()
            with self._lock:
                self._channel = None
                self._peer = None
                self._dispatcher = None
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
            dispatcher = self._dispatcher
            self._dispatcher = None
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        if dispatcher is not None:
            dispatcher.stop()
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

    def send(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
    ) -> OrinMessage:
        """Send an unacknowledged event through the shared vehicle transport."""

        message = self._decode_contract(
            packet_type,
            payload,
            exchange=ExchangeKind.NOTIFICATION,
        )
        return self._message(self._contract_link.notify(message))

    def issue(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> OrinCommandReceipt:
        wait_timeout = float(timeout or self._config.command_timeout_s)
        if wait_timeout <= 0:
            raise ValueError("Orin komut zaman aşımı pozitif olmalı")
        request = self._decode_contract(
            packet_type,
            payload,
            exchange=ExchangeKind.REQUEST,
        )
        operation = self._contract_link.request(request, timeout=wait_timeout)
        response_payload = operation.to_payload()
        accepted = operation.accepted
        message = operation.message
        round_trip_ms = float(operation.round_trip_ms or 0.0)
        with self._lock:
            self._state = replace(self._state, round_trip_ms=round_trip_ms)
        receipt = OrinCommandReceipt(
            operation.request_id,
            accepted,
            message,
            response_payload,
            round_trip_ms,
        )
        execution = str(response_payload.get("execution", "acknowledged"))
        if (
            request.packet_type == "kamikaze.target.update"
            and accepted
            and execution.strip().lower() in {"updated", "unchanged"}
        ):
            with self._lock:
                self._state = replace(
                    self._state,
                    kamikaze_target_ready=True,
                    kamikaze_target_revision=self._int_value(
                        response_payload.get("target_revision"),
                        self._state.kamikaze_target_revision,
                    ),
                )
                target_state = replace(self._state)
            self._changed.publish(target_state)
        task = str(
            response_payload.get("task")
            or response_payload.get("active_task")
            or request.to_payload().get("task", "")
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
            event_message = f"Orin scripti başarıyla tamamlandı: {task or request.packet_type}"
        elif event_action == "orin.script_failed":
            event_message = f"Orin scripti başarısız: {task or request.packet_type} · {message}"
        self._event(
            event_action,
            event_message,
            severity="success" if accepted else "danger",
            details={
                "packet_id": operation.request_id,
                "packet_type": request.packet_type,
                "round_trip_ms": round_trip_ms,
                "task": task,
                "execution": execution,
                "exit_code": response_payload.get("exit_code"),
                "stdout": response_payload.get("stdout", ""),
                "stderr": response_payload.get("stderr", ""),
            },
            correlation_id=str(operation.request_id),
        )
        return receipt

    def _decode_contract(
        self,
        packet_type: str,
        payload: Mapping[str, Any] | None,
        *,
        exchange: ExchangeKind,
    ):
        """Build a shared wire contract before data reaches the MAVLink peer."""

        return self._contract_gateway.outbound(
            packet_type,
            dict(payload or {}),
            exchange,
        )

    def _send_contract_notification(
        self,
        packet_type: str,
        payload: Mapping[str, Any],
    ) -> MavlinkApplicationPacket:
        if not self._vehicle.connected:
            raise ConnectionError("Telemetri hattı bağlı değil; Orin'e veri gönderilemez")
        if not self.connected:
            raise ConnectionError("Orin heartbeat alınmıyor; veri gönderilmedi")
        with self._lock:
            peer = self._peer
            started = self._started
        if not started or peer is None:
            raise ConnectionError("Orin uygulama kanalı çalışmıyor")
        return peer.send(packet_type, dict(payload))

    def _request_contract(
        self,
        packet_type: str,
        payload: Mapping[str, Any],
        timeout: float | None,
    ) -> RawResponse:
        if not self._vehicle.connected:
            raise ConnectionError("Telemetri hattı bağlı değil; Orin'e komut gönderilemez")
        with self._lock:
            peer = self._peer
            started = self._started
        if not started or peer is None:
            raise ConnectionError("Orin uygulama kanalı çalışmıyor")
        exchange = peer.request(
            packet_type,
            dict(payload),
            response_types=self.RESPONSE_TYPES,
            timeout=float(timeout or self._config.command_timeout_s),
            on_sent=self._log_command_sent,
        )
        response_payload = dict(exchange.response.payload)
        response_payload.setdefault("request_id", exchange.request.packet_id)
        response_payload.setdefault(
            "message",
            "Orin komutu kabul edildi"
            if exchange.response.packet_type == PacketType.SYSTEM_ACK.value
            else "Orin komutu reddedildi",
        )
        return RawResponse(
            exchange.response.packet_type,
            response_payload,
            exchange.round_trip_ms,
        )

    def _register_contract_handler(self, packet_type: str, handler):
        """Adapt one shared request handler to the existing core dispatcher."""

        def adapted(packet: MavlinkApplicationPacket):
            from src.communication import HandlerResult, InboundPacket

            result = handler(
                InboundPacket(
                    packet.packet_type,
                    dict(packet.payload),
                    packet.packet_id,
                    int(packet.source_system or 0),
                    int(packet.source_component or 0),
                )
            )
            if isinstance(result, HandlerResult):
                return MavlinkApplicationResult(
                    accepted=result.accepted,
                    message=result.message,
                    payload=result.to_payload(),
                )
            return result

        return self._handlers.register(
            packet_type,
            self._contract_gateway.request_handler(packet_type, adapted),
        )

    def register_handler(
        self,
        packet_type: str,
        handler: OrinMessageHandler,
    ) -> Subscription:
        """Handle future Orin-to-YKİ commands without changing this service."""

        def adapted(packet: MavlinkApplicationPacket):
            response = handler(self._message(packet))
            if isinstance(response, OrinHandlerResponse):
                return MavlinkApplicationResult(
                    accepted=response.accepted,
                    message=response.message,
                    payload=response.payload,
                )
            return response

        return self._handlers.register(packet_type, adapted)

    def _handle_remote_logs(
        self,
        packet: MavlinkApplicationPacket,
    ) -> MavlinkApplicationResult:
        """Validate, de-duplicate and acknowledge one onboard log batch."""

        try:
            # Core keeps application payloads deeply immutable. The remote-log
            # decoder validates the serialized wire shape, where records are a
            # JSON list, so decode from the packet's detached representation.
            batch = RemoteLogBatch.from_payload(packet.to_dict()["payload"])
        except (TypeError, ValueError) as exc:
            raise LogValidationError("Invalid remote log batch") from exc
        with self._lock:
            last_sequence = self._remote_log_sessions.get(batch.session, 0)
        accepted_records: list[OrinLogRecord] = []
        duplicates = 0
        for record in batch.records:
            if record.sequence <= last_sequence:
                duplicates += 1
                continue
            accepted_records.append(
                OrinLogRecord(
                    sequence=record.sequence,
                    source=record.source,
                    action=record.action,
                    message=record.message,
                    level=record.level,
                    emitted_at=record.emitted_at,
                    details=dict(record.details),
                    device_id=record.device_id,
                    correlation_id=record.correlation_id,
                    session_id=batch.session,
                )
            )
        if accepted_records:
            # One batch event is the persistence boundary. The sequence cursor
            # moves only after all records have been accepted by subscribers.
            self._remote_log_batches.publish(tuple(accepted_records))
            last_sequence = accepted_records[-1].sequence
            with self._lock:
                self._remote_log_sessions[batch.session] = last_sequence
                self._remote_log_sessions.move_to_end(batch.session)
                while len(self._remote_log_sessions) > 8:
                    self._remote_log_sessions.popitem(last=False)
            for record in accepted_records:
                self._remote_logs.publish(record)
        return MavlinkApplicationResult.success(
            {
                "session": batch.session,
                "received": len(accepted_records),
                "duplicates": duplicates,
                "last_sequence": last_sequence,
            },
            message="Logs accepted",
        )

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
            source_systems=self._config.system_id,
            source_components=self._config.component_id,
            fragment_timeout=max(10.0, self._config.heartbeat_timeout_s),
        )

    def _create_peer(
        self,
        channel: MavlinkApplicationChannel,
    ) -> MavlinkApplicationPeer:
        timeout = float(self._config.heartbeat_timeout_s)
        return MavlinkApplicationPeer(
            channel,
            role="yki",
            target_system=self._target_system,
            target_component=lambda: self._config.component_id,
            transport_available=lambda: self._vehicle.connected,
            heartbeat_payload=self._heartbeat_payload,
            heartbeat_interval=1.0,
            heartbeat_timeout=timeout,
        )

    def _heartbeat_payload(self) -> dict[str, Any]:
        # Keep the one-hertz liveness packet compact. Capabilities and mission
        # inventories are transferred only by explicit state/list responses.
        return {
            "role": "yki",
            "health": "ok",
            "version": "1.4.4-beta",
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
            active_tasks = self._string_tuple(
                payload.get("active_tasks"), self._state.active_tasks
            )
            available_tasks = self._string_tuple(
                payload.get("available_tasks"), self._state.available_tasks
            )
            queued_tasks = self._string_tuple(
                payload.get("queued_tasks"), self._state.queued_tasks
            )
            active_missions = self._int_tuple(
                payload.get("active_missions"), self._state.active_missions
            )
            queued_missions = self._int_tuple(
                payload.get("queued_missions"), self._state.queued_missions
            )
            remote_logging = payload.get("remote_logging")
            if not isinstance(remote_logging, dict):
                remote_logging = {}
            kamikaze_target = payload.get("kamikaze_target")
            if not isinstance(kamikaze_target, dict):
                kamikaze_target = {}
            onboard_time = self._utc_milliseconds(payload.get("utc_ms"))
            session_id = str(
                payload.get("session")
                or payload.get("session_id")
                or self._state.session_id
            ).strip()[:96]
            self._state = replace(
                self._state,
                alive=True if is_liveness else self._state.alive,
                endpoint=self._client.endpoint.uri,
                system_id=int(packet.source_system or self._target_system()),
                component_id=int(packet.source_component or self._config.component_id),
                health=str(payload.get("health", self._state.health or "ok")),
                session_id=session_id,
                onboard_time_utc=(
                    onboard_time
                    if onboard_time is not None
                    else self._state.onboard_time_utc
                ),
                onboard_time_received_at=(
                    now
                    if onboard_time is not None
                    else self._state.onboard_time_received_at
                ),
                active_task=str(payload.get("active_task", self._state.active_task)),
                active_tasks=active_tasks,
                available_tasks=available_tasks,
                queued_tasks=queued_tasks,
                active_missions=active_missions,
                queued_missions=queued_missions,
                kamikaze_target_ready=bool(
                    kamikaze_target.get(
                        "ready",
                        self._state.kamikaze_target_ready,
                    )
                ),
                kamikaze_target_revision=self._int_value(
                    kamikaze_target.get("revision"),
                    self._state.kamikaze_target_revision,
                ),
                vehicle_mode=str(payload.get("vehicle_mode", self._state.vehicle_mode)).upper(),
                idle_vehicle_mode=str(payload.get("idle_vehicle_mode", self._state.idle_vehicle_mode)).upper(),
                mission_vehicle_mode=str(payload.get("mission_vehicle_mode", self._state.mission_vehicle_mode)).upper(),
                remote_log_running=bool(remote_logging.get("running", self._state.remote_log_running)),
                remote_log_queued=self._int_value(
                    remote_logging.get("queued"), self._state.remote_log_queued
                ),
                remote_log_sent=self._int_value(
                    remote_logging.get("sent"), self._state.remote_log_sent
                ),
                remote_log_dropped=self._int_value(
                    remote_logging.get("dropped"), self._state.remote_log_dropped
                ),
                remote_log_error=str(remote_logging.get("last_error", self._state.remote_log_error)),
                version=str(payload.get("version", self._state.version)),
                uptime_s=self._float_value(
                    payload.get("uptime_s"), self._state.uptime_s
                ),
                capabilities=capabilities,
                last_heartbeat_at=now if is_liveness else self._state.last_heartbeat_at,
                last_packet_at=now,
                last_error="",
            )
            state = replace(self._state)
        self._changed.publish(state)
        self._received.publish(
            self._message(packet, received_at=now)
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
        self._changed.publish(state)
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
        self._changed.publish(state)
        if was_alive:
            self._event(
                "orin.disconnected",
                reason,
                severity="warning",
                details={"device_id": state.device_id},
            )

    def _target_system(self) -> int:
        return int(self._config.system_id)

    @staticmethod
    def _string_tuple(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return fallback
        return tuple(str(item) for item in value)

    @staticmethod
    def _int_tuple(value: Any, fallback: tuple[int, ...]) -> tuple[int, ...]:
        if not isinstance(value, (list, tuple)):
            return fallback
        result: list[int] = []
        for item in value:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return tuple(result)

    @staticmethod
    def _int_value(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return int(fallback)

    @staticmethod
    def _float_value(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return float(fallback)

    @staticmethod
    def _utc_milliseconds(value: Any) -> datetime | None:
        try:
            resolved = datetime.fromtimestamp(int(value) / 1_000.0, UTC)
        except (OSError, OverflowError, TypeError, ValueError):
            return None
        return resolved if 2020 <= resolved.year <= 2200 else None

    @staticmethod
    def _message(
        packet: MavlinkApplicationPacket,
        *,
        received_at: datetime | None = None,
    ) -> OrinMessage:
        return OrinMessage(
            packet.packet_type,
            packet.payload,
            packet.packet_id,
            int(packet.source_system or 0),
            int(packet.source_component or 0),
            received_at,
        )

    def _publish(self) -> None:
        self._changed.publish(self.state)

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
