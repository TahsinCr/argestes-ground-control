from __future__ import annotations

import unittest

from src.extension.host.host import ExtensionHost
from src.services.logging_service import LoggingService


class _Logger:
    def __init__(self) -> None:
        self.entries: list[tuple[object, ...]] = []

    def for_extension(self, identifier: str):
        return self

    def event(self, *args, **kwargs) -> None:
        self.entries.append((args, kwargs))


class _Container:
    def __init__(self, logger: _Logger) -> None:
        self.logger = logger

    def resolve(self, token):
        if token is not LoggingService:
            raise KeyError(token)
        return self.logger


class _Plugin:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True
        if self.error:
            raise self.error


class ExtensionLifecycleTests(unittest.TestCase):
    def test_stop_continues_after_a_plugin_stop_error(self) -> None:
        logger = _Logger()
        first = _Plugin(RuntimeError("stop failed"))
        second = _Plugin()
        host = object.__new__(ExtensionHost)
        host.container = _Container(logger)
        host._running = [("first", first, None), ("second", second, None)]
        host._started_plugins = [first, second]
        host._lifecycle_started = True

        host.stop()

        self.assertTrue(first.stopped)
        self.assertTrue(second.stopped)
        self.assertEqual(host._started_plugins, [])
        self.assertFalse(host._lifecycle_started)
        self.assertEqual(len(logger.entries), 1)

    def test_failed_reload_keeps_the_persisted_enabled_choice(self) -> None:
        logger = _Logger()
        host = object.__new__(ExtensionHost)
        host.container = _Container(logger)
        host._active_ids = {"example"}
        host._stored = {"example": True}
        host._dirty_settings = {"example"}
        host.can_reload = lambda identifier: True
        host._unload_one = lambda identifier: None

        def fail_to_load(identifier: str) -> None:
            raise RuntimeError("invalid new settings")

        host._load_one = fail_to_load

        with self.assertRaisesRegex(RuntimeError, "invalid new settings"):
            host.reload("example")

        self.assertTrue(host._stored["example"])
        self.assertIn("example", host._dirty_settings)
        self.assertEqual(len(logger.entries), 1)


if __name__ == "__main__":
    unittest.main()
