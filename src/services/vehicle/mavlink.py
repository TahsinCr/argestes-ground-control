from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

from src.core.events import EventBus, Subscription
from src.core.mavlink import MavlinkClient, MavlinkMessageFilter
from src.core.mavlink.filter import (
    mavlink_message_type,
    mavlink_source_component,
    mavlink_source_system,
)
from src.models.commands import CommandRequest
from src.models.mission import MapOverlay
from src.models.vehicle import (
    VehicleConnectionConfig,
    VehicleConnectionState,
    VehicleTransportStats,
)
from src.services.contracts.vehicle import MissionTransferInterrupted, VehicleService
from src.services.logging_service import ServiceLogger


class NullVehicleService(VehicleService):
    """Service that remains safely offline when no vehicle link is configured."""

    def __init__(self) -> None:
        self._changed = EventBus[VehicleConnectionState]()
        self._state = VehicleConnectionState(False, "")

    @property
    def connected(self) -> bool:
        return False

    @property
    def state(self) -> VehicleConnectionState:
        return self._state

    @property
    def connection_changed(self) -> EventBus[VehicleConnectionState]:
        return self._changed

    @property
    def transport_stats(self) -> VehicleTransportStats:
        return VehicleTransportStats()

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def configure_connection(self, config: VehicleConnectionConfig) -> None:
        del config
        self._unavailable("CONFIGURE_CONNECTION")

    def reconnect(self, config: VehicleConnectionConfig | None = None) -> None:
        del config
        self._unavailable("RECONNECT")

    def subscribe(self, message_types, callback) -> Subscription:
        del message_types, callback
        return Subscription(0, lambda: None)

    def request_parameters(self) -> None:
        self._unavailable("PARAM_REQUEST_LIST")

    def set_parameter(self, name: str, value: object, value_type: int | None = None) -> None:
        del value, value_type
        self._unavailable(f"PARAM_SET:{name}")

    def mode_name(self, heartbeat: Any, fallback: str = "UNKNOWN") -> str:
        del heartbeat
        return fallback

    def set_default_mission_altitude(self, altitude_m: float) -> None:
        if float(altitude_m) <= 0:
            raise ValueError("Varsayılan görev irtifası pozitif olmalı")

    def execute(self, request: CommandRequest) -> bool:
        self._unavailable(request.code)

    def upload_mission(
        self,
        overlays: Iterable[MapOverlay],
        *,
        loop: bool = False,
    ) -> int:
        del overlays, loop
        self._unavailable("MISSION_UPLOAD")

    def download_mission(self) -> list[MapOverlay]:
        self._unavailable("MISSION_DOWNLOAD")

    @staticmethod
    def _unavailable(action: str) -> None:
        raise ConnectionError(f"Uçuş cihazı bağlantısı hazır değil: {action}")


class MavlinkVehicleService(VehicleService):
    """Vehicle adapter hiding core MAVLink details from domain services."""

    FORCE_ARM_MAGIC = 21_196.0
    TRANSPORT_STATUS_TYPES = frozenset({"RADIO", "RADIO_STATUS"})
    DEFAULT_STREAM_RATES_HZ = {
        "ATTITUDE": 20.0,
        "GLOBAL_POSITION_INT": 10.0,
        "LOCAL_POSITION_NED": 10.0,
        "VFR_HUD": 10.0,
        "SCALED_IMU": 10.0,
        "NAV_CONTROLLER_OUTPUT": 5.0,
        "GPS_RAW_INT": 5.0,
        "RC_CHANNELS": 5.0,
        "SYS_STATUS": 2.0,
        "BATTERY_STATUS": 2.0,
        "HOME_POSITION": 1.0,
    }

    def __init__(
        self,
        client: MavlinkClient,
        logger: ServiceLogger | None = None,
        *,
        operation_timeout: float = 5.0,
        default_mission_altitude: float = 100.0,
        custom_handlers: dict[str, Callable[[CommandRequest], bool | None]] | None = None,
        stream_rates_hz: dict[str, float] | None = None,
    ) -> None:
        if operation_timeout <= 0:
            raise ValueError("Araç işlem zaman aşımı pozitif olmalı")
        self._client = client
        self._logger = logger
        self._operation_timeout = float(operation_timeout)
        self._default_mission_altitude = float(default_mission_altitude)
        if self._default_mission_altitude <= 0:
            raise ValueError("Varsayılan görev irtifası pozitif olmalı")
        self._changed = EventBus[VehicleConnectionState]()
        self._state = VehicleConnectionState(False, client.endpoint.uri)
        self._subscriptions: list[Subscription] = []
        self._started = False
        self._state_lock = threading.RLock()
        self._mission_transfer_lock = threading.Lock()
        self._mission_download_cancel = threading.Event()
        self._last_heartbeat = 0.0
        self._received_packets_baseline = 0
        self._sent_packets_baseline = 0
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._custom_handlers = {
            code.strip().upper(): handler for code, handler in (custom_handlers or {}).items()
        }
        source_rates = stream_rates_hz or self.DEFAULT_STREAM_RATES_HZ
        self._stream_rates_hz = {
            str(message_type).strip().upper(): float(rate)
            for message_type, rate in source_rates.items()
            if str(message_type).strip() and float(rate) > 0.0
        }

    @property
    def client(self) -> MavlinkClient:
        return self._client

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._state.connected and self._client.is_connected

    @property
    def state(self) -> VehicleConnectionState:
        with self._state_lock:
            return self._state

    @property
    def connection_changed(self) -> EventBus[VehicleConnectionState]:
        return self._changed

    @property
    def transport_stats(self) -> VehicleTransportStats:
        router_stats = self._client.router.stats
        return VehicleTransportStats(
            received_packets=max(
                0,
                router_stats.received_messages - self._received_packets_baseline,
            ),
            sent_packets=max(
                0,
                self._client.connection.sent_messages - self._sent_packets_baseline,
            ),
            estimated_dropped_packets=router_stats.estimated_dropped_messages,
            delivery_quality=router_stats.delivery_quality,
        )

    def configure_connection(self, config: VehicleConnectionConfig) -> None:
        if self._started:
            raise RuntimeError("Araç bağlantısını yapılandırmadan önce durdurun")
        endpoint = self._endpoint_for(config)
        self._client.configure_endpoint(endpoint)
        with self._state_lock:
            self._state = VehicleConnectionState(False, endpoint.uri)

    def reconnect(self, config: VehicleConnectionConfig | None = None) -> None:
        endpoint = self._endpoint_for(config) if config is not None else None
        self.stop()
        if endpoint is not None:
            self._client.configure_endpoint(endpoint)
            with self._state_lock:
                self._state = VehicleConnectionState(False, endpoint.uri)
        self.start()

    def _endpoint_for(self, config: VehicleConnectionConfig):
        return replace(
            self._client.endpoint,
            uri=config.endpoint.strip(),
            baud=int(config.baud),
            heartbeat_timeout=float(config.heartbeat_timeout_s),
        )

    def start(self) -> None:
        if self._started:
            return
        self._received_packets_baseline = self._client.router.stats.received_messages
        self._sent_packets_baseline = self._client.connection.sent_messages
        self._subscriptions.extend(
            [
                self._client.connection.errors.subscribe(self._on_connection_error),
                self._client.router.errors.subscribe(self._on_router_error),
                self.subscribe("HEARTBEAT", self._on_heartbeat),
            ]
        )
        try:
            self._client.start()
        except Exception:
            self._cancel_subscriptions()
            raise
        connection = self._client.connection
        heartbeat = connection.message_state.get("HEARTBEAT")
        with self._state_lock:
            self._state = VehicleConnectionState(
                True,
                self._client.endpoint.uri,
                connection.target_system,
                connection.target_component,
                int(getattr(heartbeat, "type", 0)) if heartbeat is not None else None,
                int(getattr(heartbeat, "autopilot", 0)) if heartbeat is not None else None,
            )
            self._last_heartbeat = time.monotonic()
        self._started = True
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watch_heartbeat,
            name="VehicleHeartbeatWatchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        self._request_telemetry_streams()
        self._changed.publish(self._state)
        self._event(
            "success",
            "vehicle.connected",
            f"Uçuş cihazına bağlanıldı ({self._state.device_id})",
            details=self._state.to_dict(),
        )

    def _request_telemetry_streams(self) -> None:
        mavlink = self._client.mavlink
        requested: dict[str, float] = {}
        rejected: dict[str, str] = {}
        system, component = self._target
        for message_type, rate_hz in self._stream_rates_hz.items():
            message_id = getattr(mavlink, f"MAVLINK_MSG_ID_{message_type}", None)
            if message_id is None:
                rejected[message_type] = "message id unavailable"
                continue
            try:
                self._client.request_message_rate(
                    int(message_id),
                    rate_hz,
                    target_system=system,
                    target_component=component,
                )
            except Exception as exc:
                rejected[message_type] = str(exc)
            else:
                requested[message_type] = rate_hz
        if requested:
            self._event(
                "success",
                "vehicle.telemetry_stream_profile_requested",
                "Uçuş cihazından hızlı telemetri akışı istendi",
                details={
                    "rates": ", ".join(
                        f"{name}={rate:g} Hz"
                        for name, rate in requested.items()
                    ),
                    "requested_count": len(requested),
                },
            )
        if rejected:
            self._event(
                "warning",
                "vehicle.telemetry_stream_profile_partial",
                "Bazı telemetri mesaj hızları istenemedi",
                details={
                    "messages": ", ".join(rejected),
                    "failed_count": len(rejected),
                },
            )

    def stop(self) -> None:
        if not self._started:
            return
        previous = self._state
        self._watchdog_stop.set()
        thread = self._watchdog_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._watchdog_thread = None
        try:
            self._client.stop()
        finally:
            self._started = False
            with self._state_lock:
                self._state = VehicleConnectionState(
                    False,
                    self._client.endpoint.uri,
                    previous.system_id,
                    previous.component_id,
                    previous.vehicle_type,
                    previous.autopilot_type,
                )
            self._changed.publish(self._state)
            self._cancel_subscriptions()
        self._event(
            "info",
            "vehicle.disconnected",
            f"Uçuş cihazı bağlantısı kapatıldı ({previous.device_id})",
            device_id=previous.device_id,
        )

    def subscribe(
        self,
        message_types: str | Iterable[str],
        callback: Callable[[Any], None],
    ) -> Subscription:
        return self._client.subscribe(
            callback,
            MavlinkMessageFilter(
                message_types=message_types,
                predicate=self._is_active_connection_message,
            ),
        )

    def _is_active_connection_message(self, message: Any) -> bool:
        """Read radio status from the link and vehicle data from target SYS/COMP."""

        try:
            if mavlink_message_type(message) in self.TRANSPORT_STATUS_TYPES:
                return True
        except (TypeError, ValueError):
            pass
        return self._is_target_vehicle_message(message)

    def _is_target_vehicle_message(self, message: Any) -> bool:
        if not self._is_target_system_message(message):
            return False
        try:
            if mavlink_message_type(message) != "HEARTBEAT":
                return True
        except (TypeError, ValueError):
            return True

        source_component = mavlink_source_component(message)
        if source_component is None:
            return True
        try:
            target_component = self._client.connection.target_component
        except (AttributeError, ConnectionError):
            return True
        return not target_component or source_component == target_component

    def _is_target_system_message(self, message: Any) -> bool:
        source_system = mavlink_source_system(message)
        if source_system is None:
            return True
        try:
            target_system = self._client.connection.target_system
        except ConnectionError:
            return True
        return not target_system or source_system == target_system

    def request_parameters(self) -> None:
        system, component = self._target
        self._client.call_mav("param_request_list_send", system, component)
        self._event(
            "info",
            "parameter.refresh_requested",
            "Araç parametre kataloğu istendi",
        )

    def set_parameter(self, name: str, value: object, value_type: int | None = None) -> None:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name.encode("ascii")) > 16:
            raise ValueError(f"Geçersiz MAVLink parametre adı: {name!r}")
        numeric = self._numeric_parameter_value(normalized_name, value)
        system, component = self._target
        param_type = value_type or self._client.mavlink.MAV_PARAM_TYPE_REAL32
        self._client.call_mav(
            "param_set_send",
            system,
            component,
            normalized_name.encode("ascii"),
            numeric,
            int(param_type),
        )

    def mode_name(self, heartbeat: Any, fallback: str = "UNKNOWN") -> str:
        value = getattr(self._client.raw, "flightmode", None)
        if value:
            return str(value)
        try:
            from pymavlink import mavutil

            value = mavutil.mode_string_v10(heartbeat)
        except (ImportError, AttributeError, TypeError, ValueError):
            return fallback
        return str(value) if value and value != "Mode(0x00000000)" else fallback

    def set_default_mission_altitude(self, altitude_m: float) -> None:
        value = float(altitude_m)
        if value <= 0:
            raise ValueError("Varsayılan görev irtifası pozitif olmalı")
        self._default_mission_altitude = value

    def execute(self, request: CommandRequest) -> bool:
        custom = self._custom_handlers.get(request.code)
        if custom is not None:
            result = custom(request)
            return result if isinstance(result, bool) else False
        handlers: dict[str, Callable[[], bool]] = {
            "ARM": lambda: self._arm(True),
            "FORCE_ARM": lambda: self._force_arm(request),
            "DISARM": lambda: self._arm(False),
            "SET_MODE": lambda: self._set_mode(str(request.parameters["mode"])),
            "GOTO": lambda: self._goto(request.parameters),
            "SET_HOME": lambda: self._set_home(request.parameters),
            "LAND": lambda: self._land(request.parameters),
            "MISSION_UPLOAD": lambda: bool(
                self.upload_mission(
                    (
                        item if isinstance(item, MapOverlay) else MapOverlay.from_dict(item)
                        for item in request.parameters.get("overlays", [])
                    ),
                    loop=bool(request.parameters.get("loop", False)),
                )
            ),
        }
        handler = handlers.get(request.code)
        if handler is None:
            raise NotImplementedError(f"Araç davranışı tanımlı değil: {request.code}")
        return handler()

    def _force_arm(self, request: CommandRequest) -> bool:
        if request.parameters.get("operator_confirmation") is not True:
            raise PermissionError("Zorla ARM için açık operatör onayı gerekli")
        return self._arm(True, force=True)

    def upload_mission(
        self,
        overlays: Iterable[MapOverlay],
        *,
        loop: bool = False,
    ) -> int:
        # Operator uploads take precedence over an automatic plan refresh.
        self._mission_download_cancel.set()
        with self._mission_transfer_lock:
            source_items = list(overlays)
            self._validate_mission_structure(source_items, loop=loop)
            mavlink = self._client.mavlink
            route_items, ignored = self._mission_items(
                source_items,
                mavlink,
                default_altitude=self._default_mission_altitude,
            )
            if ignored:
                self._event(
                    "info",
                    "mission.items_retained_locally",
                    f"{len(ignored)} uygulama öğesi yalnızca backend durumunda tutuldu",
                    details={
                        "count": len(ignored),
                        "overlay_types": sorted(set(ignored)),
                    },
                )
            if not route_items:
                raise ValueError("Araca gönderilebilecek görev öğesi bulunamadı")

            items = [self._mission_home_item(route_items, mavlink), *route_items]
            if loop:
                loop_target = self._loop_target_sequence(source_items)
                items.append(self._mission_jump_item(loop_target, mavlink))

            system, component = self._target
            mission_type = mavlink.MAV_MISSION_TYPE_MISSION
            boundary = self._client.router.sequence
            self._client.call_mav("mission_count_send", system, component, len(items), mission_type)

            sent_sequences: set[int] = set()
            deadline = time.monotonic() + self._operation_timeout * max(2, len(items))
            while len(sent_sequences) < len(items):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Görev yükleme isteği zaman aşımına uğradı")
                request = self._client.wait_for(
                    {"MISSION_REQUEST_INT", "MISSION_REQUEST"},
                    timeout=min(self._operation_timeout, remaining),
                    after_sequence=boundary,
                )
                sequence = int(request.seq)
                if not 0 <= sequence < len(items):
                    raise ValueError(f"Araç geçersiz görev sırası istedi: {sequence}")
                boundary = self._client.router.sequence
                self._client.call_mav(
                    "mission_item_int_send",
                    system,
                    component,
                    sequence,
                    *items[sequence],
                    mission_type,
                )
                sent_sequences.add(sequence)

            acknowledgement = self._client.wait_for(
                "MISSION_ACK",
                timeout=self._operation_timeout,
                after_sequence=boundary,
            )
            if int(acknowledgement.type) != mavlink.MAV_MISSION_ACCEPTED:
                raise RuntimeError(f"Araç görev planını reddetti: {acknowledgement.type}")
            self._client.call_mav("mission_set_current_send", system, component, 1)
            route_item_count = len(route_items)
            self._event(
                "success",
                "mission.upload_confirmed",
                f"{route_item_count} görev öğesi uçuş cihazı tarafından kabul edildi",
                details={
                    "item_count": route_item_count,
                    "wire_item_count": len(items),
                    "loop": loop,
                    "start_sequence": 1,
                },
            )
            return route_item_count

    def download_mission(self) -> list[MapOverlay]:
        with self._mission_transfer_lock:
            self._mission_download_cancel.clear()
            mavlink = self._client.mavlink
            system, component = self._target
            mission_type = mavlink.MAV_MISSION_TYPE_MISSION
            boundary = self._client.router.sequence
            self._client.call_mav("mission_request_list_send", system, component, mission_type)
            count_message = self._wait_for_mission_download(
                "MISSION_COUNT",
                after_sequence=boundary,
            )
            count = int(count_message.count)
            overlays: list[MapOverlay] = []
            loop_detected = False
            for sequence in range(count):
                boundary = self._client.router.sequence
                self._client.call_mav(
                    "mission_request_int_send",
                    system,
                    component,
                    sequence,
                    mission_type,
                )
                item = self._wait_for_mission_download(
                    {"MISSION_ITEM_INT", "MISSION_ITEM"},
                    predicate=lambda message, expected=sequence: int(message.seq) == expected,
                    after_sequence=boundary,
                )
                if (
                    int(getattr(item, "command", -1))
                    == int(getattr(mavlink, "MAV_CMD_DO_JUMP", -2))
                    and float(getattr(item, "param2", 0.0)) < 0
                ):
                    loop_detected = True
                overlay = self._overlay_from_mission_item(item, mavlink)
                if overlay is not None:
                    overlays.append(overlay)
            if loop_detected and overlays:
                overlays[0].metadata["mission_loop"] = True
            self._client.call_mav(
                "mission_ack_send",
                system,
                component,
                mavlink.MAV_MISSION_ACCEPTED,
                mission_type,
            )
            self._event(
                "success",
                "mission.download_confirmed",
                f"Uçuş cihazından {count} görev öğesi alındı",
                details={"vehicle_item_count": count, "supported_item_count": len(overlays)},
            )
            return overlays

    def _wait_for_mission_download(
        self,
        message_types,
        *,
        after_sequence: int,
        predicate=None,
    ) -> Any:
        deadline = time.monotonic() + self._operation_timeout
        while True:
            if self._mission_download_cancel.is_set():
                raise MissionTransferInterrupted("Otomatik görev indirme kullanıcı gönderimi için ertelendi")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expected = sorted(message_types) if isinstance(message_types, set) else [message_types]
                raise TimeoutError(f"MAVLink mesajı beklenirken zaman aşımı: {expected}")
            try:
                return self._client.wait_for(
                    message_types,
                    predicate=predicate,
                    timeout=min(0.25, remaining),
                    after_sequence=after_sequence,
                )
            except TimeoutError:
                continue

    @property
    def _target(self) -> tuple[int, int]:
        if not self.connected:
            raise ConnectionError("Uçuş cihazı bağlı değil")
        connection = self._client.connection
        return connection.target_system, connection.target_component

    def _arm(self, armed: bool, *, force: bool = False) -> bool:
        command = self._client.mavlink.MAV_CMD_COMPONENT_ARM_DISARM
        if force and not armed:
            raise ValueError("Zorla DISARM bu arayüz tarafından desteklenmiyor")
        boundary = self._client.router.sequence
        self._command_long(
            command,
            1.0 if armed else 0.0,
            self.FORCE_ARM_MAGIC if force else 0.0,
        )
        armed_flag = int(
            getattr(self._client.mavlink, "MAV_MODE_FLAG_SAFETY_ARMED", 128)
        )
        self._client.wait_for(
            "HEARTBEAT",
            predicate=lambda message: (
                self._is_target_vehicle_message(message)
                and bool(int(getattr(message, "base_mode", 0)) & armed_flag) is armed
            ),
            timeout=self._operation_timeout,
            after_sequence=boundary,
        )
        return True

    def _set_mode(self, mode: str) -> bool:
        normalized = mode.strip().upper()
        mapping = self._client.call_raw("mode_mapping") or {}
        if normalized not in mapping:
            raise ValueError(f"Araç modu desteklenmiyor: {normalized}")
        custom_mode = int(mapping[normalized])
        attempt_timeout = self._operation_timeout / 2.0
        last_error: TimeoutError | None = None
        for _attempt in range(2):
            boundary = self._client.router.sequence
            self._client.call_raw("set_mode", custom_mode)
            try:
                self._client.wait_for(
                    "HEARTBEAT",
                    predicate=lambda message: (
                        self._is_target_vehicle_message(message)
                        and int(getattr(message, "custom_mode", -1)) == custom_mode
                    ),
                    timeout=attempt_timeout,
                    after_sequence=boundary,
                )
            except TimeoutError as exc:
                last_error = exc
                continue
            return True
        assert last_error is not None
        raise last_error

    def _goto(self, parameters: dict[str, Any]) -> bool:
        system, component = self._target
        mavlink = self._client.mavlink
        latitude = int(float(parameters["latitude"]) * 1e7)
        longitude = int(float(parameters["longitude"]) * 1e7)
        altitude_value = parameters.get("altitude", parameters.get("target_altitude"))
        altitude = float(altitude_value or 0.0)
        type_mask = 0b0000111111111000
        if altitude_value is None:
            type_mask |= 1 << 2
        self._client.call_mav(
            "set_position_target_global_int_send",
            0,
            system,
            component,
            mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            type_mask,
            latitude,
            longitude,
            altitude,
            0, 0, 0, 0, 0, 0, 0, 0,
        )
        self._event(
            "info",
            "vehicle.goto_sent",
            "Konum hedefi uçuş cihazına iletildi",
            details={"latitude": latitude / 1e7, "longitude": longitude / 1e7, "altitude": altitude_value},
        )
        return False

    def _set_home(self, parameters: dict[str, Any]) -> bool:
        latitude = float(parameters["latitude"])
        longitude = float(parameters["longitude"])
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90.0 <= latitude <= 90.0
            or not -180.0 <= longitude <= 180.0
        ):
            raise ValueError("Home koordinatı geçersiz")
        altitude_value = parameters.get("altitude")
        if altitude_value is None:
            position = self._client.latest("GLOBAL_POSITION_INT")
            altitude = (
                float(getattr(position, "alt", 0.0)) / 1000.0
                if position is not None
                else 0.0
            )
        else:
            altitude = float(altitude_value)
        if not math.isfinite(altitude):
            raise ValueError("Home irtifası geçersiz")
        self._command_long(
            self._client.mavlink.MAV_CMD_DO_SET_HOME,
            0,
            0,
            0,
            0,
            latitude,
            longitude,
            altitude,
        )
        self._event(
            "success",
            "vehicle.home_set",
            "Home/merkez noktası uçuş cihazında güncellendi",
            details={
                "latitude": latitude,
                "longitude": longitude,
                "altitude": altitude,
            },
        )
        return True

    def _land(self, parameters: dict[str, Any]) -> bool:
        latitude = float(parameters.get("latitude", 0.0))
        longitude = float(parameters.get("longitude", 0.0))
        altitude = float(parameters.get("altitude", 0.0))
        self._command_long(
            self._client.mavlink.MAV_CMD_NAV_LAND,
            0, 0, 0, 0, latitude, longitude, altitude,
        )
        return True

    def _command_long(self, command: int, *parameters: float | int) -> None:
        system, component = self._target
        values = [float(value) for value in parameters[:7]]
        values.extend([0.0] * (7 - len(values)))
        attempt_timeout = self._operation_timeout / 2.0
        last_error: TimeoutError | None = None
        for confirmation in range(2):
            boundary = self._client.router.sequence
            self._client.call_mav(
                "command_long_send",
                system,
                component,
                int(command),
                confirmation,
                *values,
            )
            deadline = time.monotonic() + attempt_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    last_error = TimeoutError(
                        f"MAVLink komut onayı zaman aşımı: {command}"
                    )
                    break
                try:
                    acknowledgement = self._client.wait_for(
                        "COMMAND_ACK",
                        predicate=lambda message: int(message.command) == int(command),
                        timeout=remaining,
                        after_sequence=boundary,
                    )
                except TimeoutError as exc:
                    last_error = exc
                    break
                result = int(acknowledgement.result)
                if result == self._client.mavlink.MAV_RESULT_IN_PROGRESS:
                    boundary = self._client.router.sequence
                    continue
                if result != self._client.mavlink.MAV_RESULT_ACCEPTED:
                    raise RuntimeError(
                        f"MAVLink komutu reddedildi: command={command}, result={result}"
                    )
                return
        assert last_error is not None
        raise last_error

    @staticmethod
    def _numeric_parameter_value(name: str, value: object) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
        raise TypeError(f"MAVLink parametresi sonlu sayısal olmalı: {name}")

    @staticmethod
    def _validate_mission_structure(
        overlays: Iterable[MapOverlay],
        *,
        loop: bool,
    ) -> None:
        ordered = [
            overlay
            for _index, overlay in sorted(
                enumerate(overlays),
                key=lambda pair: (
                    pair[1].route_order
                    if pair[1].route_order is not None
                    else 1_000_000,
                    pair[0],
                ),
            )
            if overlay.is_vehicle_mission
        ]
        if not ordered:
            raise ValueError("Araca gönderilebilecek görev öğesi bulunamadı")

        takeoff_indexes = [
            index
            for index, overlay in enumerate(ordered)
            if overlay.overlay_type == "takeoff"
        ]
        if len(takeoff_indexes) > 1:
            raise ValueError("Görev planında yalnızca bir TAKEOFF noktası olabilir")
        if takeoff_indexes and takeoff_indexes[0] != 0:
            raise ValueError("TAKEOFF noktası görev sırasının ilk öğesi olmalı")

        terminal = [
            (index, overlay.overlay_type)
            for index, overlay in enumerate(ordered)
            if overlay.overlay_type in {"land", "rtl", "loiter"}
        ]
        if len(terminal) > 1:
            types = ", ".join(item_type.upper() for _index, item_type in terminal)
            raise ValueError(f"Görev planında birden fazla terminal öğe var: {types}")
        if terminal and terminal[0][0] != len(ordered) - 1:
            raise ValueError(
                f"{terminal[0][1].upper()} terminal komutu görev sırasının sonunda olmalı"
            )

        if loop:
            if terminal:
                raise ValueError(
                    "Döngü görevinde terminal öğe kullanılamaz: "
                    + terminal[0][1]
                )
            if not any(
                overlay.overlay_type in {"waypoint", "route"}
                and overlay.points
                for overlay in ordered
            ):
                raise ValueError(
                    "Döngü görevi için en az bir WAYPOINT veya ROUTE öğesi gerekli"
                )

    @staticmethod
    def _mission_items(
        overlays: Iterable[MapOverlay],
        mavlink: Any,
        *,
        default_altitude: float = 100.0,
    ) -> tuple[list[tuple[Any, ...]], list[str]]:
        supported = {
            "takeoff": mavlink.MAV_CMD_NAV_TAKEOFF,
            "waypoint": mavlink.MAV_CMD_NAV_WAYPOINT,
            "loiter": mavlink.MAV_CMD_NAV_LOITER_UNLIM,
            "rtl": mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
            "land": mavlink.MAV_CMD_NAV_LAND,
        }
        source = list(overlays)
        ordered = sorted(
            enumerate(source),
            key=lambda pair: (pair[1].route_order if pair[1].route_order is not None else 1_000_000, pair[0]),
        )
        result: list[tuple[Any, ...]] = []
        ignored: list[str] = []
        for _index, overlay in ordered:
            overlay_type = overlay.overlay_type
            command = mavlink.MAV_CMD_NAV_WAYPOINT if overlay_type == "route" else supported.get(overlay_type)
            if command is None:
                ignored.append(overlay_type)
                continue
            points = list(overlay.points)
            if overlay_type == "rtl":
                points = points or [[0.0, 0.0, 0.0]]
            for point in points:
                is_rtl = overlay_type == "rtl"
                latitude = int(float(point[0]) * 1e7) if not is_rtl else 0
                longitude = int(float(point[1]) * 1e7) if not is_rtl else 0
                fallback_altitude = 0.0 if overlay_type == "land" else default_altitude
                altitude = float(point[2]) if len(point) > 2 else float(
                    overlay.metadata.get("altitude", fallback_altitude)
                )
                radius = float(overlay.radius_m or 0.0)
                acceptance_radius = radius if overlay_type in {"waypoint", "route"} else 0.0
                loiter_radius = radius if overlay_type == "loiter" else 0.0
                result.append(
                    (
                        mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        command,
                        0,
                        1,
                        0.0,
                        acceptance_radius,
                        loiter_radius,
                        float("nan"),
                        latitude,
                        longitude,
                        altitude,
                    )
                )
        return result, ignored

    def _mission_home_item(
        self,
        route_items: list[tuple[Any, ...]],
        mavlink: Any,
    ) -> tuple[Any, ...]:
        latitude = 0
        longitude = 0
        latest = getattr(self._client, "latest", None)
        if callable(latest):
            home = latest("HOME_POSITION")
            if home is not None:
                latitude = int(getattr(home, "latitude", 0) or 0)
                longitude = int(getattr(home, "longitude", 0) or 0)
            if latitude == 0 and longitude == 0:
                position = latest("GLOBAL_POSITION_INT")
                if position is not None:
                    latitude = int(getattr(position, "lat", 0) or 0)
                    longitude = int(getattr(position, "lon", 0) or 0)
        if latitude == 0 and longitude == 0:
            latitude = int(route_items[0][8])
            longitude = int(route_items[0][9])
        return (
            mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            mavlink.MAV_CMD_NAV_WAYPOINT,
            0,
            1,
            0.0,
            0.0,
            0.0,
            float("nan"),
            latitude,
            longitude,
            0.0,
        )

    @staticmethod
    def _loop_target_sequence(overlays: Iterable[MapOverlay]) -> int:
        ordered = sorted(
            enumerate(overlays),
            key=lambda pair: (
                pair[1].route_order
                if pair[1].route_order is not None
                else 1_000_000,
                pair[0],
            ),
        )
        sequence = 1  # Sequence 0 is the MAVLink mission origin/home record.
        for _index, overlay in ordered:
            if not overlay.is_vehicle_mission:
                continue
            points = list(overlay.points)
            if overlay.overlay_type in {"waypoint", "route"} and points:
                return sequence
            if overlay.overlay_type == "rtl":
                points = points or [[0.0, 0.0, 0.0]]
            sequence += len(points)
        raise ValueError("Döngü görevi için en az bir waypoint gerekli")

    @staticmethod
    def _mission_jump_item(sequence: int, mavlink: Any) -> tuple[Any, ...]:
        return (
            getattr(mavlink, "MAV_FRAME_MISSION", 2),
            mavlink.MAV_CMD_DO_JUMP,
            0,
            1,
            float(sequence),
            -1.0,
            0.0,
            0.0,
            0,
            0,
            0.0,
        )

    @staticmethod
    def _overlay_from_mission_item(message: Any, mavlink: Any) -> MapOverlay | None:
        if int(getattr(message, "seq", -1)) == 0:
            return None
        command_types = {
            mavlink.MAV_CMD_NAV_TAKEOFF: ("takeoff", "#78b7ff"),
            mavlink.MAV_CMD_NAV_WAYPOINT: ("waypoint", "#74d99f"),
            mavlink.MAV_CMD_NAV_LOITER_UNLIM: ("loiter", "#3f9f63"),
            mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH: ("rtl", "#f4b942"),
            mavlink.MAV_CMD_NAV_LAND: ("land", "#2f7ed8"),
        }
        mapping = command_types.get(int(message.command))
        if mapping is None:
            return None
        overlay_type, color = mapping
        is_int = str(message.get_type()).upper() == "MISSION_ITEM_INT"
        latitude = float(message.x) / 1e7 if is_int else float(message.x)
        longitude = float(message.y) / 1e7 if is_int else float(message.y)
        altitude = float(message.z)
        radius_value = (
            float(getattr(message, "param2", 0.0))
            if int(message.command) == mavlink.MAV_CMD_NAV_WAYPOINT
            else float(getattr(message, "param3", 0.0))
        )
        radius = abs(radius_value) or None
        return MapOverlay(
            overlay_id=f"vehicle-{overlay_type}-{int(message.seq)}",
            overlay_type=overlay_type,
            label=f"{overlay_type.upper()} {int(message.seq)}",
            points=[[latitude, longitude, altitude]],
            color=color,
            radius_m=radius,
            metadata={"route_order": int(message.seq), "source": "flight-controller"},
        )

    def _on_connection_error(self, error: Exception) -> None:
        self._event("danger", "vehicle.connection_error", str(error))

    def _on_heartbeat(self, heartbeat: Any) -> None:
        recovered: VehicleConnectionState | None = None
        with self._state_lock:
            self._last_heartbeat = time.monotonic()
            if self._started and not self._state.connected and self._client.is_connected:
                system_id = int(getattr(heartbeat, "get_srcSystem", lambda: 0)() or self._state.system_id)
                component_id = int(
                    getattr(heartbeat, "get_srcComponent", lambda: 0)() or self._state.component_id
                )
                self._state = VehicleConnectionState(
                    True,
                    self._client.endpoint.uri,
                    system_id,
                    component_id,
                    int(getattr(heartbeat, "type", 0)),
                    int(getattr(heartbeat, "autopilot", 0)),
                )
                recovered = self._state
        if recovered is not None:
            self._changed.publish(recovered)
            self._event(
                "success",
                "vehicle.link_recovered",
                f"Uçuş cihazı heartbeat bağlantısı geri geldi ({recovered.device_id})",
                details=recovered.to_dict(),
                device_id=recovered.device_id,
            )

    def _watch_heartbeat(self) -> None:
        timeout = float(self._client.endpoint.heartbeat_timeout)
        poll_interval = min(1.0, max(0.2, timeout / 4.0))
        while not self._watchdog_stop.wait(poll_interval):
            lost: VehicleConnectionState | None = None
            with self._state_lock:
                if (
                    self._state.connected
                    and self._last_heartbeat > 0
                    and time.monotonic() - self._last_heartbeat > timeout
                ):
                    previous = self._state
                    self._state = VehicleConnectionState(
                        False,
                        previous.endpoint,
                        previous.system_id,
                        previous.component_id,
                        previous.vehicle_type,
                        previous.autopilot_type,
                    )
                    lost = self._state
            if lost is not None:
                self._changed.publish(lost)
                self._event(
                    "warning",
                    "vehicle.link_lost",
                    f"Uçuş cihazı heartbeat bağlantısı kesildi ({lost.device_id})",
                    details={"heartbeat_timeout_s": timeout},
                    device_id=lost.device_id,
                )

    def _on_router_error(self, error: Any) -> None:
        self._event(
            "danger",
            "vehicle.router_error",
            f"MAVLink {getattr(error, 'phase', 'router')} hatası: {getattr(error, 'error', error)}",
        )

    def _cancel_subscriptions(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()

    def _event(
        self,
        severity: str,
        action: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
        device_id: str | None = None,
    ) -> None:
        if self._logger:
            self._logger.event(
                action,
                message,
                severity=severity,
                details=details,
                device_id=device_id or (self._state.device_id if self._state.connected else None),
            )
