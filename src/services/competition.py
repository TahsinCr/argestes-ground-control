from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import http.cookiejar
import json
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from src.core.events import EventStream
from src.models.competition import (
    CompetitionConnectionState,
    CompetitionServerConfig,
    CompetitorTelemetry,
    HssZone,
    KamikazeEvent,
    LockEvent,
    QrCoordinate,
)
from src.models.telemetry import TelemetrySnapshot
from src.services.contracts.competition import CompetitionService
from src.services.contracts.mission import MissionService
from src.services.contracts.telemetry import TelemetryService
from src.services.contracts.vehicle import VehicleService
from src.services.logging_service import ServiceLogger


class CompetitionProtocolError(RuntimeError):
    pass


class TeknofestCompetitionService(CompetitionService):
    """2026 TEKNOFEST HTTP protokolünü merkezi backend durumuna bağlar."""

    REFERENCE_REFRESH_PERIOD_S = 5.0

    def __init__(
        self,
        config: CompetitionServerConfig,
        telemetry: TelemetryService,
        mission: MissionService,
        vehicle: VehicleService,
        logger: ServiceLogger | None = None,
        *,
        telemetry_freshness_timeout_s: float | None = None,
    ) -> None:
        if telemetry_freshness_timeout_s is not None and telemetry_freshness_timeout_s <= 0:
            raise ValueError("Telemetri tazelik zaman aşımı pozitif olmalı")
        self._config = config
        self._telemetry = telemetry
        self._mission = mission
        self._vehicle = vehicle
        self._logger = logger
        self._telemetry_freshness_timeout_s = telemetry_freshness_timeout_s
        self._state = CompetitionConnectionState(
            connected=False,
            authenticated=False,
            running=False,
            endpoint=config.base_url,
            team_number=config.team_number,
        )
        self._competitors: tuple[CompetitorTelemetry, ...] = ()
        self._hss_zones: tuple[HssZone, ...] = ()
        self._qr_coordinate: QrCoordinate | None = None
        self._changed = EventStream[CompetitionConnectionState]()
        self._competitors_updated = EventStream[tuple[CompetitorTelemetry, ...]]()
        self._hss_updated = EventStream[tuple[HssZone, ...]]()
        self._qr_updated = EventStream[QrCoordinate | None]()
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._http_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._opener = self._new_opener()
        self._last_reference_refresh = 0.0

    @property
    def state(self) -> CompetitionConnectionState:
        with self._lock:
            return replace(self._state)

    @property
    def changed(self) -> EventStream[CompetitionConnectionState]:
        return self._changed

    @property
    def competitors_updated(self) -> EventStream[tuple[CompetitorTelemetry, ...]]:
        return self._competitors_updated

    @property
    def competitors(self) -> tuple[CompetitorTelemetry, ...]:
        with self._lock:
            return tuple(self._competitors)

    @property
    def hss_updated(self) -> EventStream[tuple[HssZone, ...]]:
        return self._hss_updated

    @property
    def qr_updated(self) -> EventStream[QrCoordinate | None]:
        return self._qr_updated

    @property
    def hss_zones(self) -> tuple[HssZone, ...]:
        with self._lock:
            return tuple(self._hss_zones)

    @property
    def qr_coordinate(self) -> QrCoordinate | None:
        with self._lock:
            return self._qr_coordinate

    @property
    def config(self) -> CompetitionServerConfig:
        with self._lock:
            return self._config

    def configure(self, config: CompetitionServerConfig) -> None:
        with self._lock:
            self._config = config
            self._state = replace(
                self._state,
                endpoint=config.base_url,
                team_number=config.team_number,
            )

    def start(self) -> None:
        if self.config.auto_connect:
            self._launch()
        else:
            self._publish_state(running=False)

    def reconnect(self, config: CompetitionServerConfig | None = None) -> None:
        with self._lifecycle_lock:
            self.disconnect()
            if config is not None:
                self.configure(config)
            self._launch()

    def disconnect(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=min(self.config.timeout_s + 0.5, 5.0))
            self._thread = None
            self._opener = self._new_opener()
            self._telemetry.patch(server_link=False, server_time=None)
            self._mission.replace_contacts((), source="teknofest")
            self._mission.replace_source_overlays("teknofest-hss", ())
            self._mission.replace_source_overlays("teknofest-qr", ())
            with self._lock:
                had_hss = bool(self._hss_zones)
                had_qr = self._qr_coordinate is not None
                self._competitors = ()
                self._hss_zones = ()
                self._qr_coordinate = None
                self._last_reference_refresh = 0.0
            if had_hss:
                self._hss_updated.publish_isolated(())
            if had_qr:
                self._qr_updated.publish_isolated(None)
            self._publish_state(
                connected=False,
                authenticated=False,
                running=False,
                competitor_count=0,
                hss_count=0,
                qr_available=False,
                telemetry_tx_active=False,
                last_error="",
            )

    def stop(self) -> None:
        self.disconnect()

    def _launch(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._opener = self._new_opener()
        self._publish_state(running=True, last_error="")
        self._thread = threading.Thread(
            target=self._run,
            name="TeknofestCompetition",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        announced_waiting_for_vehicle_data = False
        while not self._stop_event.is_set():
            try:
                self._login()
                self._log_protocol(
                    "competition.server_time_request_tx",
                    "TX TEKNOFEST sunucu saati isteği gönderiliyor",
                    {},
                    severity="debug",
                )
                server_payload = self._request_json("GET", "/api/sunucusaati")
                server_time = self._server_time(server_payload)
                self._log_protocol(
                    "competition.server_time_rx",
                    "RX TEKNOFEST sunucu saati alındı",
                    {"server_time": server_time.isoformat() if server_time else None},
                    severity="debug",
                )
                self._telemetry.patch(server_link=True, server_time=server_time)
                self._publish_state(
                    connected=True,
                    authenticated=True,
                    running=True,
                    telemetry_tx_active=False,
                    last_error="",
                    last_success_at=datetime.now(UTC),
                )
                if self._logger:
                    self._logger.event(
                        "competition.connected",
                        "TEKNOFEST yarışma sunucusuna bağlanıldı",
                        severity="success",
                        details={"endpoint": self.config.base_url, "team_number": self.config.team_number},
                    )
                self._refresh_reference_data_if_due(force=True)
                first_telemetry_sent = False

                while not self._stop_event.is_set():
                    started = time.monotonic()
                    snapshot = self._telemetry.current
                    vehicle_time = self._vehicle_time(snapshot)
                    source_timeout_s = self._source_timeout_s()
                    vehicle_connected = self._vehicle.connected
                    feed_ready = (
                        vehicle_connected
                        and snapshot.has_fresh_vehicle_telemetry(source_timeout_s)
                    )
                    if feed_ready and vehicle_time is not None:
                        outbound = self._telemetry_payload(snapshot, vehicle_time)
                        self._log_telemetry_tx(outbound)
                        response = self._request_json(
                            "POST",
                            "/api/telemetri_gonder",
                            outbound,
                        )
                        self._consume_telemetry_response(response)
                        if not first_telemetry_sent:
                            first_telemetry_sent = True
                            self._refresh_reference_data_if_due(force=True)
                        announced_waiting_for_vehicle_data = False
                    elif not announced_waiting_for_vehicle_data:
                        announced_waiting_for_vehicle_data = True
                        if self._logger:
                            self._logger.event(
                                "competition.telemetry_blocked",
                                "TEKNOFEST telemetri TX durduruldu; canlı SİHA telemetri hattı bekleniyor",
                                severity="warning",
                                details={
                                    "reason": self._feed_block_reason(snapshot, source_timeout_s),
                                    "vehicle_connected": vehicle_connected,
                                    "source_timeout_s": source_timeout_s,
                                },
                            )
                    if not feed_ready or vehicle_time is None:
                        if self.state.telemetry_tx_active:
                            self._publish_state(telemetry_tx_active=False)
                        self._log_protocol(
                            "competition.server_time_request_tx",
                            "TX TEKNOFEST bağlantı sağlığı/saat isteği gönderiliyor",
                            {},
                            severity="debug",
                        )
                        health = self._request_json("GET", "/api/sunucusaati")
                        health_time = self._server_time(health)
                        self._telemetry.patch(
                            server_link=True,
                            server_time=health_time,
                        )
                        self._log_protocol(
                            "competition.server_time_rx",
                            "RX TEKNOFEST sunucu saati/bağlantı sağlığı alındı",
                            {"server_time": health_time.isoformat() if health_time else None},
                            severity="debug",
                        )
                        self._publish_state(
                            connected=True,
                            authenticated=True,
                            running=True,
                            last_error="",
                            last_success_at=datetime.now(UTC),
                        )
                    self._refresh_reference_data_if_due()
                    period_s = self.config.telemetry_period_ms / 1000.0
                    self._stop_event.wait(max(0.0, period_s - (time.monotonic() - started)))
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                message = str(exc) or exc.__class__.__name__
                self._telemetry.patch(server_link=False, server_time=None)
                self._publish_state(
                    connected=False,
                    authenticated=False,
                    running=True,
                    telemetry_tx_active=False,
                    last_error=message,
                )
                if self._logger:
                    self._logger.event(
                        "competition.connection_failed",
                        f"TEKNOFEST sunucu bağlantısı kesildi: {message}",
                        severity="danger",
                        details={"endpoint": self.config.base_url},
                    )
                self._opener = self._new_opener()
                self._stop_event.wait(2.0)

    def refresh_reference_data(self) -> None:
        if not self.state.connected:
            raise ConnectionError("TEKNOFEST sunucu oturumu açık değil")
        self._refresh_reference_data_if_due(force=True)

    def submit_lock_event(self, event: LockEvent) -> None:
        self._require_connected()
        payload = event.to_payload()
        self._log_protocol(
            "competition.lock_tx",
            "TX SİHA kilitlenme olayı TEKNOFEST sunucusuna gönderiliyor",
            {"vehicle_source": event.vehicle_source, "autonomous": event.autonomous},
        )
        self._request_json("POST", "/api/kilitlenme_bilgisi", payload)
        self._log_protocol(
            "competition.lock_ack",
            "RX TEKNOFEST kilitlenme kayıt onayı alındı",
            {"vehicle_source": event.vehicle_source},
            severity="success",
        )

    def submit_kamikaze_event(self, event: KamikazeEvent) -> None:
        self._require_connected()
        payload = event.to_payload()
        self._log_protocol(
            "competition.kamikaze_tx",
            "TX SİHA kamikaze olayı TEKNOFEST sunucusuna gönderiliyor",
            {"vehicle_source": event.vehicle_source, "qr_text_length": len(event.qr_text)},
        )
        self._request_json("POST", "/api/kamikaze_bilgisi", payload)
        self._log_protocol(
            "competition.kamikaze_ack",
            "RX TEKNOFEST kamikaze kayıt onayı alındı",
            {"vehicle_source": event.vehicle_source},
            severity="success",
        )

    def _login(self) -> None:
        self._log_protocol(
            "competition.login_tx",
            "TX TEKNOFEST oturum açma isteği gönderiliyor",
            {"username": self.config.username, "team_number": self.config.team_number},
            severity="debug",
        )
        response = self._request_json(
            "POST",
            "/api/giris",
            {"kadi": self.config.username, "sifre": self.config.password},
        )
        if isinstance(response, dict):
            returned_team = int(response.get("takim_numarasi", self.config.team_number))
        elif isinstance(response, (int, float, str)):
            returned_team = int(response)
        else:
            raise CompetitionProtocolError("Sunucu geçersiz giriş yanıtı döndürdü")
        if returned_team != self.config.team_number:
            raise CompetitionProtocolError(
                f"Sunucunun takım numarası {returned_team}, yapılandırılan {self.config.team_number}"
            )
        self._log_protocol(
            "competition.login_rx",
            "RX TEKNOFEST oturum onayı alındı",
            {"team_number": returned_team},
            severity="debug",
        )

    def _consume_telemetry_response(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise CompetitionProtocolError("Telemetri yanıtı JSON nesnesi değil")
        received_at = datetime.now(UTC)
        raw_contacts = payload.get("konumBilgileri", [])
        if not isinstance(raw_contacts, list):
            raise CompetitionProtocolError("konumBilgileri liste olmalı")
        contacts: list[CompetitorTelemetry] = []
        for item in raw_contacts:
            if not isinstance(item, dict):
                continue
            try:
                contact = CompetitorTelemetry.from_payload(item, received_at)
            except (KeyError, TypeError, ValueError):
                continue
            if contact.team_number != self.config.team_number:
                contacts.append(contact)
        contacts.sort(key=lambda item: item.team_number)
        result = tuple(contacts)
        ages = [contact.data_age_ms for contact in result]
        with self._lock:
            previous_count = len(self._competitors)
            self._competitors = result
        self._mission.replace_contacts(
            (contact.to_map_contact() for contact in result),
            source="teknofest",
        )
        server_time = self._server_time(payload.get("sunucusaati", {}))
        self._telemetry.patch(server_link=True, server_time=server_time)
        self._competitors_updated.publish_isolated(result)
        self._publish_state(
            connected=True,
            authenticated=True,
            running=True,
            telemetry_tx_active=True,
            competitor_count=len(result),
            last_error="",
            last_success_at=received_at,
        )
        self._log_protocol(
            "competition.telemetry_rx",
            f"RX TEKNOFEST rakip telemetrisi alındı ({len(result)} İHA)",
            {
                "competitor_count": len(result),
                "data_age_min_ms": min(ages, default=0),
                "data_age_max_ms": max(ages, default=0),
            },
            severity="debug",
        )
        if previous_count != len(result) and self._logger:
            self._logger.event(
                "competition.contacts_updated",
                f"TEKNOFEST sunucusundan {len(result)} rakip İHA alındı",
                severity="success",
                details={"competitor_count": len(result)},
            )

    def _telemetry_payload(
        self,
        snapshot: TelemetrySnapshot,
        vehicle_time: datetime,
    ) -> dict[str, Any]:
        timestamp = vehicle_time.astimezone(UTC)
        track = snapshot.target_track
        autonomous = snapshot.flight_mode.upper() not in {
            "MANUAL",
            "STABILIZE",
            "ACRO",
            "DISCONNECTED",
        }
        return {
            "takim_numarasi": self.config.team_number,
            "iha_enlem": round(snapshot.latitude, 7),
            "iha_boylam": round(snapshot.longitude, 7),
            # Pixhawk RELATIVE_ALT yerdeyken barometre gurultusu nedeniyle
            # birkac santimetre negatif olabilir. Yarışma protokolü bu alanı
            # 0 veya daha buyuk kabul ettigi icin yalnizca alt siniri normalize et.
            "iha_irtifa": max(0.0, round(snapshot.altitude_agl, 2)),
            "iha_dikilme": round(snapshot.pitch, 2),
            "iha_yonelme": round(snapshot.heading % 360.0, 2),
            "iha_yatis": round(snapshot.roll, 2),
            "iha_hiz": max(0.0, round(snapshot.ground_speed, 2)),
            "iha_batarya": max(0, min(100, int(snapshot.battery_percent))),
            "iha_otonom": int(autonomous),
            "iha_kilitlenme": int(track.locked),
            "hedef_merkez_X": max(0, int(track.center_x)),
            "hedef_merkez_Y": max(0, int(track.center_y)),
            "hedef_genislik": max(0, int(track.width)),
            "hedef_yukseklik": max(0, int(track.height)),
            "gps_saati": {
                "saat": timestamp.hour,
                "dakika": timestamp.minute,
                "saniye": timestamp.second,
                "milisaniye": timestamp.microsecond // 1000,
            },
        }

    @staticmethod
    def _vehicle_time(snapshot: TelemetrySnapshot) -> datetime | None:
        value = snapshot.uav_time
        if not isinstance(value, datetime) or value.tzinfo is None:
            return None
        return value.astimezone(UTC)

    def _source_timeout_s(self) -> float:
        if self._telemetry_freshness_timeout_s is not None:
            return self._telemetry_freshness_timeout_s
        return max(2.0, self.config.telemetry_period_ms / 1000.0 * 3.0)

    def _feed_block_reason(self, snapshot: TelemetrySnapshot, max_age_s: float) -> str:
        if not self._vehicle.connected:
            return "vehicle_connection_down"
        if not snapshot.telemetry_link or not snapshot.autopilot_link:
            return "telemetry_link_down"
        if not snapshot.has_valid_position:
            return "position_missing"
        if not isinstance(snapshot.uav_time, datetime) or snapshot.uav_time.tzinfo is None:
            return "vehicle_gps_time_missing"
        now = datetime.now(UTC)
        if snapshot.position_received_at is None or snapshot.position_received_at.tzinfo is None:
            return "position_timestamp_missing"
        position_age_s = (now - snapshot.position_received_at.astimezone(UTC)).total_seconds()
        if position_age_s < 0:
            return "position_timestamp_invalid"
        if position_age_s > max_age_s:
            return "position_stale"
        if snapshot.uav_time_received_at is None or snapshot.uav_time_received_at.tzinfo is None:
            return "vehicle_time_timestamp_missing"
        vehicle_time_age_s = (now - snapshot.uav_time_received_at.astimezone(UTC)).total_seconds()
        if vehicle_time_age_s < 0:
            return "vehicle_time_timestamp_invalid"
        if vehicle_time_age_s > max_age_s:
            return "vehicle_gps_time_stale"
        return "vehicle_feed_not_ready"

    def _log_telemetry_tx(self, payload: dict[str, Any]) -> None:
        self._log_protocol(
            "competition.telemetry_tx",
            "TX SİHA telemetrisi TEKNOFEST sunucusuna gönderiliyor",
            {
                "team_number": payload["takim_numarasi"],
                "latitude": payload["iha_enlem"],
                "longitude": payload["iha_boylam"],
                "altitude_agl_m": payload["iha_irtifa"],
                "gps_time": dict(payload["gps_saati"]),
                "target_locked": bool(payload["iha_kilitlenme"]),
            },
            severity="debug",
        )

    def _refresh_reference_data_if_due(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_reference_refresh < self.REFERENCE_REFRESH_PERIOD_S:
                return
            self._last_reference_refresh = now

        self._log_protocol(
            "competition.hss_request_tx",
            "TX TEKNOFEST HSS koordinat isteği gönderiliyor",
            {},
            severity="debug",
        )
        hss_payload = self._request_json("GET", "/api/hss_koordinatlari")
        if not isinstance(hss_payload, dict):
            raise CompetitionProtocolError("HSS yanıtı JSON nesnesi değil")
        raw_hss = hss_payload.get("hss_koordinat_bilgileri", [])
        if not isinstance(raw_hss, list):
            raise CompetitionProtocolError("hss_koordinat_bilgileri liste olmalı")
        zones: list[HssZone] = []
        for item in raw_hss:
            if not isinstance(item, dict):
                continue
            try:
                zones.append(HssZone.from_payload(item))
            except (KeyError, TypeError, ValueError):
                continue
        zones.sort(key=lambda item: item.identifier)
        hss_result = tuple(zones)
        self._mission.replace_source_overlays(
            "teknofest-hss",
            (zone.to_overlay() for zone in hss_result),
        )
        with self._lock:
            hss_changed = hss_result != self._hss_zones
            self._hss_zones = hss_result
        if hss_changed:
            self._hss_updated.publish_isolated(hss_result)
        self._log_protocol(
            "competition.hss_rx",
            f"RX TEKNOFEST HSS koordinatları alındı ({len(hss_result)})",
            {"hss_count": len(hss_result)},
            severity="debug",
        )

        self._log_protocol(
            "competition.qr_request_tx",
            "TX TEKNOFEST QR koordinat isteği gönderiliyor",
            {},
            severity="debug",
        )
        qr_payload = self._request_json("GET", "/api/qr_koordinati")
        if not isinstance(qr_payload, dict):
            raise CompetitionProtocolError("QR koordinatı yanıtı JSON nesnesi değil")
        try:
            qr = QrCoordinate.from_payload(qr_payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise CompetitionProtocolError("QR koordinatı geçersiz") from exc
        self._mission.replace_source_overlays("teknofest-qr", (qr.to_overlay(),))
        with self._lock:
            qr_changed = qr != self._qr_coordinate
            self._qr_coordinate = qr
        if qr_changed:
            self._qr_updated.publish_isolated(qr)
        self._log_protocol(
            "competition.qr_rx",
            "RX TEKNOFEST QR koordinatı alındı",
            {"latitude": qr.latitude, "longitude": qr.longitude},
            severity="debug",
        )
        self._publish_state(
            hss_count=len(hss_result),
            qr_available=True,
            last_success_at=datetime.now(UTC),
        )

    def _require_connected(self) -> None:
        state = self.state
        if not state.connected or not state.authenticated:
            raise ConnectionError("TEKNOFEST sunucu oturumu açık değil")

    def _log_protocol(
        self,
        action: str,
        message: str,
        details: dict[str, Any],
        *,
        severity: str = "info",
    ) -> None:
        if self._logger:
            self._logger.event(action, message, severity=severity, details=details)

    def _request_json(self, method: str, path: str, payload: Any = None) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.config.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._http_lock:
                with self._opener.open(request, timeout=self.config.timeout_s) as response:
                    raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            detail = self._decode_error(raw)
            raise CompetitionProtocolError(f"HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ConnectionError(f"{self.config.base_url} erişilemiyor: {reason}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompetitionProtocolError("Sunucu geçersiz JSON döndürdü") from exc

    @staticmethod
    def _decode_error(raw: bytes) -> str:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", errors="replace") or "sunucu hatası"
        if isinstance(payload, dict):
            return str(payload.get("mesaj", payload.get("message", payload)))
        return str(payload)

    @staticmethod
    def _server_time(payload: Any) -> datetime | None:
        if not isinstance(payload, dict):
            return None
        now = datetime.now(UTC)
        try:
            return now.replace(
                day=int(payload["gun"]),
                hour=int(payload["saat"]),
                minute=int(payload["dakika"]),
                second=int(payload["saniye"]),
                microsecond=int(payload.get("milisaniye", 0)) * 1000,
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _new_opener():
        return build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _publish_state(self, **changes: Any) -> None:
        with self._lock:
            self._state = replace(self._state, **changes)
            snapshot = replace(self._state)
        self._changed.publish_isolated(snapshot)
