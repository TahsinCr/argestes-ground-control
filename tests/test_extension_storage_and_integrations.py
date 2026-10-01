from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.container import create_container
from src.extension.api.protocol import ActiveProtocol
from src.extension.host import ExtensionHost
from src.models.logs import LogEntry
from src.models.telemetry import TelemetrySnapshot
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService
from extensions.teknofest.runtime import CompetitionDatabase, ServerSession, validate_servers


class ExtensionStorageTests(unittest.TestCase):
    def test_migration_preserves_state_and_config_then_removes_old_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "data/extensions"
            (old / "camera").mkdir(parents=True)
            (old / "state.json").write_text('{"camera": false}')
            (old / "camera/settings.json").write_text('{"video_sources": [], "future_key": 9}')
            settings = SettingsService(root)
            self.assertFalse(old.exists())
            self.assertFalse(settings.current.extensions["camera"]["enabled"])
            self.assertEqual(settings.extension_config("camera")["future_key"], 9)
            stale = settings.current
            settings.update_extension("camera", config={"future_key": 10})
            settings.save(stale)
            self.assertEqual(settings.extension_config("camera")["future_key"], 10)
            self.assertEqual(json.loads((root / "data/settings.json").read_text())["extensions"]["camera"]["config"]["future_key"], 10)

    def test_concurrent_extension_updates_do_not_overwrite_other_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsService(Path(directory))
            threads = [threading.Thread(target=settings.update_extension,
                                        args=(identifier,), kwargs={"config": {"value": value}})
                       for value, identifier in enumerate(("camera", "map", "joystick", "teknofest"))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual([settings.extension_config(key)["value"] for key in
                              ("camera", "map", "joystick", "teknofest")], [0, 1, 2, 3])

    def test_shared_json_session_includes_injected_extension_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                service = container.resolve(LoggingService)
                service.for_extension("camera").child("VIDEO").info("frame")
                service.for_extension("joystick").emit_entry(LogEntry("RC", "input"))
                handler = service.create_python_handler()
                handler.emit(logging.LogRecord("extensions.teknofest.worker", logging.INFO,
                                               "/tmp/extensions/teknofest/worker.py", 1, "server", (), None))
                service.flush()
                rows = json.loads(service.log_path.read_text())
                self.assertTrue(service.log_path.name.startswith("gcs_"))
                self.assertEqual(service.log_path.suffix, ".json")
                self.assertEqual([row["extension_id"] for row in rows[-3:]],
                                 ["camera", "joystick", "teknofest"])
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
                self.assertFalse(host.is_active("teknofest"))
                self.assertTrue(host.is_active("map"))
                host.start()
                for identifier in ("joystick", "teknofest"):
                    host.set_enabled(identifier, True)
                    self.assertTrue(host.is_active(identifier))
                    host.set_enabled(identifier, False)
                    self.assertFalse(host.is_active(identifier))
                self.assertFalse((Path(directory) / "data/extensions").exists())
            finally:
                container.shutdown()


class CompetitionDeliveryTests(unittest.TestCase):
    def test_http_session_delivers_data_and_records_sql_without_credentials(self):
        delivered = threading.Event()
        bodies = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/giris":
                    response = {"takim_numarasi": 21}
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
                session.start()
                try:
                    self.assertTrue(delivered.wait(3))
                finally:
                    session.stop()
                self.assertEqual(bodies[0]["takim_numarasi"], 21)
                self.assertEqual(bodies[0]["iha_enlem"], 39.0)
                self.assertEqual(bodies[0]["gps_saati"]["saat"], 12)
                with sqlite3.connect(database.path) as connection:
                    rows = connection.execute("SELECT endpoint,request,response FROM exchanges").fetchall()
                self.assertEqual(rows[0][0], "/api/giris")
                self.assertEqual(json.loads(rows[0][1]), None)
                self.assertNotIn("not-in-sql", str(rows))
                self.assertIn("/api/telemetri_gonder", [row[0] for row in rows])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_invalid_server_config_is_rejected_before_persistence(self):
        with self.assertRaises(ValueError):
            validate_servers([{"identifier": "invalid", "base_url": "file:///etc/passwd"}])
