from __future__ import annotations

import threading
import unittest

from src.services.runtime import RuntimeServices


class _Service:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.started = False

    def start(self) -> None:
        self.started = True
        if self.error:
            raise self.error


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def event(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


class RuntimeStartupTests(unittest.TestCase):
    def test_required_telemetry_failure_cancels_later_startup(self) -> None:
        runtime = object.__new__(RuntimeServices)
        runtime._stop_requested = threading.Event()
        runtime.log_service = _Logger()
        runtime.async_runtime = _Service()
        runtime.telemetry_service = _Service(RuntimeError("telemetry failed"))
        runtime.extension_host = _Service()
        runtime.connection_service = _Service()

        runtime._start_services()

        self.assertTrue(runtime.async_runtime.started)
        self.assertTrue(runtime.telemetry_service.started)
        self.assertTrue(runtime._stop_requested.is_set())
        self.assertFalse(runtime.extension_host.started)
        self.assertFalse(runtime.connection_service.started)
        self.assertEqual(len(runtime.log_service.events), 1)

    def test_optional_connection_failure_does_not_cancel_core_runtime(self) -> None:
        runtime = object.__new__(RuntimeServices)
        runtime._stop_requested = threading.Event()
        runtime.log_service = _Logger()
        runtime.async_runtime = _Service()
        runtime.telemetry_service = _Service()
        runtime.extension_host = _Service()
        runtime.connection_service = _Service(RuntimeError("connection watcher failed"))

        runtime._start_services()

        self.assertFalse(runtime._stop_requested.is_set())
        self.assertTrue(runtime.extension_host.started)
        self.assertEqual(len(runtime.log_service.events), 1)


if __name__ == "__main__":
    unittest.main()
