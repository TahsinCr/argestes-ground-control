from __future__ import annotations

import unittest
from concurrent.futures import Future
from types import SimpleNamespace

from extensions.siha_teknofest_server.credentials import (
    CredentialStoreError,
    ServerCredentialStore,
)
from extensions.siha_teknofest_server.models import CompetitionServerConfig
from extensions.siha_teknofest_server.runtime import CompetitionRuntime


class _MemoryKeyring:
    def __init__(self) -> None:
        self.priority = 1
        self.values: dict[tuple[str, str], str] = {}

    def get_keyring(self):
        return self

    def get_password(self, service: str, username: str):
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


class _Settings:
    def __init__(self, servers) -> None:
        self.config = {"servers": list(servers)}

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


def _runtime(settings, credential_store, logger):
    runtime = object.__new__(CompetitionRuntime)
    runtime.settings = settings
    runtime.telemetry = SimpleNamespace(current=None)
    runtime.database = object()
    runtime.logger = logger
    runtime.credentials = credential_store
    runtime.sessions = {}
    return runtime


class TeknofestCredentialTests(unittest.TestCase):
    def test_server_config_never_serializes_or_repr_exposes_password(self) -> None:
        config = CompetitionServerConfig(password="secret-value")

        self.assertNotIn("password", config.to_dict())
        self.assertNotIn("secret-value", repr(config))
        self.assertEqual(config.to_dict(include_password=True)["password"], "secret-value")

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

    def test_keyring_round_trip_and_unavailable_backend(self) -> None:
        backend = _MemoryKeyring()
        store = ServerCredentialStore(backend)
        store.set("profile-1", "secret-value")
        self.assertEqual(store.get("profile-1"), "secret-value")
        store.delete("profile-1")
        self.assertIsNone(store.get("profile-1"))

        backend.priority = 0
        with self.assertRaises(CredentialStoreError):
            store.set("profile-1", "secret-value")

    def test_legacy_password_migrates_and_is_removed_from_settings(self) -> None:
        logger = _Logger()
        keyring = _MemoryKeyring()
        store = ServerCredentialStore(keyring)
        settings = _Settings(
            [
                {
                    "identifier": "legacy-1",
                    "name": "Test",
                    "base_url": "http://127.0.0.1:5000",
                    "username": "pilot",
                    "password": "legacy-secret",
                }
            ]
        )
        runtime = _runtime(settings, store, logger)

        runtime._load()

        self.assertNotIn("password", settings.config["servers"][0])
        self.assertEqual(runtime.sessions["legacy-1"].config["password"], "legacy-secret")
        self.assertEqual(store.get("legacy-1"), "legacy-secret")
        self.assertFalse(any("legacy-secret" in repr(event) for event in logger.events))

    def test_unavailable_store_preserves_legacy_setting_for_existing_connection(self) -> None:
        logger = _Logger()
        settings = _Settings(
            [
                {
                    "identifier": "legacy-2",
                    "name": "Test",
                    "base_url": "http://127.0.0.1:5000",
                    "username": "pilot",
                    "password": "legacy-secret",
                }
            ]
        )

        class _UnavailableStore:
            def set(self, *_args) -> None:
                raise CredentialStoreError("not available")

            def get(self, *_args):
                raise CredentialStoreError("not available")

        runtime = _runtime(settings, _UnavailableStore(), logger)
        runtime._load()

        self.assertEqual(settings.config["servers"][0]["password"], "legacy-secret")
        self.assertEqual(runtime.sessions["legacy-2"].config["password"], "legacy-secret")
        self.assertTrue(logger.events)
        self.assertFalse(any("legacy-secret" in repr(event) for event in logger.events))

    def test_failed_credential_write_keeps_current_profiles_and_sessions(self) -> None:
        previous = {
            "identifier": "server-1",
            "name": "Existing",
            "base_url": "http://127.0.0.1:5000",
            "username": "pilot",
            "password": "saved-secret",
        }
        settings = _Settings([previous])
        logger = _Logger()

        class _UnavailableStore:
            def set(self, *_args) -> None:
                raise CredentialStoreError("not available")

            def get(self, *_args):
                raise CredentialStoreError("not available")

        runtime = _runtime(settings, _UnavailableStore(), logger)
        session = SimpleNamespace(stopped=False)
        session.stop = lambda: setattr(session, "stopped", True)
        runtime.sessions = {"server-1": session}
        runtime.worker = _InlineWorker()
        runtime._closed = False

        with self.assertRaises(CredentialStoreError):
            runtime.configure(
                [
                    {
                        **{key: value for key, value in previous.items() if key != "password"},
                        "password": "new-secret",
                    }
                ]
            ).result()

        self.assertEqual(settings.config["servers"], [previous])
        self.assertIs(runtime.sessions["server-1"], session)
        self.assertFalse(session.stopped)


if __name__ == "__main__":
    unittest.main()
