from __future__ import annotations

import threading
import unittest
from concurrent.futures import Future
from types import SimpleNamespace

from extensions.siha_teknofest_server.models import CompetitionServerConfig
from extensions.siha_teknofest_server.runtime import CompetitionRuntime


class _Settings:
    def __init__(self, servers) -> None:
        self.config = {"servers": [dict(server) for server in servers]}

    def extension_config(self, _identifier: str):
        return self.config

    def update_extension(self, _identifier: str, *, config: dict) -> None:
        self.config.update(config)


class _Logger:
    def __init__(self) -> None:
        self.events = []

    def event(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


class _InlineWorker:
    def submit(self, callback) -> Future:
        future = Future()
        try:
            future.set_result(callback())
        except Exception as error:
            future.set_exception(error)
        return future


class _Session:
    def __init__(self, *, running: bool = False) -> None:
        self.config = {"timeout_s": 0.1}
        self.running = running
        self.stopped = False

    def stop(self, *, timeout_s=None) -> bool:
        self.stopped = True
        self.running = False
        return True

    def start(self) -> None:
        self.stopped = False
        self.running = True


def _runtime(settings, logger):
    runtime = object.__new__(CompetitionRuntime)
    runtime.settings = settings
    runtime.telemetry = SimpleNamespace(current=None)
    runtime.database = object()
    runtime.logger = logger
    runtime.sessions = {}
    runtime.worker = _InlineWorker()
    runtime._lifecycle_lock = threading.RLock()
    runtime._closed = False
    return runtime


def _server(*, identifier="server-1", password="", **changes):
    return {
        "identifier": identifier,
        "name": "Test server",
        "base_url": "http://127.0.0.1:5000",
        "username": "pilot",
        "password": password,
        "team_number": 21,
        "timeout_s": 3.0,
        "auto_connect": False,
        **changes,
    }


class TeknofestPasswordConfigTests(unittest.TestCase):
    def test_model_omits_password_by_default_and_repr_never_exposes_it(self) -> None:
        config = CompetitionServerConfig(password="test-secret")

        self.assertNotIn("password", config.to_dict())
        self.assertNotIn("test-secret", repr(config))
        self.assertEqual(config.to_dict(include_password=True)["password"], "test-secret")

    def test_url_scheme_is_validated_and_url_credentials_are_rejected(self) -> None:
        self.assertEqual(
            CompetitionServerConfig(base_url="https://localhost:9443/api").base_url,
            "https://localhost:9443/api",
        )
        with self.assertRaises(ValueError):
            CompetitionServerConfig(base_url="ftp://localhost:21")
        with self.assertRaises(ValueError):
            CompetitionServerConfig(base_url="https://user:pass@example.test")
        with self.assertRaises(ValueError):
            CompetitionServerConfig(base_url="https://example.test:invalid")

    def test_runtime_loads_password_directly_from_extension_settings(self) -> None:
        secret = "settings-secret"
        settings = _Settings([_server(password=secret)])
        runtime = _runtime(settings, _Logger())

        runtime._load()

        self.assertEqual(runtime.sessions["server-1"].config["password"], secret)
        self.assertEqual(settings.config["servers"][0]["password"], secret)

    def test_configuration_persists_password_and_blank_edit_keeps_existing_value(self) -> None:
        secret = "saved-secret"
        settings = _Settings([_server(password=secret)])
        logger = _Logger()
        runtime = _runtime(settings, logger)
        old_session = _Session()
        runtime.sessions = {"server-1": old_session}

        runtime.configure([_server(password="")]).result()

        self.assertEqual(settings.config["servers"][0]["password"], secret)
        self.assertEqual(runtime.sessions["server-1"].config["password"], secret)
        self.assertTrue(old_session.stopped)
        self.assertFalse(any(secret in repr(event) for event in logger.events))

    def test_new_password_is_written_to_settings_before_session_replacement(self) -> None:
        settings = _Settings([_server(password="old-secret")])
        runtime = _runtime(settings, _Logger())
        old_session = _Session()
        runtime.sessions = {"server-1": old_session}

        runtime.configure([_server(password="replacement-secret")]).result()

        self.assertEqual(
            settings.config["servers"][0]["password"], "replacement-secret"
        )
        self.assertEqual(
            runtime.sessions["server-1"].config["password"], "replacement-secret"
        )
        self.assertTrue(old_session.stopped)

    def test_settings_failure_keeps_existing_profiles_and_live_sessions(self) -> None:
        class _FailingSettings(_Settings):
            def update_extension(self, _identifier: str, *, config: dict) -> None:
                raise OSError("settings file is read-only")

        previous = _server(password="saved-secret")
        settings = _FailingSettings([previous])
        runtime = _runtime(settings, _Logger())
        old_session = _Session(running=True)
        runtime.sessions = {"server-1": old_session}

        with self.assertRaisesRegex(OSError, "read-only"):
            runtime.configure([_server(password="replacement-secret")]).result()

        self.assertEqual(settings.config["servers"], [previous])
        self.assertIs(runtime.sessions["server-1"], old_session)
        self.assertTrue(old_session.running)
        self.assertFalse(old_session.stopped)


if __name__ == "__main__":
    unittest.main()
