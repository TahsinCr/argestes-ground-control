"""Independent server sessions, bounded HTTP work and durable SQL records."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener

from src.core.events import EventBus

from .models import (
    CompetitionClockSample,
    CompetitionServerConfig,
    CompetitionServerTime,
)
from .credentials import CredentialStoreError, ServerCredentialStore


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
        self._stop = threading.Event()
        self._thread = None
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._request_lock = threading.Lock()
        self.clock = None
        self.last_snapshot = None
        self.reference = {}
        self.updated = EventBus[dict]()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="TeknofestServer", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=float(self.config.get("timeout_s", 3)) + 1)
        self.connected = False

    def request(self, path, payload=None):
        with self._request_lock:
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
            self.database.record(
                self.config["identifier"],
                path,
                None if path == "/api/giris" else payload,
                response,
                error,
                time.monotonic() - started,
            )

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
                    assigned = (
                        login.get("takim_numarasi")
                        if isinstance(login, dict)
                        else login
                    )
                    if (
                        isinstance(assigned, int)
                        and not isinstance(assigned, bool)
                        and 1 <= assigned <= 9999
                    ):
                        self.config["team_number"] = assigned
                    self.connected = True
                    self.error = ""
                    self.logger.event(
                        "server.connected",
                        "Competition server connected",
                        details={"server": self.config["identifier"]},
                    )
                started = time.monotonic()
                clock = self.request("/api/sunucusaati")
                received = time.monotonic()
                server_time = CompetitionServerTime.from_payload(clock)
                self.clock = CompetitionClockSample(
                    server_time,
                    received,
                    started,
                    server_time.resolve_near(datetime.now(UTC)),
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
                    self.updated.publish(dict(self.reference))
                backoff = 1.0
                self._stop.wait(max(0.0, deadline - time.monotonic()))
            except Exception as exc:
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
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS exchanges (
                id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, server TEXT NOT NULL,
                endpoint TEXT NOT NULL, request TEXT, response TEXT, error TEXT,
                duration REAL NOT NULL)""")

    def record(self, server, path, payload, response, error, duration):
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


class CompetitionRuntime:
    def __init__(self, context, credential_store=None):
        from src.services.abstracts.telemetry import TelemetryService
        from src.services.logging_service import LoggingService
        from src.services.settings import SettingsService

        self.settings = context.services.resolve(SettingsService)
        self.telemetry = context.services.resolve(TelemetryService)
        self.logger = context.services.resolve(LoggingService).child("TEKNOFEST")
        self.credentials = (
            credential_store
            if credential_store is not None
            else ServerCredentialStore()
        )
        self.database = CompetitionDatabase(
            context.base_dir / "data" / "siha_teknofest_server" / "history.sqlite3"
        )
        self.sessions = {}
        self.worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="CompetitionControl"
        )
        self._closed = False
        self._load()

    def _load(self):
        raw_configs = self.settings.extension_config("siha_teknofest_server").get(
            "servers", []
        )
        configs = validate_servers(raw_configs)
        if len({item["identifier"] for item in configs}) != len(configs):
            raise ValueError("Duplicate competition server identity")
        persisted, needs_migration = [], False
        for config in configs:
            timeout = float(config.get("timeout_s", 3))
            if not 0.1 <= timeout <= 15:
                raise ValueError("Server timeout must be between 0.1 and 15 seconds")
            legacy_password = config.get("password", "")
            safe_config = {key: value for key, value in config.items() if key != "password"}
            password = ""
            if legacy_password:
                try:
                    self.credentials.set(config["identifier"], legacy_password)
                    password = legacy_password
                    needs_migration = True
                    persisted.append(safe_config)
                except CredentialStoreError:
                    # Keep an existing plaintext credential only when the OS
                    # store is unavailable, so a migration never breaks the
                    # current connection or silently destroys the only copy.
                    persisted.append(config)
                    self.logger.event(
                        "teknofest.credential_migration_pending",
                        "Sunucu parolası güvenli kasaya taşınamadı; eski ayar korunuyor",
                        severity="warning",
                    )
                    password = legacy_password
            else:
                persisted.append(safe_config)
                try:
                    password = self.credentials.get(config["identifier"]) or ""
                except CredentialStoreError:
                    self.logger.event(
                        "teknofest.credential_read_failed",
                        "Sunucu parolası işletim sistemi kasasından okunamadı",
                        severity="warning",
                    )
                    password = ""
            self.sessions[config["identifier"]] = ServerSession(
                {**safe_config, "password": password},
                lambda: self.telemetry.current,
                self.database,
                self.logger,
            )
        if needs_migration:
            try:
                self.settings.update_extension(
                    "siha_teknofest_server", config={"servers": persisted}
                )
            except Exception:
                self.logger.event(
                    "teknofest.credential_migration_save_failed",
                    "Güvenli kasa hazır ancak ayar dosyasındaki eski parola temizlenemedi",
                    severity="warning",
                )

    def start(self):
        for session in self.sessions.values():
            if session.config.get("auto_connect", False):
                session.start()

    def connect(self, identifier):
        self.sessions[identifier].start()

    def disconnect(self, identifier):
        return self.worker.submit(self.sessions[identifier].stop)

    def configure(self, configs):
        configs = validate_servers(configs)
        if self._closed:
            raise RuntimeError("Competition extension stopped")

        def apply():
            existing = self.settings.extension_config(
                "siha_teknofest_server"
            ).get("servers", [])
            existing_by_id = {item.get("identifier"): item for item in existing}
            persisted, prepared = [], []
            for config in configs:
                profile_id = config["identifier"]
                password = config.get("password", "")
                if not password:
                    password = existing_by_id.get(profile_id, {}).get("password") or ""
                if password:
                    self.credentials.set(profile_id, password)
                else:
                    try:
                        password = self.credentials.get(profile_id) or ""
                    except CredentialStoreError:
                        if profile_id in existing_by_id:
                            raise
                        password = ""
                safe_config = {key: value for key, value in config.items() if key != "password"}
                persisted.append(safe_config)
                prepared.append({**safe_config, "password": password})
            # Persist only after every secret has been stored and verified;
            # the running sessions remain untouched on any credential error.
            new_sessions = {
                config["identifier"]: ServerSession(
                    config,
                    lambda: self.telemetry.current,
                    self.database,
                    self.logger,
                )
                for config in prepared
            }
            self.settings.update_extension(
                "siha_teknofest_server", config={"servers": persisted}
            )
            previous_sessions = self.sessions
            for session in previous_sessions.values():
                session.stop()
            self.sessions = new_sessions
            self.start()
            retained = {item["identifier"] for item in prepared}
            for removed_id in set(existing_by_id) - retained:
                try:
                    self.credentials.delete(removed_id)
                except CredentialStoreError:
                    self.logger.event(
                        "teknofest.credential_cleanup_failed",
                        "Kaldırılan sunucunun parolası parola kasasından silinemedi",
                        severity="warning",
                    )

        return self.worker.submit(apply)

    def refresh_reference_data(self, identifier):
        def refresh():
            session = self.sessions[identifier]
            session.reference["qr"] = session.request("/api/qr_koordinati")
            session.reference["hss"] = session.request("/api/hss_koordinatlari")
            session.updated.publish(dict(session.reference))
            return dict(session.reference)

        return self.worker.submit(refresh)

    def submit_lock_event(self, identifier, event):
        return self.worker.submit(
            self.sessions[identifier].request,
            "/api/kilitlenme_bilgisi",
            event.to_payload(),
        )

    def submit_kamikaze_event(self, identifier, event):
        return self.worker.submit(
            self.sessions[identifier].request,
            "/api/kamikaze_bilgisi",
            event.to_payload(),
        )

    def stop(self):
        self._closed = True
        self.worker.shutdown(wait=True, cancel_futures=True)
        for session in self.sessions.values():
            session.stop()


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
