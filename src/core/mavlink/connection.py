from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventStream
from src.core.mavlink.endpoint import MavlinkEndpoint
from src.core.mavlink.filter import MessageTypeInput, normalize_message_types


ConnectionFactory = Callable[[MavlinkEndpoint], Any]


class MavlinkUnavailableError(RuntimeError):
    pass


class MavlinkConnection(Service):
    """pymavlink bağlantısının sahipliğini ve thread-safe I/O erişimini yönetir."""

    def __init__(
        self,
        endpoint: MavlinkEndpoint,
        *,
        connection_factory: ConnectionFactory | None = None,
        mavutil_module: Any | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.connection_changed = EventStream[bool]()
        self.errors = EventStream[Exception]()
        self._connection: Any | None = None
        self._connection_factory = connection_factory
        self._mavutil_module = mavutil_module
        self._lifecycle_lock = threading.RLock()
        self._receive_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._sent_messages = 0

    @property
    def is_connected(self) -> bool:
        with self._lifecycle_lock:
            return self._connection is not None

    @property
    def raw(self) -> Any:
        with self._lifecycle_lock:
            if self._connection is None:
                raise ConnectionError("MAVLink bağlantısı açık değil")
            return self._connection

    @property
    def target_system(self) -> int:
        return int(getattr(self.raw, "target_system", 0) or 1)

    @property
    def target_component(self) -> int:
        return int(getattr(self.raw, "target_component", 0) or 1)

    @property
    def message_state(self) -> dict[str, Any]:
        return getattr(self.raw, "messages", {})

    @property
    def mavlink(self) -> Any:
        """Yüklenen dialect'in MAVLink sabit ve temel mesaj sınıfları."""
        return self._load_mavutil().mavlink

    @property
    def sent_messages(self) -> int:
        with self._send_lock:
            return self._sent_messages

    def start(self) -> None:
        self.connect()

    def connect(self) -> None:
        with self._lifecycle_lock:
            if self._connection is not None:
                return
            connection: Any | None = None
            try:
                connection = self._create_connection()
                heartbeat = self._wait_vehicle_heartbeat(connection)
                if heartbeat is None:
                    raise TimeoutError("MAVLink heartbeat zaman aşımı")
            except Exception as exc:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception as close_error:
                        self._publish_error(close_error)
                self._publish_error(exc)
                raise
            self._connection = connection
        self.connection_changed.publish_isolated(True)

    def _wait_vehicle_heartbeat(self, connection: Any) -> Any:
        """Companion/GCS heartbeat'lerini bağlantı hedefi sanmadan otopilotu bekler."""
        deadline = time.monotonic() + self.endpoint.heartbeat_timeout
        classifier = getattr(connection, "probably_vehicle_heartbeat", None)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("MAVLink uçuş cihazı heartbeat zaman aşımı")
            heartbeat = connection.wait_heartbeat(timeout=remaining)
            if heartbeat is None:
                raise TimeoutError("MAVLink uçuş cihazı heartbeat zaman aşımı")
            if not callable(classifier) or classifier(heartbeat):
                return heartbeat

    def reconnect(self) -> None:
        self.stop()
        self.start()

    def receive(
        self,
        *,
        message_types: MessageTypeInput | None = None,
        condition: str | None = None,
        blocking: bool = False,
        timeout: float | None = None,
    ) -> Any | None:
        """pymavlink recv_match type/condition filtrelerini doğrudan sunar.

        Router çalışırken doğrudan çağrılmamalıdır; tek okuyucu router olmalıdır.
        """
        normalized_types = normalize_message_types(message_types)
        native_types: str | list[str] | None
        if normalized_types is None:
            native_types = None
        elif len(normalized_types) == 1:
            native_types = next(iter(normalized_types))
        else:
            native_types = sorted(normalized_types)
        normalized_condition = condition.strip() if condition else None
        kwargs: dict[str, Any] = {"blocking": blocking, "timeout": timeout}
        if native_types is not None:
            kwargs["type"] = native_types
        if normalized_condition:
            kwargs["condition"] = normalized_condition
        try:
            with self._receive_lock:
                return self.raw.recv_match(**kwargs)
        except Exception as exc:
            self._publish_error(exc)
            raise

    def evaluate_condition(self, condition: str) -> bool:
        normalized = condition.strip()
        if not normalized:
            raise ValueError("MAVLink condition boş olamaz")
        mavutil = self._load_mavutil()
        return bool(mavutil.evaluate_condition(normalized, self.message_state))

    def send(self, message: Any) -> None:
        self.call_mav("send", message)

    def send_named(self, message_name: str, **parameters: Any) -> None:
        self.call_mav(f"{message_name.strip().lower()}_send", **parameters)

    def call_mav(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Bütün pymavlink `raw.mav.*` yazmalarını aynı kilitten geçirir."""
        try:
            with self._send_lock:
                sender_name = method_name.strip()
                sender = getattr(self.raw.mav, sender_name, None)
                if not callable(sender):
                    raise ValueError(f"Bilinmeyen MAVLink gönderim metodu: {method_name}")
                result = sender(*args, **kwargs)
                self._sent_messages += 1
                return result
        except Exception as exc:
            self._publish_error(exc)
            raise

    def call_raw(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """set_mode gibi mav nesnesi dışındaki yazma işlemlerini serialize eder."""
        try:
            with self._send_lock:
                method = getattr(self.raw, method_name.strip(), None)
                if not callable(method):
                    raise ValueError(f"Bilinmeyen pymavlink metodu: {method_name}")
                result = method(*args, **kwargs)
                self._sent_messages += 1
                return result
        except Exception as exc:
            self._publish_error(exc)
            raise

    def stop(self) -> None:
        with self._send_lock, self._receive_lock, self._lifecycle_lock:
            connection = self._connection
            self._connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                self._publish_error(exc)
                raise
            self.connection_changed.publish_isolated(False)

    def _create_connection(self) -> Any:
        if self._connection_factory is not None:
            return self._connection_factory(self.endpoint)
        mavutil = self._load_mavutil()
        return mavutil.mavlink_connection(
            self.endpoint.uri,
            **self.endpoint.connection_kwargs(),
        )

    def _load_mavutil(self) -> Any:
        with self._lifecycle_lock:
            if self._mavutil_module is not None:
                return self._mavutil_module
            try:
                from pymavlink import mavutil
            except ImportError as exc:
                raise MavlinkUnavailableError(
                    "MAVLink kullanmak için pymavlink kurulmalı"
                ) from exc
            self._mavutil_module = mavutil
            return mavutil

    def _publish_error(self, error: Exception) -> None:
        self.errors.publish_isolated(error)

    close = stop

    def __enter__(self) -> "MavlinkConnection":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()
