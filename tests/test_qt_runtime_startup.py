from __future__ import annotations

import unittest
import time

from src.ui.adapters.runtime import QtRuntimeServices


class _Logger:
    def __init__(self) -> None:
        self.events = []

    def event(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


class _Backend:
    def __init__(self, *, started: bool, failures: tuple[str, ...] = ()) -> None:
        self.started = started
        self.startup_failures = failures
        self.log_service = _Logger()
        self.facade = object()

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.started = False


class _Adapter:
    def close(self) -> None:
        return None


class QtRuntimeStartupTests(unittest.TestCase):
    def _start(self, backend: _Backend) -> QtRuntimeServices:
        runtime = QtRuntimeServices(backend, _Adapter(), _Adapter())
        runtime.start()
        deadline = time.monotonic() + 1.0
        while runtime.startup_state == "starting" and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertNotEqual(runtime.startup_state, "starting")
        return runtime

    def test_required_startup_failure_is_reported_and_retryable(self) -> None:
        runtime = self._start(_Backend(started=False, failures=("telemetry",)))
        self.assertEqual(runtime.startup_state, "failed")
        self.assertFalse(runtime._active)
        self.assertEqual(runtime.startup_message, "telemetry")
        self.assertTrue(runtime.backend.log_service.events)

    def test_optional_service_failure_is_reported_as_degraded(self) -> None:
        runtime = self._start(_Backend(started=True, failures=("camera",)))
        self.assertEqual(runtime.startup_state, "degraded")
        self.assertTrue(runtime._active)
        self.assertEqual(runtime.startup_message, "camera")

    def test_successful_startup_is_reported_ready(self) -> None:
        runtime = self._start(_Backend(started=True))
        self.assertEqual(runtime.startup_state, "ready")
        self.assertTrue(runtime._active)


if __name__ == "__main__":
    unittest.main()
