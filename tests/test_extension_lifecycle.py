from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.extension.host.host import ExtensionError, ExtensionHost
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


class _ExtensionSettings:
    def __init__(self) -> None:
        self.config = {"value": "new-invalid-value"}

    def update_extension(self, identifier: str, *, config: dict) -> None:
        self.config = dict(config)


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
        self.assertEqual(host._started_plugins, [first])
        self.assertTrue(host._lifecycle_started)
        self.assertEqual(len(logger.entries), 1)

    def test_unload_keeps_services_registered_when_plugin_stop_fails(self) -> None:
        plugin = _Plugin(RuntimeError("stop failed"))
        host = object.__new__(ExtensionHost)
        host._running = [("example", plugin, None)]
        host._active_ids = {"example"}
        host._started_plugins = [plugin]
        host._owned_tokens = {"example": ["service-token"]}
        host._visual_views = []
        host._status_widgets = []
        host._settings_contributions = {}
        host._log_lifecycle_error = lambda *_args: None
        host.container = SimpleNamespace(unregister=lambda _token: self.fail("unregistered"))

        with self.assertRaisesRegex(ExtensionError, "services remain registered"):
            host._unload_one("example")

        self.assertIn("example", host._active_ids)
        self.assertIn(plugin, host._started_plugins)
        self.assertEqual(host._owned_tokens["example"], ["service-token"])

    def test_failed_reload_restores_previous_config_and_live_extension(self) -> None:
        logger = _Logger()
        host = object.__new__(ExtensionHost)
        host.container = _Container(logger)
        host._active_ids = {"example"}
        host._stored = {"example": True}
        host._dirty_settings = {"example"}
        host._reload_baselines = {"example": {"value": "previous-working-value"}}
        host._settings_service = _ExtensionSettings()
        host.settings = lambda _identifier: dict(host._settings_service.config)
        host.can_reload = lambda identifier: True
        host._unload_one = lambda identifier: host._active_ids.discard(identifier)
        loads = 0

        def fail_to_load(identifier: str) -> None:
            nonlocal loads
            loads += 1
            if loads == 1:
                raise RuntimeError("invalid new settings")
            host._active_ids.add(identifier)

        host._load_one = fail_to_load

        with self.assertRaisesRegex(RuntimeError, "invalid new settings"):
            host.reload("example")

        self.assertTrue(host._stored["example"])
        self.assertEqual(host._settings_service.config, {"value": "previous-working-value"})
        self.assertIn("example", host._active_ids)
        self.assertNotIn("example", host._dirty_settings)
        self.assertNotIn("example", host._reload_baselines)
        self.assertEqual(len(logger.entries), 1)


if __name__ == "__main__":
    unittest.main()
