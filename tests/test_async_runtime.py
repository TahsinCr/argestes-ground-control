from __future__ import annotations

import asyncio
import threading
import unittest

from src.services.async_runtime import AsyncRuntime


class AsyncRuntimeTests(unittest.TestCase):
    def test_coroutines_share_one_background_event_loop(self) -> None:
        runtime = AsyncRuntime()

        async def identify() -> tuple[int, str]:
            await asyncio.sleep(0)
            return id(asyncio.get_running_loop()), threading.current_thread().name

        first = runtime.submit(identify()).result(timeout=1.0)
        second = runtime.submit(identify()).result(timeout=1.0)

        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1], "YKIAsyncIO")
        self.assertEqual(second[1], "YKIAsyncIO")
        runtime.stop()
        self.assertFalse(runtime.running)

    def test_stop_cancels_pending_coroutines_and_can_restart(self) -> None:
        runtime = AsyncRuntime()

        async def wait_forever() -> None:
            await asyncio.Event().wait()

        pending = runtime.submit(wait_forever())
        runtime.stop()

        self.assertTrue(pending.cancelled() or pending.done())
        self.assertEqual(
            runtime.submit(asyncio.sleep(0, result="restarted")).result(timeout=1.0),
            "restarted",
        )
        runtime.stop()


if __name__ == "__main__":
    unittest.main()
