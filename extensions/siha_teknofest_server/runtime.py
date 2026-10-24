"""Independent server sessions, bounded HTTP work and durable SQL records."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

from src.core.events import EventBus
from src.extension.api import ClockSample

from .models import (
    CompetitionClockSample,
    CompetitionServerConfig,
    CompetitionServerTime,
)


class ServerSession:
    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def __init__(self, config, snapshot, database, logger):
        self.config = dict(config)
        self.snapshot = snapshot
        self.database = database
        self.logger = logger
        self.connected = False
        self.error = ""
        self.history_write_failures = 0
        self._stop = threading.Event()
        self._thread = None
        self._lifecycle_lock = threading.RLock()
        self._restart_when_stopped = False
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._request_lock = threading.Lock()
        self.clock = None
        self.last_snapshot = None
        self.reference = {}
        self.updated = EventBus[dict]()

    def start(self):
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stop.is_set():
                    self._restart_when_stopped = True
                return
            self._restart_when_stopped = False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_with_lifecycle,
                name="TeknofestServer",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout_s=None):
        with self._lifecycle_lock:
            self._restart_when_stopped = False
            self._stop.set()
            self.connected = False
            thread = self._thread
        if thread and thread is not threading.current_thread():
            timeout = (
                float(self.config.get("timeout_s", 3)) + 1
                if timeout_s is None
                else max(0.0, float(timeout_s))
            )
            thread.join(timeout=timeout)
        stopped = thread is None or not thread.is_alive() or thread is threading.current_thread()
        return stopped

    def _run_with_lifecycle(self):
        try:
            self._run()
        finally:
            with self._lifecycle_lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                restart = self._restart_when_stopped
                self._restart_when_stopped = False
                if restart:
                    self.start()

    def request(self, path, payload=None):
        with self._request_lock:
            return self._request(path, payload)

    def request_authenticated(self, path, payload=None):
        """Send a management event only through an active authenticated session."""
        with self._request_lock:
            if self._stop.is_set():
                raise RuntimeError("Competition server session is stopping")
            if not self.connected:
                raise RuntimeError("Competition server login is not complete")
            return self._request(path, payload)

    def _request(self, path, payload=None):
        base = str(self.config["base_url"]).rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError("HTTP(S) server address required")
        request = Request(
            base + path,
            data=None
            if payload is None
            else json.dumps(payload, allow_nan=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        started = time.monotonic()
        response, error = None, ""
        try:
            with self._opener.open(
                request, timeout=float(self.config.get("timeout_s", 3))
            ) as handle:
                raw = handle.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("Competition response exceeds limit")
                response = json.loads(raw) if raw else None
            return response
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            # Never persist credentials, including login request bodies.
            try:
                self.database.record(
                    self.config["identifier"],
                    path,
                    None if path == "/api/giris" else payload,
                    response,
                    error,
                    time.monotonic() - started,
                )
            except Exception as database_error:
                # History is best-effort: a local SQLite failure must not turn
                # a successful competition-server response into a request error.
                self.history_write_failures += 1
                if self.logger is not None:
                    try:
                        self.logger.error(
                            "Could not persist server exchange history "
                            f"(failure {self.history_write_failures}): {database_error}"
                        )
                    except Exception:
                        pass

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            deadline = time.monotonic() + 1.0
            try:
                if not self.connected:
                    login = self.request(
                        "/api/giris",
                        {
                            "kadi": self.config.get("username", ""),
                            "sifre": self.config.get("password", ""),
                        },
                    )
                    if self._stop.is_set():
                        return
                    self.config["team_number"] = parse_login_response(login)
                    self.connected = True
                    self.error = ""
                    self.logger.event(
                        "server.connected",
                        "Competition server connected",
                        details={"server": self.config["identifier"]},
                    )
                if self._stop.is_set():
                    return
                started = time.monotonic()
                clock = self.request("/api/sunucusaati")
                if self._stop.is_set():
                    return
                received = time.monotonic()
                received_at = datetime.now(UTC)
                server_time = CompetitionServerTime.from_payload(clock)
                self.clock = CompetitionClockSample(
                    server_time,
                    received,
                    started,
                    server_time.resolve_near(received_at),
                    received_at,
                )
                snapshot = self.snapshot()
                if snapshot.has_valid_position:
                    self.last_snapshot = snapshot
                # Preserve the established competition delivery policy: after
                # first valid data, keep sending last data during vehicle loss.
                if self.last_snapshot is not None:
                    payload = telemetry_payload(
                        self.last_snapshot,
                        self.config,
                        self.clock.estimate(time.monotonic()),
                    )
                    self.reference["telemetry"] = self.request(
                        "/api/telemetri_gonder", payload
                    )
                    if self._stop.is_set():
                        return
                    self.updated.publish(dict(self.reference))
                backoff = 1.0
                self._stop.wait(max(0.0, deadline - time.monotonic()))
            except Exception as exc:
                if self._stop.is_set():
                    return
                self.connected = False
                self.error = str(exc)
                self.logger.event(
                    "server.request_failed",
                    "Competition request failed",
                    severity="warning",
                    details={"server": self.config["identifier"], "error": self.error},
                )
                self._stop.wait(backoff)
                backoff = min(30.0, backoff * 2)


def parse_login_response(payload):
    """Require the documented team-number response before marking a session connected."""

    assigned = payload.get("takim_numarasi") if isinstance(payload, dict) else payload
    if (
        not isinstance(assigned, int)
        or isinstance(assigned, bool)
        or not 1 <= assigned <= 9999
    ):
        raise ValueError("Unexpected TEKNOFEST login response")
    return assigned


def telemetry_payload(snapshot, config, server_time=None):
    stamp = server_time or (
        snapshot.uav_time
        if isinstance(snapshot.uav_time, datetime)
        else snapshot.timestamp
    )
    return {
        "takim_numarasi": int(config.get("team_number", 1)),
        "iha_enlem": snapshot.latitude,
        "iha_boylam": snapshot.longitude,
        "iha_irtifa": snapshot.altitude_agl,
        "iha_dikilme": snapshot.pitch,
        "iha_yonelme": snapshot.heading,
        "iha_yatis": snapshot.roll,
        "iha_hizi": snapshot.air_speed,
        "iha_batarya": snapshot.battery_percent,
        "iha_otonom": int(snapshot.flight_mode.upper() in {"AUTO", "GUIDED"}),
        "iha_kilitlenme": 0,
        "hedef_merkez_X": 0,
        "hedef_merkez_Y": 0,
        "hedef_genislik": 0,
        "hedef_yukseklik": 0,
        "gps_saati": {
            "saat": stamp.hour,
            "dakika": stamp.minute,
            "saniye": stamp.second,
            "milisaniye": stamp.microsecond // 1000,
        },
    }


class CompetitionDatabase:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._write_lock = threading.RLock()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS exchanges (
                id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, server TEXT NOT NULL,
                endpoint TEXT NOT NULL, request TEXT, response TEXT, error TEXT,
                duration REAL NOT NULL)""")

    def record(self, server, path, payload, response, error, duration):
        with self._write_lock:
            with closing(sqlite3.connect(self.path, timeout=3)) as connection, connection:
                connection.execute(
                    "INSERT INTO exchanges VALUES(NULL,?,?,?,?,?,?,?)",
                    (
                        datetime.now(UTC).isoformat(),
                        server,
                        path,
                        json.dumps(payload, ensure_ascii=False),
                        json.dumps(response, ensure_ascii=False),
                        error,
                        duration,
                    ),
                )

    def storage_summary(self):
        with closing(sqlite3.connect(self.path, timeout=3)) as connection:
            count, oldest, newest = connection.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM exchanges"
            ).fetchone()
        files = (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
        size_bytes = 0
        for file_path in files:
            try:
                size_bytes += file_path.stat().st_size
            except OSError:
                continue
        return {
            "records": int(count),
            "size_bytes": size_bytes,
            "oldest": oldest,
            "newest": newest,
        }

    def export_jsonl(self, destination):
        destination = Path(destination).expanduser()
        if destination.is_symlink() or not destination.parent.is_dir():
            raise ValueError("Choose an existing folder and a regular export file")
        protected = {
            self.path.resolve(),
            Path(f"{self.path}-wal").resolve(),
            Path(f"{self.path}-shm").resolve(),
        }
        if destination.resolve() in protected:
            raise ValueError("The export destination must not replace the database")
        temporary_path = None
        count = 0
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                with closing(sqlite3.connect(self.path, timeout=3)) as connection:
                    cursor = connection.execute(
                        "SELECT id, timestamp, server, endpoint, request, response, error, duration "
                        "FROM exchanges ORDER BY id"
                    )
                    while rows := cursor.fetchmany(256):
                        for row in rows:
                            record = dict(
                                zip(
                                    ("id", "timestamp", "server", "endpoint", "request", "response", "error", "duration"),
                                    row,
                                )
                            )
                            for key in ("request", "response"):
                                if record[key] is not None:
                                    try:
                                        record[key] = json.loads(record[key])
                                    except (TypeError, ValueError):
                                        pass
                            output.write(
                                json.dumps(record, ensure_ascii=False, default=str) + "\n"
                            )
                            count += 1
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return count

    def clear_history(self):
        with self._write_lock:
            with closing(sqlite3.connect(self.path, timeout=3)) as connection:
                cursor = connection.execute("DELETE FROM exchanges")
                removed = cursor.rowcount
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM")
        return max(0, removed)


class CompetitionRuntime:
    def __init__(self, context):
        from src.services.abstracts.telemetry import TelemetryService
        from src.services.logging_service import LoggingService
        from src.services.settings import SettingsService

        self.settings = context.services.resolve(SettingsService)
        self.telemetry = context.services.resolve(TelemetryService)
        self.logger = context.services.resolve(LoggingService).child("TEKNOFEST")
        self.clock_references = getattr(context, "clock_references", None)
        self._clock_reference_ids: set[str] = set()
        self.database = CompetitionDatabase(
            context.base_dir / "data" / "siha_teknofest_history.sqlite3"
        )
        self.sessions = {}
        self._profile_generations = {}
        self.worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="CompetitionControl"
        )
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        self._load()

    def _load(self):
        raw_configs = self.settings.extension_config("siha_teknofest_server").get(
            "servers", []
        )
        configs = validate_servers(raw_configs)
        if len({item["identifier"] for item in configs}) != len(configs):
            raise ValueError("Duplicate competition server identity")
        for config in configs:
            timeout = float(config.get("timeout_s", 3))
            if not 0.1 <= timeout <= 15:
                raise ValueError("Server timeout must be between 0.1 and 15 seconds")
            self.sessions[config["identifier"]] = ServerSession(
                config,
                lambda: self.telemetry.current,
                self.database,
                self.logger,
            )
            self._profile_generations[config["identifier"]] = 1
        self._sync_clock_references()

    def _sync_clock_references(self):
        registry = getattr(self, "clock_references", None)
        if registry is None:
            return
        identifiers = set()
        for profile_id, session in self.sessions.items():
            stable_key = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()
            identifier = registry.publish(
                f"server.{stable_key}.utc",
                str(session.config.get("name") or profile_id),
                lambda current=session: self._server_clock_sample(current),
                stale_after_seconds=30.0,
            )
            identifiers.add(identifier)
        for identifier in self._clock_reference_ids - identifiers:
            registry.unregister(identifier)
        self._clock_reference_ids = identifiers

    @staticmethod
    def _server_time(session):
        sample = session.clock
        now = time.monotonic()
        if sample is None or now - sample.received_monotonic > 30.0:
            return None
        return sample.estimate(now)

    @staticmethod
    def _server_clock_sample(session):
        sample = session.clock
        if sample is None:
            return None
        return ClockSample(
            value=sample.estimate(time.monotonic()),
            updated_at=sample.received_at,
            estimated=True,
        )

    def start(self):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Competition extension stopped")
            for session in self.sessions.values():
                if session.config.get("auto_connect", False):
                    session.start()

    def connect(self, identifier):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Competition extension stopped")
            return self.worker.submit(self._start_session, identifier)

    def disconnect(self, identifier):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Competition extension stopped")
            return self.worker.submit(self._stop_session, identifier)

    def _start_session(self, identifier):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Competition extension stopped")
            session = self.sessions.get(identifier)
            if session is None:
                raise ValueError("Unknown TEKNOFEST server profile")
            session.start()

    def _stop_session(self, identifier):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Competition extension stopped")
            session = self.sessions.get(identifier)
            if session is None:
                raise ValueError("Unknown TEKNOFEST server profile")
            return session.stop()

    def configure(self, configs):
        configs = validate_servers(configs)

        def apply():
            existing = self.settings.extension_config(
                "siha_teknofest_server"
            ).get("servers", [])
            existing_by_id = {item.get("identifier"): item for item in existing}
            prepared = []
            for config in configs:
                profile_id = config["identifier"]
                password = config.get("password", "")
                if not password:
                    password = existing_by_id.get(profile_id, {}).get("password") or ""
                prepared.append({**config, "password": password})
            new_sessions = {
                config["identifier"]: ServerSession(
                    config,
                    lambda: self.telemetry.current,
                    self.database,
                    self.logger,
                )
                for config in prepared
            }
            previous_sessions = self.sessions
            was_running = {
                identifier: session.running
                for identifier, session in previous_sessions.items()
            }
            stopped = self._stop_sessions(previous_sessions)
            if not all(stopped.values()):
                with self._lifecycle_lock:
                    if not self._closed:
                        for identifier, session in previous_sessions.items():
                            if was_running.get(identifier):
                                session.start()
                raise RuntimeError(
                    "An existing TEKNOFEST session is still stopping; profiles were not changed"
                )
            with self._lifecycle_lock:
                if self._closed:
                    return
                try:
                    self.settings.update_extension(
                        "siha_teknofest_server", config={"servers": prepared}
                    )
                except Exception:
                    for identifier, session in previous_sessions.items():
                        if was_running.get(identifier):
                            session.start()
                    raise
                for identifier in previous_sessions.keys() | new_sessions.keys():
                    if previous_sessions.get(identifier) is not new_sessions.get(identifier):
                        self._profile_generations[identifier] = (
                            self._profile_generations.get(identifier, 0) + 1
                        )
                self.sessions = new_sessions
                self._sync_clock_references()
                self.start()

        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Competition extension stopped")
            return self.worker.submit(apply)

    def refresh_reference_data(self, identifier):
        def refresh():
            session = self.sessions[identifier]
            session.reference["qr"] = session.request("/api/qr_koordinati")
            session.reference["hss"] = session.request("/api/hss_koordinatlari")
            session.updated.publish(dict(session.reference))
            return dict(session.reference)

        return self.worker.submit(refresh)

    def _submit_profile_event(self, identifier, path, payload):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Competition extension stopped")
            if identifier not in self.sessions:
                raise ValueError("Unknown TEKNOFEST server profile")
            generation = self._profile_generations.get(identifier, 0)
            return self.worker.submit(
                self._request_current_session,
                identifier,
                generation,
                path,
                payload,
            )

    def _request_current_session(self, identifier, generation, path, payload):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Competition extension stopped")
            session = self.sessions.get(identifier)
            current_generation = self._profile_generations.get(identifier, 0)
        if session is None:
            raise ValueError("Unknown TEKNOFEST server profile")
        if current_generation != generation:
            raise RuntimeError(
                "TEKNOFEST server profile changed before the event was sent; "
                "submit the event again for the current profile"
            )
        return session.request_authenticated(path, payload)

    def submit_lock_event(self, identifier, event):
        return self._submit_profile_event(
            identifier,
            "/api/kilitlenme_bilgisi",
            event.to_payload(),
        )

    def submit_kamikaze_event(self, identifier, event):
        return self._submit_profile_event(
            identifier,
            "/api/kamikaze_bilgisi",
            event.to_payload(),
        )

    def stop(self):
        with self._lifecycle_lock:
            self._closed = True
            sessions = dict(self.sessions)
        # Active configuration work can be waiting for bounded session cleanup.
        # Cancel queued requests and let that worker finish without holding the
        # extension host or UI shutdown path open.
        self.worker.shutdown(wait=False, cancel_futures=True)
        stopped = self._stop_sessions(sessions)
        for identifier, did_stop in stopped.items():
            if not did_stop:
                self.logger.event(
                    "server.shutdown_timeout",
                    "TEKNOFEST session did not stop before its shutdown deadline",
                    severity="danger",
                    details={"server": identifier},
                )
        if self.clock_references is not None:
            self.clock_references.unregister_owner("siha_teknofest_server")
        self._clock_reference_ids.clear()

    @staticmethod
    def _stop_sessions(sessions):
        """Signal every session first, then join against one shared deadline."""
        items = tuple(sessions.items())
        for _identifier, session in items:
            session.stop(timeout_s=0)
        grace = max(
            (
                float(session.config.get("timeout_s", 3)) + 1.0
                for _identifier, session in items
            ),
            default=0.0,
        )
        deadline = time.monotonic() + grace
        return {
            identifier: session.stop(timeout_s=max(0.0, deadline - time.monotonic()))
            for identifier, session in items
        }


def validate_servers(configs):
    parsed = [
        CompetitionServerConfig(**dict(item)).to_dict(include_password=True)
        for item in configs
    ]
    if len({item["identifier"] for item in parsed}) != len(parsed):
        raise ValueError("Duplicate competition server identity")
    if any(item["timeout_s"] > 15 for item in parsed):
        raise ValueError("Server timeout must not exceed 15 seconds")
    return parsed
