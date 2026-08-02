from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventStream, Subscription
from src.core.mavlink.application import MavlinkApplicationPacket
from src.core.mavlink.peer import MavlinkApplicationPeer


@dataclass(frozen=True, slots=True)
class MavlinkApplicationResult:
    """Transport-neutral result returned by an application packet handler."""

    accepted: bool = True
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    response_type: str = ""

    def __post_init__(self) -> None:
        response_type = str(self.response_type).strip().lower()
        if not response_type:
            response_type = "system.ack" if self.accepted else "system.error"
        object.__setattr__(self, "accepted", bool(self.accepted))
        object.__setattr__(self, "message", str(self.message))
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "response_type", response_type)

    @classmethod
    def success(
        cls,
        payload: Mapping[str, Any] | None = None,
        *,
        message: str = "Command accepted",
    ) -> "MavlinkApplicationResult":
        return cls(True, message, dict(payload or {}))

    @classmethod
    def failure(
        cls,
        message: str,
        payload: Mapping[str, Any] | None = None,
    ) -> "MavlinkApplicationResult":
        return cls(False, message, dict(payload or {}))


@dataclass(frozen=True, slots=True)
class MavlinkApplicationDispatch:
    """Record a handled request and the result produced for it."""

    packet: MavlinkApplicationPacket
    result: MavlinkApplicationResult


MavlinkApplicationHandler = Callable[
    [MavlinkApplicationPacket],
    MavlinkApplicationResult | Mapping[str, Any] | None,
]


class MavlinkApplicationHandlerRegistry:
    """Keep packet handlers independent from a channel or peer lifecycle."""

    def __init__(self) -> None:
        self._handlers: dict[
            str,
            tuple[object, MavlinkApplicationHandler],
        ] = {}
        self._lock = threading.RLock()

    @property
    def packet_types(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._handlers))

    def register(
        self,
        packet_type: str,
        handler: MavlinkApplicationHandler,
        *,
        replace: bool = False,
    ) -> Subscription:
        normalized = _normalize_packet_type(packet_type)
        if normalized in MavlinkApplicationDispatcher.RESERVED_TYPES:
            raise ValueError(f"Reserved application packet type: {normalized}")
        if not callable(handler):
            raise TypeError("Application packet handler must be callable")
        token = object()
        with self._lock:
            if normalized in self._handlers and not replace:
                raise ValueError(
                    f"Application packet handler is already registered: {normalized}"
                )
            self._handlers[normalized] = (token, handler)

        def cancel() -> None:
            with self._lock:
                current = self._handlers.get(normalized)
                if current is not None and current[0] is token:
                    self._handlers.pop(normalized, None)

        return Subscription(cancel)

    def resolve(self, packet_type: str) -> MavlinkApplicationHandler | None:
        with self._lock:
            entry = self._handlers.get(_normalize_packet_type(packet_type))
            return entry[1] if entry is not None else None


class MavlinkApplicationDispatcher(Service):
    """Route application packets without blocking the MAVLink receive thread.

    Handlers are registered by packet type. Notifications are handled without
    a reply, while packets sent through ``MavlinkApplicationPeer.request``
    receive a correlated ``system.ack`` or ``system.error`` automatically.
    """

    RESERVED_TYPES = (
        MavlinkApplicationPeer.LIVENESS_TYPES
        | frozenset({"system.ack", "system.error"})
    )

    def __init__(
        self,
        peer: MavlinkApplicationPeer,
        *,
        workers: int = 1,
        max_pending: int = 64,
        thread_name: str = "MavlinkApplicationDispatch",
        handlers: MavlinkApplicationHandlerRegistry | None = None,
    ) -> None:
        if int(workers) <= 0:
            raise ValueError("Application dispatcher worker count must be positive")
        if int(max_pending) <= 0:
            raise ValueError("Application dispatcher queue limit must be positive")
        self._peer = peer
        self._workers = int(workers)
        self._thread_name = str(thread_name).strip() or "MavlinkApplicationDispatch"
        self._capacity = threading.BoundedSemaphore(int(max_pending))
        self._handlers = handlers or MavlinkApplicationHandlerRegistry()
        self._subscription: Subscription | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.RLock()

        self.handled = EventStream[MavlinkApplicationDispatch]()
        self.unhandled = EventStream[MavlinkApplicationPacket]()
        self.errors = EventStream[Exception]()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._subscription is not None

    @property
    def packet_types(self) -> tuple[str, ...]:
        return self._handlers.packet_types

    def register(
        self,
        packet_type: str,
        handler: MavlinkApplicationHandler,
        *,
        replace: bool = False,
    ) -> Subscription:
        return self._handlers.register(packet_type, handler, replace=replace)

    def start(self) -> None:
        with self._lock:
            if self._subscription is not None:
                return
            self._executor = ThreadPoolExecutor(
                max_workers=self._workers,
                thread_name_prefix=self._thread_name,
            )
            self._subscription = self._peer.packets.subscribe(self.dispatch)

    def stop(self) -> None:
        with self._lock:
            subscription = self._subscription
            self._subscription = None
            executor = self._executor
            self._executor = None
        if subscription is not None:
            subscription.cancel()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def dispatch(self, packet: MavlinkApplicationPacket) -> bool:
        """Queue one packet for its registered handler."""

        if packet.packet_type in self.RESERVED_TYPES:
            return False
        handler = self._handlers.resolve(packet.packet_type)
        with self._lock:
            executor = self._executor
        if handler is None:
            self.unhandled.publish_isolated(packet)
            if packet.expects_response:
                self._respond(
                    packet,
                    MavlinkApplicationResult.failure(
                        f"Unsupported application packet type: {packet.packet_type}",
                        {"error_type": "UnsupportedPacket"},
                    ),
                )
            return False
        if executor is None:
            error = RuntimeError("MAVLink application dispatcher is not running")
            self.errors.publish_isolated(error)
            if packet.expects_response:
                self._respond(
                    packet,
                    MavlinkApplicationResult.failure(
                        str(error),
                        {"error_type": error.__class__.__name__},
                    ),
                )
            return False
        if not self._capacity.acquire(blocking=False):
            error = RuntimeError("MAVLink application dispatcher queue is full")
            self.errors.publish_isolated(error)
            if packet.expects_response:
                self._respond(
                    packet,
                    MavlinkApplicationResult.failure(
                        str(error),
                        {"error_type": "DispatcherBusy"},
                    ),
                )
            return False
        try:
            future = executor.submit(self._execute, packet, handler)
        except Exception as exc:
            self._capacity.release()
            self.errors.publish_isolated(exc)
            if packet.expects_response:
                self._respond(
                    packet,
                    MavlinkApplicationResult.failure(
                        str(exc),
                        {"error_type": exc.__class__.__name__},
                    ),
                )
            return False
        future.add_done_callback(lambda _future: self._capacity.release())
        return True

    def _execute(
        self,
        packet: MavlinkApplicationPacket,
        handler: MavlinkApplicationHandler,
    ) -> None:
        try:
            returned = handler(packet)
            if isinstance(returned, MavlinkApplicationResult):
                result = returned
            elif returned is None:
                result = MavlinkApplicationResult.success()
            elif isinstance(returned, Mapping):
                result = MavlinkApplicationResult.success(returned)
            else:
                raise TypeError(
                    "Application packet handlers must return a result, mapping, or None"
                )
        except Exception as exc:
            self.errors.publish_isolated(exc)
            result = MavlinkApplicationResult.failure(
                str(exc),
                {"error_type": exc.__class__.__name__},
            )
        self.handled.publish_isolated(MavlinkApplicationDispatch(packet, result))
        if packet.expects_response:
            self._respond(packet, result)

    def _respond(
        self,
        request: MavlinkApplicationPacket,
        result: MavlinkApplicationResult,
    ) -> None:
        payload = dict(result.payload)
        payload.update(
            {
                "request_id": request.packet_id,
                "accepted": result.accepted,
                "message": result.message,
            }
        )
        try:
            self._peer.send(
                result.response_type,
                payload,
                target_system=int(request.source_system or 0),
                target_component=int(request.source_component or 0),
            )
        except Exception as exc:
            self.errors.publish_isolated(exc)

def _normalize_packet_type(packet_type: str) -> str:
    normalized = str(packet_type).strip().lower()
    if not normalized:
        raise ValueError("Application packet type cannot be empty")
    return normalized


__all__ = [
    "MavlinkApplicationDispatch",
    "MavlinkApplicationDispatcher",
    "MavlinkApplicationHandler",
    "MavlinkApplicationHandlerRegistry",
    "MavlinkApplicationResult",
]
