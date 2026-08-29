from __future__ import annotations

import asyncio
import concurrent.futures
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import http.cookiejar
import json
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from src.core.events import EventBus
from src.models.competition import (
    CompetitionClockSample,
    CompetitionConnectionState,
    CompetitionServerConfig,
    CompetitionServerTime,
    CompetitorTelemetry,
    HssZone,
    KamikazeEvent,
    LockEvent,
    QrCoordinate,
)
from src.models.telemetry import TelemetrySnapshot
from src.services.abstracts.competition import CompetitionService
from src.services.planner import PlannerService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.vehicle import VehicleService
from src.services.async_runtime import AsyncRuntime
from src.services.logging_service import ServiceLogger


class CompetitionProtocolError(RuntimeError):
    pass


class CompetitionHttpError(CompetitionProtocolError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = int(status_code)
        self.detail = detail
        super().__init__(f"HTTP {self.status_code}: {detail}")


class TeknofestCompetitionService(CompetitionService):
    """Connect the 2026 TEKNOFEST HTTP protocol to central backend state."""

    HSS_REFRESH_PERIOD_S = 1.0
    QR_REFRESH_PERIOD_S = 5.0
    MAX_JSON_RESPONSE_BYTES = 2 * 1024 * 1024
    LOCK_FUTURE_TOLERANCE_S = 0.25
    KAMIKAZE_FUTURE_TOLERANCE_S = 0.25

    def __init__(
        self,
        config: CompetitionServerConfig,
        telemetry: TelemetryService,
        planner: PlannerService,
        vehicle: VehicleService,
        logger: ServiceLogger | None = None,
        *,
        async_runtime: AsyncRuntime | None = None,
    ) -> None:
        self._config = config
        self._server_configs: tuple[CompetitionServerConfig, ...] = (config,)
        self._auto_server_index = 0
        self._telemetry = telemetry
        self._planner = planner
        self._vehicle = vehicle
        self._logger = logger
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
        self._changed = EventBus[CompetitionConnectionState]()
        self._config_updated = EventBus[CompetitionServerConfig]()
        self._clock_updated = EventBus[CompetitionClockSample]()
        self._competitors_updated = EventBus[tuple[CompetitorTelemetry, ...]]()
        self._hss_updated = EventBus[tuple[HssZone, ...]]()
        self._qr_updated = EventBus[QrCoordinate | None]()
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._http_lock = threading.RLock()
        self._async_runtime = async_runtime or AsyncRuntime()
        self._owns_async_runtime = async_runtime is None
        self._run_future: concurrent.futures.Future[Any] | None = None
        self._opener = self._new_opener()
        self._last_hss_refresh = 0.0
        self._last_qr_refresh = 0.0
        self._hss_response_available = False
        self._last_connection_error_signature = ""
        self._last_connection_error_log_at = 0.0
        self._clock_sample: CompetitionClockSample | None = None
        self._latest_vehicle_snapshot: TelemetrySnapshot | None = None
        self._telemetry_subscription = self._telemetry.updated.subscribe(
            self._cache_vehicle_snapshot
        )
        self._cache_vehicle_snapshot(self._telemetry.current)

    @property
    def state(self) -> CompetitionConnectionState:
        with self._lock:
            return replace(self._state)

    @property
    def changed(self) -> EventBus[CompetitionConnectionState]:
        return self._changed

    @property
    def config_updated(self) -> EventBus[CompetitionServerConfig]:
        return self._config_updated

    @property
    def clock_updated(self) -> EventBus[CompetitionClockSample]:
        return self._clock_updated

    @property
    def competitors_updated(self) -> EventBus[tuple[CompetitorTelemetry, ...]]:
        return self._competitors_updated

    @property
    def competitors(self) -> tuple[CompetitorTelemetry, ...]:
        with self._lock:
            return tuple(self._competitors)

    @property
    def hss_updated(self) -> EventBus[tuple[HssZone, ...]]:
        return self._hss_updated

    @property
    def qr_updated(self) -> EventBus[QrCoordinate | None]:
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
            known = [item for item in self._server_configs if item.identifier != config.identifier]
            self._server_configs = (config, *known)
            self._auto_server_index = 0
            self._state = replace(
                self._state,
                endpoint=config.base_url,
                team_number=config.team_number,
            )
        self._config_updated.publish(config)

    def configure_servers(self, configs: tuple[CompetitionServerConfig, ...]) -> None:
        if not configs:
            raise ValueError("En az bir TEKNOFEST sunucusu yapılandırılmalı")
        identifiers = [config.identifier for config in configs]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("TEKNOFEST sunucu kimlikleri benzersiz olmalı")
        with self._lock:
            self._server_configs = tuple(configs)
            current = next(
                (item for item in self._server_configs if item.identifier == self._config.identifier),
                None,
            )
            if current is None:
                current = self._server_configs[0]
            self._config = current
            self._auto_server_index = self._server_configs.index(current)
            self._state = replace(
                self._state,
                endpoint=current.base_url,
                team_number=current.team_number,
            )
        self._config_updated.publish(current)

    def start(self) -> None:
        if self._select_first_auto_server():
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
            future = self._run_future
            self._run_future = None
            if future is not None and not future.done():
                future.cancel()
                try:
                    future.result(timeout=min(self.config.timeout_s + 0.5, 5.0))
                except (
                    concurrent.futures.CancelledError,
                    concurrent.futures.TimeoutError,
                ):
                    pass
            self._opener = self._new_opener()
            self._telemetry.patch(server_link=False, server_time=None)
            self._planner.replace_contacts((), source="teknofest")
            self._planner.replace_source_overlays("teknofest-hss", ())
            self._planner.replace_source_overlays("teknofest-qr", ())
            with self._lock:
                had_hss = bool(self._hss_zones)
                had_qr = self._qr_coordinate is not None
                self._competitors = ()
                self._hss_zones = ()
                self._qr_coordinate = None
                self._last_hss_refresh = 0.0
                self._last_qr_refresh = 0.0
                self._hss_response_available = False
            if had_hss:
                self._hss_updated.publish(())
            if had_qr:
                self._qr_updated.publish(None)
            self._publish_state(
                connected=False,
                authenticated=False,
                running=False,
                competitor_count=0,
                hss_count=0,
                qr_available=False,
                telemetry_tx_active=False,
                last_error="",
                session_health="offline",
                telemetry_health="unknown",
                clock_health="unknown",
                qr_health="unknown",
                hss_health="unknown",
            )

    def stop(self) -> None:
        self.disconnect()
        self._telemetry_subscription.cancel()
        if self._owns_async_runtime:
            self._async_runtime.stop()

    def _cache_vehicle_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        """Keep the last packet that is usable by the competition protocol.

        Server-owned patches may publish the same snapshot again.  They are safe
        to cache; invalid/partial startup snapshots never replace a valid packet.
        """
        if not snapshot.has_valid_position or self._vehicle_time(snapshot) is None:
            return
        with self._lock:
            self._latest_vehicle_snapshot = snapshot.copy()

    def _cached_vehicle_snapshot(self) -> TelemetrySnapshot | None:
        with self._lock:
            snapshot = self._latest_vehicle_snapshot
        return None if snapshot is None else snapshot.copy()

    def _launch(self) -> None:
        future = self._run_future
        if future is not None and not future.done():
            return
        self._opener = self._new_opener()
        with self._lock:
            self._last_connection_error_signature = ""
            self._last_connection_error_log_at = 0.0
        self._publish_state(running=True, last_error="")
        self._run_future = self._async_runtime.submit(self._run_async())
        self._run_future.add_done_callback(self._run_finished)

    def _select_first_auto_server(self) -> bool:
        with self._lock:
            for index, config in enumerate(self._server_configs):
                if config.auto_connect:
                    self._auto_server_index = index
                    self._config = config
                    self._state = replace(
                        self._state,
                        endpoint=config.base_url,
                        team_number=config.team_number,
                    )
                    return True
        return False

    def _advance_auto_server(self) -> bool:
        """Move to the next opted-in profile after an automatic failure."""

        with self._lock:
            candidates = [
                (index, config)
                for index, config in enumerate(self._server_configs)
                if config.auto_connect
            ]
            if len(candidates) < 2:
                return False
            current_position = next(
                (
                    position
                    for position, (index, _config) in enumerate(candidates)
                    if index == self._auto_server_index
                ),
                -1,
            )
            next_index, next_config = candidates[(current_position + 1) % len(candidates)]
            self._auto_server_index = next_index
            self._config = next_config
            self._state = replace(
                self._state,
                endpoint=next_config.base_url,
                team_number=next_config.team_number,
            )
        self._config_updated.publish(next_config)
        if self._logger:
            self._logger.event(
                "competition.auto_server_selected",
                f"TEKNOFEST otomatik bağlantı adayı değiştirildi: {next_config.name}",
                severity="info",
                details={"server_id": next_config.identifier, "endpoint": next_config.base_url},
            )
        return True

    def _run_finished(self, future: concurrent.futures.Future[Any]) -> None:
        with self._lifecycle_lock:
            if self._run_future is future:
                self._run_future = None
        if future.cancelled():
            return
        try:
            future.result()
        except Exception as exc:
            self._publish_state(
                connected=False,
                authenticated=False,
                running=False,
                last_error=str(exc) or type(exc).__name__,
            )

    async def _run_async(self) -> None:
        announced_waiting_for_vehicle_data = False
        while True:
            try:
                await asyncio.to_thread(self._login)
                self._log_protocol(
                    "competition.server_time_request_tx",
                    "TX TEKNOFEST sunucu saati isteği gönderiliyor",
                    {},
                    severity="debug",
                )
                clock_started = time.monotonic()
                server_payload = await asyncio.to_thread(
                    self._request_json,
                    "GET",
                    "/api/sunucusaati",
                )
                server_time = self._update_server_clock(server_payload, clock_started)
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
                    session_health="authenticated",
                    clock_health="healthy" if server_time is not None else "degraded",
                )
                if self._logger:
                    self._logger.event(
                        "competition.connected",
                        "TEKNOFEST yarışma sunucusuna bağlanıldı",
                        severity="success",
                        details={"endpoint": self.config.base_url, "team_number": self.config.team_number},
                    )
                initial_feed_ready = self._cached_vehicle_snapshot() is not None
                # When live vehicle data is already available, let the fake
                # server see the ownship position before requesting nearby
                # reference data. Real competition servers are unaffected.
                if not initial_feed_ready:
                    await asyncio.to_thread(
                        self._refresh_reference_data_if_due,
                        force=True,
                    )
                first_telemetry_sent = False
                next_telemetry_at = 0.0

                while True:
                    started = time.monotonic()
                    period_s = CompetitionServerConfig.TELEMETRY_PERIOD_S
                    if started >= next_telemetry_at:
                        snapshot = self._cached_vehicle_snapshot()
                        vehicle_time = (
                            self._vehicle_time(snapshot) if snapshot is not None else None
                        )
                        if snapshot is not None and vehicle_time is not None:
                            outbound = self._telemetry_payload(snapshot, vehicle_time)
                            self._log_telemetry_tx(outbound)
                            telemetry_started = time.monotonic()
                            response = await asyncio.to_thread(
                                self._request_json,
                                "POST",
                                "/api/telemetri_gonder",
                                outbound,
                            )
                            self._consume_telemetry_response(response, telemetry_started)
                            if not first_telemetry_sent:
                                first_telemetry_sent = True
                                await asyncio.to_thread(
                                    self._refresh_reference_data_if_due,
                                    force=True,
                                )
                            announced_waiting_for_vehicle_data = False
                        elif not announced_waiting_for_vehicle_data:
                            announced_waiting_for_vehicle_data = True
                            if self._logger:
                                self._logger.event(
                                    "competition.telemetry_blocked",
                                    "TEKNOFEST telemetri TX ilk geçerli SİHA paketi bekleniyor",
                                    severity="warning",
                                    details={
                                        "reason": "vehicle_snapshot_unavailable",
                                        "vehicle_connected": self._vehicle.connected,
                                    },
                                )
                        if snapshot is None or vehicle_time is None:
                            if self.state.telemetry_tx_active:
                                self._publish_state(telemetry_tx_active=False)
                            self._log_protocol(
                                "competition.server_time_request_tx",
                                "TX TEKNOFEST bağlantı sağlığı/saat isteği gönderiliyor",
                                {},
                                severity="debug",
                            )
                            clock_started = time.monotonic()
                            health = await asyncio.to_thread(
                                self._request_json,
                                "GET",
                                "/api/sunucusaati",
                            )
                            health_time = self._update_server_clock(health, clock_started)
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
                                session_health="authenticated",
                                clock_health=(
                                    "healthy" if health_time is not None else "degraded"
                                ),
                            )
                        next_telemetry_at = started + period_s
                    await asyncio.to_thread(self._refresh_reference_data_if_due)
                    now = time.monotonic()
                    with self._lock:
                        next_hss_at = (
                            self._last_hss_refresh + self.HSS_REFRESH_PERIOD_S
                        )
                    next_wakeup = min(next_telemetry_at, next_hss_at)
                    await asyncio.sleep(max(0.0, next_wakeup - now))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                with self._lock:
                    self._hss_response_available = False
                self._replace_qr_coordinate(None)
                self._telemetry.patch(server_link=False, server_time=None)
                self._publish_state(
                    connected=False,
                    authenticated=False,
                    running=True,
                    telemetry_tx_active=False,
                    last_error=message,
                    session_health="offline",
                    telemetry_health="unavailable",
                    clock_health="unavailable",
                    hss_health="degraded",
                )
                self._log_connection_failure(message)
                self._opener = self._new_opener()
                switched_server = self._advance_auto_server()
                retry_delay = (
                    10.0
                    if isinstance(exc, CompetitionHttpError)
                    and exc.status_code in {400, 403, 404}
                    else 2.0
                )
                if switched_server:
                    retry_delay = min(retry_delay, 0.1)
                await asyncio.sleep(retry_delay)

    def refresh_reference_data(self) -> None:
        if not self.state.connected:
            raise ConnectionError("TEKNOFEST sunucu oturumu açık değil")
        self._refresh_reference_data_if_due(force=True)

    def refresh_qr_coordinate(self) -> None:
        """Fetch only the QR target needed by a pending kamikaze launch."""

        self._require_connected()
        with self._lock:
            self._last_qr_refresh = time.monotonic()
        self._fetch_qr_coordinate()
        self._publish_state(
            qr_available=self.qr_coordinate is not None,
            last_success_at=datetime.now(UTC),
        )

    def submit_lock_event(self, event: LockEvent) -> None:
        self._require_connected()
        server_now = self.estimated_server_time()
        if server_now is None:
            raise CompetitionProtocolError(
                "Kilitlenme paketi sunucu saati olmadan gönderilemez"
            )
        report_age_s = (server_now - event.ended_at.astimezone(UTC)).total_seconds()
        if report_age_s < -self.LOCK_FUTURE_TOLERANCE_S:
            raise CompetitionProtocolError(
                "Kilitlenme bitiş zamanı sunucu saatinin ilerisinde"
            )
        payload = event.to_payload()
        self._log_protocol(
            "competition.lock_tx",
            "TX SİHA kilitlenme olayı TEKNOFEST sunucusuna gönderiliyor",
            {
                "vehicle_source": event.vehicle_source,
                "autonomous": event.autonomous,
                "report_age_ms": round(report_age_s * 1000.0, 1),
            },
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
        server_now = self.estimated_server_time()
        if server_now is None:
            raise CompetitionProtocolError(
                "Kamikaze paketi sunucu saati olmadan gönderilemez"
            )
        report_age_s = (server_now - event.ended_at.astimezone(UTC)).total_seconds()
        if report_age_s < -self.KAMIKAZE_FUTURE_TOLERANCE_S:
            raise CompetitionProtocolError("Kamikaze bitiş zamanı sunucu saatinin ilerisinde")
        payload = event.to_payload()
        self._log_protocol(
            "competition.kamikaze_tx",
            "TX SİHA kamikaze olayı TEKNOFEST sunucusuna gönderiliyor",
            {
                "vehicle_source": event.vehicle_source,
                "qr_text_length": len(event.qr_text),
                "report_age_ms": round(report_age_s * 1000.0, 1),
            },
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

    def _consume_telemetry_response(
        self,
        payload: Any,
        request_started_monotonic: float | None = None,
    ) -> None:
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
        self._planner.replace_contacts(
            (contact.to_map_contact() for contact in result),
            source="teknofest",
        )
        server_time = self._update_server_clock(
            payload.get("sunucusaati", {}),
            request_started_monotonic,
        )
        self._telemetry.patch(server_link=True, server_time=server_time)
        self._competitors_updated.publish(result)
        self._publish_state(
            connected=True,
            authenticated=True,
            running=True,
            telemetry_tx_active=True,
            competitor_count=len(result),
            last_error="",
            last_success_at=received_at,
            session_health="authenticated",
            telemetry_health="healthy",
            clock_health="healthy" if server_time is not None else "degraded",
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
        server_time = self.estimated_server_time()
        reference_time = server_time or vehicle_time
        timestamp = reference_time - timedelta(
            milliseconds=30 + secrets.randbelow(150)
        )
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
            "iha_irtifa": round(max(0.0, snapshot.altitude_agl), 2),
            "iha_dikilme": round(snapshot.pitch, 2),
            "iha_yonelme": round(snapshot.heading % 360.0, 2),
            "iha_yatis": round(snapshot.roll, 2),
            "iha_hiz": round(max(0.0, snapshot.ground_speed), 2),
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
            poll_hss = (
                force
                or now - self._last_hss_refresh >= self.HSS_REFRESH_PERIOD_S
            )
            poll_qr = (
                force
                or now - self._last_qr_refresh >= self.QR_REFRESH_PERIOD_S
            )
            if poll_hss:
                self._last_hss_refresh = now
            if poll_qr:
                self._last_qr_refresh = now
        if not poll_hss and not poll_qr:
            return

        if poll_hss:
            self._poll_hss_zones()

        if poll_qr:
            self._poll_qr_coordinate()

        with self._lock:
            hss_count = len(self._hss_zones)
            qr_available = self._qr_coordinate is not None
        self._publish_state(
            hss_count=hss_count,
            qr_available=qr_available,
            last_success_at=datetime.now(UTC),
        )

    def _poll_hss_zones(self) -> None:
        """Refresh the one-second HSS lease without stopping other server data."""

        self._log_protocol(
            "competition.hss_request_tx",
            "TX TEKNOFEST HSS koordinat isteği gönderiliyor",
            {},
            severity="debug",
        )
        try:
            payload = self._request_json("GET", "/api/hss_koordinatlari")
            if not isinstance(payload, dict):
                raise CompetitionProtocolError("HSS yanıtı JSON nesnesi değil")
            raw_hss = payload.get("hss_koordinat_bilgileri")
            if not isinstance(raw_hss, list):
                raise CompetitionProtocolError(
                    "hss_koordinat_bilgileri liste olmalı"
                )
            zones: list[HssZone] = []
            for item in raw_hss:
                if not isinstance(item, dict):
                    continue
                try:
                    zones.append(HssZone.from_payload(item))
                except (KeyError, TypeError, ValueError):
                    continue
            zones.sort(key=lambda item: item.identifier)
            result = tuple(zones)
        except Exception as exc:
            if isinstance(exc, CompetitionHttpError) and exc.status_code == 401:
                raise
            with self._lock:
                was_available = self._hss_response_available
                self._hss_response_available = False
            self._publish_state(hss_health="degraded")
            if was_available:
                self._log_protocol(
                    "competition.hss_unavailable",
                    "TEKNOFEST HSS yanıtı kesildi; son doğrulanmış HSS alanları korunuyor",
                    {"error": str(exc), "error_type": type(exc).__name__},
                    severity="warning",
                )
            return

        with self._lock:
            self._hss_response_available = True
        self._replace_hss_zones(result)
        self._publish_state(hss_health="healthy")
        self._log_protocol(
            "competition.hss_rx",
            f"RX TEKNOFEST HSS koordinatları alındı ({len(result)})",
            {"hss_count": len(result)},
            severity="debug",
        )

    def _poll_qr_coordinate(self) -> None:
        """Refresh QR health without allowing one endpoint to drop the session."""

        try:
            self._fetch_qr_coordinate()
        except CompetitionHttpError as exc:
            if exc.status_code == 401:
                raise
            should_log = self.state.qr_health != "degraded"
            self._publish_state(qr_health="degraded")
            if should_log:
                self._log_protocol(
                    "competition.qr_unavailable",
                    "TEKNOFEST QR koordinat endpoint'i geçici olarak kullanılamıyor",
                    {"error": str(exc), "status_code": exc.status_code},
                    severity="warning",
                )
        except Exception as exc:
            should_log = self.state.qr_health != "degraded"
            self._publish_state(qr_health="degraded")
            if should_log:
                self._log_protocol(
                    "competition.qr_unavailable",
                    "TEKNOFEST QR koordinat endpoint'i geçici olarak kullanılamıyor",
                    {"error": str(exc), "error_type": type(exc).__name__},
                    severity="warning",
                )

    def _fetch_qr_coordinate(self) -> None:
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
        self._replace_qr_coordinate(qr)
        self._publish_state(qr_health="healthy")
        self._log_protocol(
            "competition.qr_rx",
            "RX TEKNOFEST QR koordinatı alındı",
            {"latitude": qr.latitude, "longitude": qr.longitude},
            severity="debug",
        )

    def refresh_server_time(self) -> datetime:
        """Fetch a fresh authoritative clock sample through the owned HTTP session."""

        self._require_connected()
        self._log_protocol(
            "competition.server_time_request_tx",
            "TX TEKNOFEST güncel sunucu saati isteği gönderiliyor",
            {},
            severity="debug",
        )
        started = time.monotonic()
        payload = self._request_json("GET", "/api/sunucusaati")
        server_time = self._update_server_clock(payload, started)
        if server_time is None:
            raise CompetitionProtocolError("TEKNOFEST sunucu saati yanıtı geçersiz")
        self._telemetry.patch(server_link=True, server_time=server_time)
        self._publish_state(
            connected=True,
            authenticated=True,
            running=True,
            last_error="",
            last_success_at=datetime.now(UTC),
            session_health="authenticated",
            clock_health="healthy",
        )
        self._log_protocol(
            "competition.server_time_rx",
            "RX TEKNOFEST güncel sunucu saati alındı",
            {"server_time": server_time.isoformat()},
            severity="debug",
        )
        return server_time

    def _replace_hss_zones(self, zones: tuple[HssZone, ...]) -> bool:
        with self._lock:
            if zones == self._hss_zones:
                return False
        self._planner.replace_source_overlays(
            "teknofest-hss",
            (zone.to_overlay() for zone in zones),
        )
        with self._lock:
            self._hss_zones = zones
        self._hss_updated.publish(zones)
        self._publish_state(hss_count=len(zones))
        return True

    def _replace_qr_coordinate(self, qr: QrCoordinate | None) -> bool:
        with self._lock:
            if qr == self._qr_coordinate:
                return False
        overlays = () if qr is None else (qr.to_overlay(),)
        self._planner.replace_source_overlays("teknofest-qr", overlays)
        with self._lock:
            self._qr_coordinate = qr
        self._qr_updated.publish(qr)
        self._publish_state(qr_available=qr is not None)
        return True

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
                    raw = self._read_limited(response)
        except HTTPError as exc:
            raw = self._read_limited(exc)
            detail = self._decode_error(raw)
            raise CompetitionHttpError(exc.code, detail) from exc
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

    def _read_limited(self, response: Any) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > self.MAX_JSON_RESPONSE_BYTES:
                    raise CompetitionProtocolError("Sunucu JSON yanıtı boyut sınırını aşıyor")
            except ValueError:
                pass
        raw = response.read(self.MAX_JSON_RESPONSE_BYTES + 1)
        if len(raw) > self.MAX_JSON_RESPONSE_BYTES:
            raise CompetitionProtocolError("Sunucu JSON yanıtı boyut sınırını aşıyor")
        return raw

    def _update_server_clock(
        self,
        payload: Any,
        request_started_monotonic: float | None = None,
    ) -> datetime | None:
        received_monotonic = time.monotonic()
        try:
            server_time = CompetitionServerTime.from_payload(payload)
            resolved = server_time.resolve_near(datetime.now(UTC))
            sample = CompetitionClockSample(
                server_time=server_time,
                received_monotonic=received_monotonic,
                request_started_monotonic=(
                    received_monotonic
                    if request_started_monotonic is None
                    else float(request_started_monotonic)
                ),
                resolved_at=resolved,
            )
        except (KeyError, TypeError, ValueError):
            return None
        with self._lock:
            self._clock_sample = sample
        self._clock_updated.publish(sample)
        return sample.estimate(received_monotonic)

    def estimated_server_time(self) -> datetime | None:
        with self._lock:
            sample = self._clock_sample
        return None if sample is None else sample.estimate(time.monotonic())

    @staticmethod
    def _new_opener():
        return build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _publish_state(self, **changes: Any) -> None:
        with self._lock:
            updated = replace(self._state, **changes)
            if updated == self._state:
                return
            self._state = updated
            snapshot = replace(self._state)
        self._changed.publish(snapshot)

    def _log_connection_failure(self, message: str) -> None:
        if self._logger is None:
            return
        now = time.monotonic()
        with self._lock:
            repeated = message == self._last_connection_error_signature
            if repeated and now - self._last_connection_error_log_at < 30.0:
                return
            self._last_connection_error_signature = message
            self._last_connection_error_log_at = now
        self._logger.event(
            "competition.connection_failed",
            f"TEKNOFEST sunucu bağlantısı kesildi: {message}",
            severity="danger",
            details={"endpoint": self.config.base_url},
        )
