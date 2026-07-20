from __future__ import annotations

import asyncio
import threading

from src.core.abstracts import Service
from src.core.events import Subscription
from src.core.mavlink.filter import MessageTypeInput, MavlinkMessageFilter
from src.core.mavlink.protocols import MavlinkMessage
from src.core.mavlink.router import MavlinkMessageRouter


class MavlinkAsyncChannel(Service):
    """Router thread'inden asyncio dünyasına bounded ve filtreli mesaj köprüsü."""

    def __init__(
        self,
        router: MavlinkMessageRouter,
        message_filter: MavlinkMessageFilter | MessageTypeInput | None = None,
        *,
        maxsize: int = 128,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if maxsize <= 0:
            raise ValueError("MAVLink async channel maxsize pozitif olmalı")
        self._router = router
        self._message_filter = message_filter
        self._queue: asyncio.Queue[MavlinkMessage] = asyncio.Queue(maxsize=maxsize)
        self._loop = loop
        self._subscription: Subscription | None = None
        self._dropped_messages = 0
        self._counter_lock = threading.Lock()

    @property
    def dropped_messages(self) -> int:
        with self._counter_lock:
            return self._dropped_messages

    @property
    def pending_messages(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._subscription is not None:
            return
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError as exc:
                raise RuntimeError(
                    "MavlinkAsyncChannel.start çalışan bir asyncio loop içinde çağrılmalı"
                ) from exc
            self._loop = loop
        self._subscription = self._router.subscribe(self._forward, self._message_filter)

    def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.cancel()
            self._subscription = None

    close = stop

    async def receive(self, *, timeout: float | None = None) -> MavlinkMessage:
        if timeout is None:
            return await self._queue.get()
        if timeout <= 0:
            raise ValueError("MAVLink async receive timeout pozitif olmalı")
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    def receive_nowait(self) -> MavlinkMessage:
        return self._queue.get_nowait()

    def _forward(self, message: MavlinkMessage) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._put_nowait, message)

    def _put_nowait(self, message: MavlinkMessage) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            else:
                with self._counter_lock:
                    self._dropped_messages += 1
        self._queue.put_nowait(message)
