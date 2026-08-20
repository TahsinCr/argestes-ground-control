from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine
from typing import Any


class AsyncRuntime:
    """Own the single asyncio loop shared by backend I/O services."""

    _WAKEUP_PROBE_SECONDS = 0.05
    _RESTRICTED_RUNTIME_TICK_SECONDS = 0.05

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._wakeup_probe = threading.Event()
        self._restricted_runtime = False
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        thread = self._thread
        loop = self._loop
        return bool(thread and thread.is_alive() and loop and loop.is_running())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._ready.clear()
            self._wakeup_probe.clear()
            self._restricted_runtime = False
            self._thread = threading.Thread(
                target=self._run,
                name="YKIAsyncIO",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(2.0):
            raise RuntimeError("Async backend event loop could not start")
        loop = self._loop
        if loop is None:
            raise RuntimeError("Async backend event loop is unavailable")
        loop.call_soon_threadsafe(self._wakeup_probe.set)
        if not self._wakeup_probe.wait(self._WAKEUP_PROBE_SECONDS):
            # Some hardened sandboxes deny the event loop's AF_UNIX self-pipe.
            # The normal desktop path stays fully event-driven; only the
            # restricted fallback keeps a short timer armed to drain callbacks.
            self._restricted_runtime = True

    def submit(
        self,
        coroutine: Coroutine[Any, Any, Any],
    ) -> concurrent.futures.Future[Any]:
        self.start()
        loop = self._loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise RuntimeError("Async backend event loop is not running")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def stop(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
            if loop is None or thread is None:
                return
            loop.call_soon_threadsafe(loop.stop)
        if thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            if thread.is_alive():
                return
            self._loop = None
            self._thread = None
            self._ready.clear()
            self._wakeup_probe.clear()
            self._restricted_runtime = False

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
        loop.call_soon(self._mark_ready, loop)
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    def _mark_ready(self, loop: asyncio.AbstractEventLoop) -> None:
        # Keep one initial deadline in the selector while start() verifies that
        # the platform can wake the loop from another thread.
        loop.call_later(
            self._WAKEUP_PROBE_SECONDS * 2.0,
            self._enable_restricted_tick_if_needed,
            loop,
        )
        self._ready.set()

    def _enable_restricted_tick_if_needed(
        self,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        if not self._restricted_runtime or not loop.is_running():
            return
        loop.call_later(
            self._RESTRICTED_RUNTIME_TICK_SECONDS,
            self._enable_restricted_tick_if_needed,
            loop,
        )


__all__ = ["AsyncRuntime"]
