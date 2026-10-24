from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from extensions.siha_teknofest_server.models import CompetitionServerConfig
from extensions.siha_teknofest_server.runtime import (
    CompetitionRuntime,
    ServerSession,
    parse_login_response,
)


class TeknofestSessionLifecycleTests(unittest.TestCase):
    def test_stopped_runtime_rejects_a_new_server_connection(self) -> None:
        class Session:
            started = False

            def start(self) -> None:
                self.started = True

        session = Session()
        runtime = object.__new__(CompetitionRuntime)
        runtime._lifecycle_lock = threading.RLock()
        runtime._closed = True
        runtime.sessions = {"server": session}

        with self.assertRaisesRegex(RuntimeError, "stopped"):
            runtime.connect("server")

        self.assertFalse(session.started)

    def test_connection_requested_during_profile_replacement_uses_new_session(self) -> None:
        old_config = CompetitionServerConfig(identifier="old").to_dict(
            include_password=True
        )
        new_config = CompetitionServerConfig(identifier="new").to_dict(
            include_password=True
        )
        release_stop = threading.Event()
        stopping = threading.Event()

        class Settings:
            def __init__(self) -> None:
                self.servers = [old_config]

            def extension_config(self, _identifier):
                return {"servers": self.servers}

            def update_extension(self, _identifier, *, config):
                self.servers = config["servers"]

        class Session:
            def __init__(self, config, *, wait_to_stop=False):
                self.config = config
                self.running = wait_to_stop
                self.started = False
                self.wait_to_stop = wait_to_stop

            def start(self):
                self.started = True
                self.running = True

            def stop(self, *, timeout_s=None):
                if self.wait_to_stop and timeout_s == 0:
                    stopping.set()
                    return False
                if self.wait_to_stop and not release_stop.wait(1.0):
                    return False
                self.running = False
                return True

        previous = Session({"timeout_s": 0.1}, wait_to_stop=True)
        replacement = Session(new_config)
        runtime = object.__new__(CompetitionRuntime)
        runtime._lifecycle_lock = threading.RLock()
        runtime._closed = False
        runtime.settings = Settings()
        runtime.telemetry = SimpleNamespace(current=None)
        runtime.database = None
        runtime.logger = None
        runtime.clock_references = None
        runtime._clock_reference_ids = set()
        runtime.sessions = {"old": previous}
        runtime._profile_generations = {"old": 1}
        runtime.worker = ThreadPoolExecutor(max_workers=1)
        try:
            with patch(
                "extensions.siha_teknofest_server.runtime.ServerSession",
                return_value=replacement,
            ):
                configure = runtime.configure([new_config])
                self.assertTrue(stopping.wait(1.0))
                connect = runtime.connect("new")
                release_stop.set()
                configure.result(timeout=1.0)
                connect.result(timeout=1.0)

            self.assertTrue(replacement.started)
            self.assertEqual(set(runtime.sessions), {"new"})
        finally:
            release_stop.set()
            runtime.stop()

    def test_queued_events_resolve_the_replaced_profile_session_when_run(self) -> None:
        old_config = CompetitionServerConfig(identifier="server", name="Old").to_dict(
            include_password=True
        )
        new_config = CompetitionServerConfig(identifier="server", name="New").to_dict(
            include_password=True
        )
        entered = threading.Event()
        release = threading.Event()

        class Settings:
            def __init__(self) -> None:
                self.servers = [old_config]

            def extension_config(self, _identifier):
                return {"servers": self.servers}

            def update_extension(self, _identifier, *, config):
                self.servers = config["servers"]

        class Session:
            def __init__(self, config):
                self.config = config
                self.running = False
                self.requests = []

            def start(self):
                self.running = True

            def stop(self, *, timeout_s=None):
                self.running = False
                return True

            def request(self, path, payload):
                self.requests.append((path, payload))
                return {"accepted": True}

            def request_authenticated(self, path, payload):
                return self.request(path, payload)

        previous = Session(old_config)
        replacement = Session(new_config)
        runtime = object.__new__(CompetitionRuntime)
        runtime._lifecycle_lock = threading.RLock()
        runtime._closed = False
        runtime.settings = Settings()
        runtime.telemetry = SimpleNamespace(current=None)
        runtime.database = None
        runtime.logger = None
        runtime.clock_references = None
        runtime._clock_reference_ids = set()
        runtime.sessions = {"server": previous}
        runtime._profile_generations = {"server": 1}
        runtime.worker = ThreadPoolExecutor(max_workers=1)
        blocker = runtime.worker.submit(lambda: (entered.set(), release.wait(1.0)))
        try:
            self.assertTrue(entered.wait(1.0))
            with patch(
                "extensions.siha_teknofest_server.runtime.ServerSession",
                return_value=replacement,
            ):
                configure = runtime.configure([new_config])
                lock = runtime.submit_lock_event(
                    "server", SimpleNamespace(to_payload=lambda: {"event": "lock"})
                )
                kamikaze = runtime.submit_kamikaze_event(
                    "server", SimpleNamespace(to_payload=lambda: {"event": "kamikaze"})
                )
                release.set()
                configure.result(timeout=1.0)
                with self.assertRaisesRegex(RuntimeError, "profile changed"):
                    lock.result(timeout=1.0)
                with self.assertRaisesRegex(RuntimeError, "profile changed"):
                    kamikaze.result(timeout=1.0)
                current_event = runtime.submit_lock_event(
                    "server", SimpleNamespace(to_payload=lambda: {"event": "current"})
                )
                current_event.result(timeout=1.0)
            blocker.result(timeout=1.0)

            self.assertEqual(previous.requests, [])
            self.assertEqual(
                replacement.requests,
                [("/api/kilitlenme_bilgisi", {"event": "current"})],
            )
        finally:
            release.set()
            runtime.stop()

    def test_management_request_waits_for_authenticated_session(self) -> None:
        session = ServerSession(
            {
                "identifier": "server",
                "base_url": "http://127.0.0.1:1",
                "timeout_s": 0.1,
            },
            snapshot=lambda: None,
            database=None,
            logger=None,
        )
        requests = []
        session._request = lambda path, payload=None: requests.append((path, payload))

        with self.assertRaisesRegex(RuntimeError, "login is not complete"):
            session.request_authenticated("/api/kilitlenme_bilgisi", {"event": 1})
        self.assertEqual(requests, [])

        session.connected = True
        session._stop.set()
        with self.assertRaisesRegex(RuntimeError, "session is stopping"):
            session.request_authenticated("/api/kilitlenme_bilgisi", {"event": 2})
        self.assertEqual(requests, [])

        session._stop.clear()
        self.assertIsNone(
            session.request_authenticated("/api/kilitlenme_bilgisi", {"event": 3})
        )
        self.assertEqual(requests, [("/api/kilitlenme_bilgisi", {"event": 3})])

    def test_runtime_signals_all_sessions_before_waiting_for_any(self) -> None:
        sessions = []

        class FakeSession:
            def __init__(self) -> None:
                self.config = {"timeout_s": 0.1}
                self.signaled = False

            def stop(self, *, timeout_s=None) -> bool:
                if timeout_s == 0:
                    self.signaled = True
                    return False
                if not all(session.signaled for session in sessions):
                    raise AssertionError("session join started before stop fanout")
                return True

        sessions.extend((FakeSession(), FakeSession(), FakeSession()))
        stopped = CompetitionRuntime._stop_sessions(
            {str(index): session for index, session in enumerate(sessions)}
        )

        self.assertEqual(stopped, {"0": True, "1": True, "2": True})

    def test_runtime_shutdown_does_not_wait_for_management_executor(self) -> None:
        class Session:
            config = {"timeout_s": 0.1}

            def __init__(self) -> None:
                self.timeouts = []

            def stop(self, *, timeout_s=None) -> bool:
                self.timeouts.append(timeout_s)
                return True

        class Worker:
            def __init__(self) -> None:
                self.wait = None

            def shutdown(self, *, wait, cancel_futures) -> None:
                self.wait = wait
                self.cancel_futures = cancel_futures

        runtime = object.__new__(CompetitionRuntime)
        runtime._lifecycle_lock = threading.RLock()
        runtime._closed = False
        runtime.sessions = {"server": Session()}
        runtime.worker = Worker()
        runtime.logger = SimpleNamespace(event=lambda *args, **kwargs: None)
        runtime.clock_references = None
        runtime._clock_reference_ids = set()

        runtime.stop()

        self.assertFalse(runtime.worker.wait)
        self.assertTrue(runtime.worker.cancel_futures)
        self.assertTrue(runtime._closed)
        self.assertEqual(len(runtime.sessions["server"].timeouts), 2)

    def test_login_response_requires_a_valid_team_number(self) -> None:
        self.assertEqual(parse_login_response({"takim_numarasi": 21}), 21)
        self.assertEqual(parse_login_response(21), 21)
        for payload in ({}, {"takim_numarasi": True}, None, "ok", 0, 10000):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError, "Unexpected TEKNOFEST login response"
            ):
                parse_login_response(payload)

    def test_session_stop_reports_a_thread_that_exceeds_its_deadline(self) -> None:
        session = ServerSession(
            {"timeout_s": 0.1}, lambda: None, database=None, logger=None
        )
        release = threading.Event()
        thread = threading.Thread(target=release.wait)
        session._thread = thread
        thread.start()

        self.assertFalse(session.stop(timeout_s=0.01))
        release.set()
        thread.join(timeout=1.0)
        self.assertTrue(session.stop(timeout_s=0.1))

    def test_profile_replacement_aborts_if_old_session_is_still_stopping(self) -> None:
        old_config = CompetitionServerConfig(identifier="old").to_dict(
            include_password=True
        )
        new_config = CompetitionServerConfig(identifier="new").to_dict(
            include_password=True
        )

        class Settings:
            updated = False

            def extension_config(self, _identifier):
                return {"servers": [old_config]}

            def update_extension(self, *_args, **_kwargs):
                self.updated = True

        class Session:
            running = True
            config = {"timeout_s": 0.1}

            def stop(self, *, timeout_s=None):
                return False

        runtime = object.__new__(CompetitionRuntime)
        runtime._lifecycle_lock = threading.RLock()
        runtime._closed = False
        runtime.settings = Settings()
        runtime.telemetry = SimpleNamespace(current=None)
        runtime.database = None
        runtime.logger = None
        old_session = Session()
        runtime.sessions = {"old": old_session}
        runtime.worker = ThreadPoolExecutor(max_workers=1)
        try:
            future = runtime.configure([new_config])
            with self.assertRaisesRegex(RuntimeError, "still stopping"):
                future.result(timeout=1.0)
            self.assertIs(runtime.sessions["old"], old_session)
            self.assertFalse(runtime.settings.updated)
        finally:
            runtime.worker.shutdown(wait=True, cancel_futures=True)

    def test_partial_session_stop_failure_restarts_sessions_already_stopped(self) -> None:
        configs = [
            CompetitionServerConfig(identifier=identifier).to_dict(
                include_password=True
            )
            for identifier in ("old-a", "old-b")
        ]
        replacement = CompetitionServerConfig(identifier="new").to_dict(
            include_password=True
        )

        class Settings:
            updated = False

            def extension_config(self, _identifier):
                return {"servers": configs}

            def update_extension(self, *_args, **_kwargs):
                self.updated = True

        class Session:
            running = True
            config = {"timeout_s": 0.1}

            def __init__(self, can_stop):
                self.can_stop = can_stop

            def stop(self, *, timeout_s=None):
                if self.can_stop:
                    self.running = False
                return self.can_stop

            def start(self):
                self.running = True

        runtime = object.__new__(CompetitionRuntime)
        runtime._lifecycle_lock = threading.RLock()
        runtime._closed = False
        runtime.settings = Settings()
        runtime.telemetry = SimpleNamespace(current=None)
        runtime.database = None
        runtime.logger = None
        stopped_session = Session(can_stop=True)
        pending_session = Session(can_stop=False)
        runtime.sessions = {"old-a": stopped_session, "old-b": pending_session}
        runtime.worker = ThreadPoolExecutor(max_workers=1)
        try:
            with self.assertRaisesRegex(RuntimeError, "still stopping"):
                runtime.configure([replacement]).result(timeout=1.0)

            self.assertTrue(stopped_session.running)
            self.assertTrue(pending_session.running)
            self.assertFalse(runtime.settings.updated)
            self.assertEqual(set(runtime.sessions), {"old-a", "old-b"})
        finally:
            runtime.worker.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
