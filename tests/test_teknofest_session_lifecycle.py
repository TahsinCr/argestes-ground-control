from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from extensions.siha_teknofest_server.models import CompetitionServerConfig
from extensions.siha_teknofest_server.runtime import (
    CompetitionRuntime,
    ServerSession,
    parse_login_response,
)


class TeknofestSessionLifecycleTests(unittest.TestCase):
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

            def stop(self):
                return False

        runtime = object.__new__(CompetitionRuntime)
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

            def __init__(self, can_stop):
                self.can_stop = can_stop

            def stop(self):
                if self.can_stop:
                    self.running = False
                return self.can_stop

            def start(self):
                self.running = True

        runtime = object.__new__(CompetitionRuntime)
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
