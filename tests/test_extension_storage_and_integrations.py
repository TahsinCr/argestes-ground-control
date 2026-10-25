from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from extensions.siha_teknofest_server.runtime import (
    CompetitionDatabase,
    CompetitionRuntime,
    ServerSession,
    validate_servers,
)
from extensions.siha_teknofest_server.models import CompetitionServerConfig
from src.container import create_container
from src.extension.api.protocol import ActiveProtocol
from src.extension.host import ExtensionHost
from src.models.logs import LogEntry
from src.models.telemetry import TelemetrySnapshot
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService


class ExtensionStorageTests(unittest.TestCase):
    def test_extension_settings_use_only_the_shared_current_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsService(root)
            settings.update_extension(
                "camera",
                enabled=False,
                config={"video_sources": [], "future_key": 9},
            )
            self.assertEqual(settings.extension_config("camera")["future_key"], 9)
            stale = settings.current
            settings.update_extension("camera", config={"future_key": 10})
            settings.save(stale)
            self.assertEqual(settings.extension_config("camera")["future_key"], 10)
            payload = json.loads((root / "data/settings.json").read_text())
            self.assertFalse(payload["extensions"]["camera"]["enabled"])
            self.assertEqual(payload["extensions"]["camera"]["config"]["future_key"], 10)
            self.assertFalse((root / "data/extensions").exists())

    def test_concurrent_extension_updates_do_not_overwrite_other_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            threads = [threading.Thread(target=settings.update_extension,
                                        args=(identifier,), kwargs={"config": {"value": value}})
                       for value, identifier in enumerate(("camera", "map_autonomy", "joystick", "siha_teknofest_server"))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual([settings.extension_config(key)["value"] for key in
                              ("camera", "map_autonomy", "joystick", "siha_teknofest_server")], [0, 1, 2, 3])

    def test_shared_json_session_includes_injected_extension_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                service = container.resolve(LoggingService)
                service.for_extension("camera").child("VIDEO").info("frame")
                service.for_extension("joystick").emit_entry(LogEntry("RC", "input"))
                handler = service.create_python_handler()
                handler.emit(logging.LogRecord("extensions.siha_teknofest_server.worker", logging.INFO,
                                               "/tmp/extensions/siha_teknofest_server/worker.py", 1, "server", (), None))
                service.flush()
                rows = json.loads(service.log_path.read_text())
                self.assertTrue(service.log_path.name.startswith("gcs_"))
                self.assertEqual(service.log_path.suffix, ".json")
                self.assertEqual([row["extension_id"] for row in rows[-3:]],
                                 ["camera", "joystick", "siha_teknofest_server"])
            finally:
                container.shutdown()

    def test_adapter_selection_rejects_unsupported_protocol(self):
        self.assertEqual(ActiveProtocol("mavlink").adapter({"mavlink": lambda: 7}), 7)
        with self.assertRaisesRegex(RuntimeError, "ros2"):
            ActiveProtocol("ros2").adapter({"mavlink": lambda: 7})

    def test_optional_plugins_remain_inactive_until_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                host = container.resolve(ExtensionHost)
                self.assertFalse(host.is_active("joystick"))
                self.assertFalse(host.is_active("siha_teknofest_server"))
                self.assertTrue(host.is_active("map_autonomy"))
                host.start()
                for identifier in ("joystick", "siha_teknofest_server"):
                    host.set_enabled(identifier, True)
                    self.assertTrue(host.is_active(identifier))
                    host.set_enabled(identifier, False)
                    self.assertFalse(host.is_active(identifier))
                self.assertFalse((Path(directory) / "data/extensions").exists())
            finally:
                container.shutdown()


class CompetitionDeliveryTests(unittest.TestCase):
    def test_server_timeout_accepts_the_configured_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsService(root)
            runtime = object.__new__(CompetitionRuntime)
            runtime._lifecycle_lock = threading.RLock()
            runtime._closed = False
            runtime.settings = settings
            runtime.telemetry = SimpleNamespace(current=None)
            runtime.database = None
            runtime.logger = None
            runtime.clock_references = None
            runtime._clock_reference_ids = set()
            runtime.sessions = {}
            runtime._profile_generations = {}
            runtime.worker = ThreadPoolExecutor(max_workers=1)
            try:
                for timeout in (15.0, 15.1, 30.0, 120.0):
                    with self.subTest(timeout=timeout):
                        config = CompetitionServerConfig(
                            identifier=f"timeout-{timeout}", timeout_s=timeout
                        ).to_dict(include_password=True)
                        runtime.configure([config]).result(timeout=2.0)
                        saved = SettingsService(root).extension_config(
                            "siha_teknofest_server"
                        )["servers"][0]
                        self.assertEqual(saved["timeout_s"], timeout)

                reloaded = object.__new__(CompetitionRuntime)
                reloaded.settings = SettingsService(root)
                reloaded.telemetry = SimpleNamespace(current=None)
                reloaded.database = None
                reloaded.logger = None
                reloaded.sessions = {}
                reloaded._profile_generations = {}
                reloaded.clock_references = None
                reloaded._clock_reference_ids = set()
                reloaded._load()
                self.assertEqual(
                    reloaded.sessions["timeout-120.0"].config["timeout_s"],
                    120.0,
                )
            finally:
                runtime.stop()
                runtime.worker.shutdown(wait=True, cancel_futures=True)

            with self.assertRaises(ValueError):
                validate_servers(
                    [
                        CompetitionServerConfig(
                            identifier="too-long", timeout_s=120.1
                        ).to_dict()
                    ]
                )

    def test_database_history_failure_does_not_mask_http_response(self):
        class Database:
            @staticmethod
            def record(*_args):
                raise OSError("database is read-only")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            session = ServerSession(
                {"identifier": "local", "base_url": f"http://127.0.0.1:{server.server_port}", "timeout_s": 1},
                lambda: TelemetrySnapshot.zero(),
                Database(),
                None,
            )
            self.assertEqual(session.request("/health"), {"ok": True})
            self.assertEqual(session.history_write_failures, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_competition_history_can_be_summarized_exported_and_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = CompetitionDatabase(root / "history.sqlite3")
            database.record("srv", "/api/giris", None, {"takim_numarasi": 7}, "", 0.1)
            database.record("srv", "/api/sunucusaati", None, {"saat": 12}, "", 0.2)

            summary = database.storage_summary()
            self.assertEqual(summary["records"], 2)
            self.assertGreater(summary["size_bytes"], 0)
            self.assertIsNotNone(summary["oldest"])
            self.assertIsNotNone(summary["newest"])
            with self.assertRaisesRegex(ValueError, "must not replace the database"):
                database.export_jsonl(database.path)
            self.assertEqual(database.storage_summary()["records"], 2)

            exported = root / "history.jsonl"
            self.assertEqual(database.export_jsonl(exported), 2)
            rows = [json.loads(line) for line in exported.read_text().splitlines()]
            self.assertEqual([row["endpoint"] for row in rows], ["/api/giris", "/api/sunucusaati"])
            self.assertIsNone(rows[0]["request"])

            self.assertEqual(database.clear_history(), 2)
            self.assertEqual(database.storage_summary()["records"], 0)

    def test_http_session_delivers_data_and_records_sql_without_credentials(self):
        delivered = threading.Event()
        bodies = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/giris":
                    response = {
                        "takim_numarasi": 21,
                        "access_token": "token-from-server",
                        "user": {
                            "display_name": "Pilot",
                            "refresh-token": "refresh-secret",
                        },
                    }
                else:
                    bodies.append(body)
                    delivered.set()
                    response = {"konumBilgileri": []}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

            def do_GET(self):
                now = datetime.now(UTC)
                body = {"gun": now.day, "saat": 12, "dakika": 34, "saniye": 56, "milisaniye": 123}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                database = CompetitionDatabase(Path(directory) / "history.sqlite3")
                snapshot = TelemetrySnapshot.zero()
                snapshot.latitude, snapshot.longitude = 39.0, 32.0
                snapshot.position_valid = True
                snapshot.telemetry_link = True
                config = {"identifier": "local", "base_url": f"http://127.0.0.1:{server.server_port}",
                          "username": "pilot", "password": "not-in-sql", "timeout_s": 1, "team_number": 21}
                session = ServerSession(config, lambda: snapshot, database, LoggingService().child("TEST"))
                login_response = session._request(
                    "/api/giris", {"sifre": "not-in-sql"}
                )
                self.assertEqual(login_response["access_token"], "token-from-server")
                session.start()
                try:
                    self.assertTrue(delivered.wait(3))
                finally:
                    session.stop()
                self.assertEqual(bodies[0]["takim_numarasi"], 21)
                self.assertEqual(bodies[0]["iha_enlem"], 39.0)
                self.assertEqual(bodies[0]["gps_saati"]["saat"], 12)
                with closing(sqlite3.connect(database.path)) as connection, connection:
                    rows = connection.execute("SELECT endpoint,request,response FROM exchanges").fetchall()
                self.assertEqual(rows[0][0], "/api/giris")
                self.assertEqual(json.loads(rows[0][1]), None)
                stored_login = json.loads(rows[0][2])
                self.assertEqual(stored_login["takim_numarasi"], 21)
                self.assertEqual(stored_login["user"]["display_name"], "Pilot")
                self.assertEqual(stored_login["access_token"], "[redacted]")
                self.assertEqual(stored_login["user"]["refresh-token"], "[redacted]")
                self.assertNotIn("not-in-sql", str(rows))
                self.assertNotIn("token-from-server", str(rows))
                self.assertNotIn("refresh-secret", str(rows))
                self.assertIn("/api/telemetri_gonder", [row[0] for row in rows])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_invalid_server_config_is_rejected_before_persistence(self):
        with self.assertRaises(ValueError):
            validate_servers([{"identifier": "invalid", "base_url": "file:///etc/passwd"}])
