#!/usr/bin/env python3
"""Local test server for the TEKNOFEST 2026 Combat UAV HTTP protocol.

Run it as a process separate from the application:
    python fake_teknofest_server.py
    python fake_teknofest_server.py --lang tr
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import random
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlparse


EARTH_RADIUS_M = 6_371_000.0
HSS_MIN_CENTER_DISTANCE_M = 280.0
HSS_MAX_CENTER_DISTANCE_M = 900.0
HSS_RADII_M = (50, 75, 100, 150)

QR_LATITUDE = 37.980637
QR_LONGITUDE = 41.842464

ANSI_RED = "\033[91m"
ANSI_RESET = "\033[0m"
# Keep the fake-server console focused on received competition result payloads.
CONSOLE_PACKET_LOG_KEYS = frozenset({"lock_received", "kamikaze_received"})
RED_PACKET_LOG_KEYS = CONSOLE_PACKET_LOG_KEYS

LOG_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "server_ready": (
            "Server ready at http://%s:%d | team=%d | competitors=%d | user=%s"
        ),
        "server_stopping": "Server is shutting down",
        "control_time_request": "CONTROL RX SERVER_TIME_REQUEST team=%d",
        "control_time_response": "CONTROL TX SERVER_TIME team=%d",
        "reference_qr_request": "REFERENCE RX QR_COORDINATE_REQUEST team=%d",
        "reference_qr_response": (
            "REFERENCE TX QR_COORDINATE team=%d position=%.7f,%.7f"
        ),
        "reference_hss_request": "REFERENCE RX HSS_COORDINATES_REQUEST team=%d",
        "reference_hss_response": "REFERENCE TX HSS_COORDINATES team=%d hss=%d",
        "login_received": "RX LOGIN user=%s client=%s",
        "login_rejected": "TX LOGIN_REJECTED user=%s client=%s",
        "login_accepted": "TX LOGIN_ACK team=%d client=%s",
        "telemetry_received": (
            "TELEMETRY RX UAV_TELEMETRY team=%d position=%.7f,%.7f "
            "gps=%s locked=%d"
        ),
        "telemetry_response": (
            "TELEMETRY TX COMPETITOR_TELEMETRY team=%d competitors=%d "
            "data_age=%d..%d ms"
        ),
        "telemetry_rejected": (
            "TELEMETRY REJECTED team=%d HTTP=%d message=%s"
        ),
        "lock_received": "RX TARGET_LOCK team=%d data=%s",
        "lock_accepted": "TX TARGET_LOCK_ACK team=%d",
        "kamikaze_received": "RX KAMIKAZE team=%d data=%s",
        "kamikaze_accepted": "TX KAMIKAZE_ACK team=%d",
    },
    "tr": {
        "server_ready": (
            "Sunucu hazır http://%s:%d | takım=%d | rakip=%d | kullanıcı=%s"
        ),
        "server_stopping": "Sunucu kapatılıyor",
        "control_time_request": "KONTROL RX SUNUCU_SAATI_İSTEĞİ takım=%d",
        "control_time_response": "KONTROL TX SUNUCU_SAATİ takım=%d",
        "reference_qr_request": "REFERANS RX QR_KOORDİNATI_İSTEĞİ takım=%d",
        "reference_qr_response": (
            "REFERANS TX QR_KOORDİNATI takım=%d konum=%.7f,%.7f"
        ),
        "reference_hss_request": "REFERANS RX HSS_KOORDİNATLARI_İSTEĞİ takım=%d",
        "reference_hss_response": "REFERANS TX HSS_KOORDİNATLARI takım=%d hss=%d",
        "login_received": "RX GİRİŞ kullanıcı=%s istemci=%s",
        "login_rejected": "TX GİRİŞ_RED kullanıcı=%s istemci=%s",
        "login_accepted": "TX GİRİŞ_ONAY takım=%d istemci=%s",
        "telemetry_received": (
            "TELEMETRİ RX SİHA_TELEMETRİ takım=%d konum=%.7f,%.7f "
            "gps=%s kilit=%d"
        ),
        "telemetry_response": (
            "TELEMETRİ TX RAKİP_TELEMETRİ takım=%d rakip=%d "
            "veri_yaşı=%d..%d ms"
        ),
        "telemetry_rejected": (
            "TELEMETRİ RED takım=%d HTTP=%d mesaj=%s"
        ),
        "lock_received": "RX KİLİTLENME takım=%d veri=%s",
        "lock_accepted": "TX KİLİTLENME_ONAY takım=%d",
        "kamikaze_received": "RX KAMİKAZE takım=%d veri=%s",
        "kamikaze_accepted": "TX KAMİKAZE_ONAY takım=%d",
    },
}


def log_message(language: str, key: str) -> str:
    """Return a localized console message template."""

    return LOG_MESSAGES[language][key]


def console_log_message(language: str, key: str) -> str:
    """Colour only QR and target-lock packet logs red in a terminal."""

    message = log_message(language, key)
    if key in RED_PACKET_LOG_KEYS:
        return f"{ANSI_RED}{message}{ANSI_RESET}"
    return message


def server_time(now: datetime | None = None) -> dict[str, int]:
    value = (now or datetime.now(UTC)).astimezone(UTC)
    return {
        "gun": value.day,
        "saat": value.hour,
        "dakika": value.minute,
        "saniye": value.second,
        "milisaniye": value.microsecond // 1000,
    }


def valid_event_time(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        hour = int(payload["saat"])
        minute = int(payload["dakika"])
        second = int(payload["saniye"])
        millisecond = int(payload["milisaniye"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59 and 0 <= millisecond <= 999


@dataclass(slots=True)
class CompetitorSample:
    team_number: int
    radius_m: float
    angular_speed: float
    phase: float
    update_period_s: float
    next_update_at: float = 0.0
    sample_time: float = 0.0
    update_index: int = 0
    payload: dict[str, Any] | None = None

    def sample(
        self,
        now_wall: float,
        anchor_latitude: float,
        anchor_longitude: float,
        anchor_altitude: float,
    ) -> dict[str, Any]:
        if self.payload is None:
            self.update_index += 1
            self._update_payload(
                now_wall,
                anchor_latitude,
                anchor_longitude,
                anchor_altitude,
            )
            self.next_update_at = now_wall + self.update_period_s
        else:
            # Advance competitor samples between HTTP requests. Catching up
            # missed periods keeps zaman_farki independent from request
            # frequency and makes it represent the real sample age.
            while now_wall >= self.next_update_at:
                scheduled_at = self.next_update_at
                self.update_index += 1
                dropped = (self.team_number * 7 + self.update_index) % 17 == 0
                if not dropped:
                    self._update_payload(
                        scheduled_at,
                        anchor_latitude,
                        anchor_longitude,
                        anchor_altitude,
                    )
                self.next_update_at += self.update_period_s
        result = dict(self.payload or {})
        result["zaman_farki"] = max(0, int(round((now_wall - self.sample_time) * 1000.0)))
        return result

    def _update_payload(
        self,
        now_wall: float,
        anchor_latitude: float,
        anchor_longitude: float,
        anchor_altitude: float,
    ) -> None:
        angle = self.phase + now_wall * self.angular_speed
        breathing = 1.0 + 0.12 * math.sin(now_wall * 0.071 + self.team_number)
        north_m = self.radius_m * breathing * math.cos(angle)
        east_m = self.radius_m * breathing * math.sin(angle)
        latitude = anchor_latitude + math.degrees(north_m / EARTH_RADIUS_M)
        longitude_scale = max(0.05, math.cos(math.radians(anchor_latitude)))
        longitude = anchor_longitude + math.degrees(east_m / (EARTH_RADIUS_M * longitude_scale))
        heading = math.degrees(angle + math.pi / 2.0) % 360.0
        altitude = anchor_altitude + 22.0 + (self.team_number % 9) * 13.0
        altitude += math.sin(angle * 1.7) * 18.0
        self.sample_time = now_wall
        self.payload = {
            "takim_numarasi": self.team_number,
            "iha_enlem": round(latitude, 7),
            "iha_boylam": round(longitude, 7),
            "iha_irtifa": round(max(20.0, altitude), 2),
            "iha_dikilme": round(math.sin(angle * 1.3) * 8.0, 2),
            "iha_yonelme": round(heading, 2),
            "iha_yatis": round(math.sin(angle * 2.1) * 22.0, 2),
            "iha_hizi": round(22.0 + (self.team_number % 11) * 1.35, 2),
        }


class FakeTeknofestState:
    def __init__(
        self,
        *,
        username: str,
        password: str,
        own_team_number: int,
        competitor_count: int,
        seed: int,
        hss_seed: int | None = None,
        quiet: bool = False,
        language: str = "en",
    ) -> None:
        self.username = username
        self.password = password
        self.own_team_number = own_team_number
        self.competitor_count = competitor_count
        self.quiet = quiet
        self.language = language
        self.sessions: dict[str, int] = {}
        self.last_telemetry_at: dict[int, float] = {}
        self.initial_anchor = (39.8680, 32.7485, 1100.0)
        self.anchor = self.initial_anchor
        self.last_own_telemetry: dict[str, Any] | None = None
        self.telemetry_history: deque[dict[str, Any]] = deque(maxlen=200)
        self.received_lock_events: list[dict[str, Any]] = []
        self.received_kamikaze_events: list[dict[str, Any]] = []
        self.lock = threading.RLock()
        self._hss_seed = (
            int(hss_seed)
            if hss_seed is not None
            else random.SystemRandom().randrange(0, 2**63)
        )
        self._hss_anchor_locked = False
        self.hss_zones: tuple[dict[str, int | float], ...] = ()
        rng = random.Random(seed)
        team_numbers: list[int] = []
        candidate = 1
        while len(team_numbers) < competitor_count:
            if candidate != own_team_number:
                team_numbers.append(candidate)
            candidate += 1
        self.competitors = [
            CompetitorSample(
                team_number=team,
                radius_m=260.0 + index * 54.0 + rng.uniform(-22.0, 22.0),
                angular_speed=(0.0024 + (index % 7) * 0.00031) * (-1 if index % 2 else 1),
                phase=rng.uniform(0.0, math.tau),
                update_period_s=0.55 + (index % 6) * 0.31,
            )
            for index, team in enumerate(team_numbers)
        ]

    def _generate_hss_zones(
        self,
        rng: random.Random,
        anchor: tuple[float, float, float],
    ) -> tuple[dict[str, int | float], ...]:
        latitude, longitude, _altitude = anchor
        longitude_scale = max(0.05, math.cos(math.radians(latitude)))
        sector_width = math.tau / len(HSS_RADII_M)
        rotation = rng.uniform(0.0, math.tau)
        zones: list[dict[str, int | float]] = []
        for identifier, radius_m in enumerate(HSS_RADII_M):
            angle = (
                rotation
                + identifier * sector_width
                + rng.uniform(-sector_width * 0.22, sector_width * 0.22)
            )
            center_distance_m = rng.uniform(
                HSS_MIN_CENTER_DISTANCE_M,
                HSS_MAX_CENTER_DISTANCE_M,
            )
            north_m = center_distance_m * math.cos(angle)
            east_m = center_distance_m * math.sin(angle)
            zones.append(
                {
                    "id": identifier,
                    "hssEnlem": round(
                        latitude + math.degrees(north_m / EARTH_RADIUS_M),
                        8,
                    ),
                    "hssBoylam": round(
                        longitude
                        + math.degrees(
                            east_m / (EARTH_RADIUS_M * longitude_scale)
                        ),
                        8,
                    ),
                    "hssYaricap": radius_m,
                }
            )
        return tuple(zones)

    def login(self, username: str, password: str) -> str | None:
        if username != self.username or password != self.password:
            return None
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.sessions[token] = self.own_team_number
        return token

    def team_for_session(self, token: str | None) -> int | None:
        if not token:
            return None
        with self.lock:
            return self.sessions.get(token)

    def telemetry_response(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        now_wall = time.time()
        team_number = int(payload.get("takim_numarasi", 0))
        with self.lock:
            last_at = self.last_telemetry_at.get(team_number)
            if last_at is not None and now_wall - last_at < 0.48:
                return 400, {"hata_kodu": 3, "mesaj": "Telemetri 2 Hz'den hızlı gönderilemez"}
            self.last_telemetry_at[team_number] = now_wall
            required = {
                "takim_numarasi", "iha_enlem", "iha_boylam", "iha_irtifa",
                "iha_dikilme", "iha_yonelme", "iha_yatis", "iha_hiz",
                "iha_batarya", "iha_otonom", "iha_kilitlenme",
                "hedef_merkez_X", "hedef_merkez_Y", "hedef_genislik",
                "hedef_yukseklik", "gps_saati",
            }
            if not required <= payload.keys():
                return 204, {"hata_kodu": 2, "mesaj": "Telemetri paketi eksik"}
            try:
                latitude = float(payload["iha_enlem"])
                longitude = float(payload["iha_boylam"])
                altitude = float(payload["iha_irtifa"])
                pitch = float(payload["iha_dikilme"])
                heading = float(payload["iha_yonelme"])
                roll = float(payload["iha_yatis"])
                speed = float(payload["iha_hiz"])
                battery = int(payload["iha_batarya"])
                autonomous = int(payload["iha_otonom"])
                locked = int(payload["iha_kilitlenme"])
                target_values = tuple(
                    int(payload[key])
                    for key in ("hedef_merkez_X", "hedef_merkez_Y", "hedef_genislik", "hedef_yukseklik")
                )
                gps = payload["gps_saati"]
                gps_values = tuple(int(gps[key]) for key in ("saat", "dakika", "saniye", "milisaniye"))
            except (KeyError, TypeError, ValueError):
                return 204, {"hata_kodu": 2, "mesaj": "Telemetri paketi eksik veya geçersiz"}
            valid = (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
                and altitude >= 0
                and -90 <= pitch <= 90
                and 0 <= heading <= 360
                and -90 <= roll <= 90
                and speed >= 0
                and 0 <= battery <= 100
                and autonomous in {0, 1}
                and locked in {0, 1}
                and all(value >= 0 for value in target_values)
                and 0 <= gps_values[0] <= 23
                and 0 <= gps_values[1] <= 59
                and 0 <= gps_values[2] <= 59
                and 0 <= gps_values[3] <= 999
            )
            if locked and (target_values[2] <= 0 or target_values[3] <= 0):
                valid = False
            if not valid:
                return 400, {"hata_kodu": 2, "mesaj": "Telemetri değeri izin verilen aralık dışı"}
            if not self._hss_anchor_locked:
                self.hss_zones = self._generate_hss_zones(
                    random.Random(self._hss_seed),
                    (latitude, longitude, altitude),
                )
                self._hss_anchor_locked = True
            self.anchor = (latitude, longitude, altitude)
            self.last_own_telemetry = dict(payload)
            self.telemetry_history.append(dict(payload))
            contacts = [
                competitor.sample(now_wall, latitude, longitude, altitude)
                for competitor in self.competitors
            ]
        return 200, {
            "sunucusaati": server_time(datetime.fromtimestamp(now_wall, UTC)),
            "konumBilgileri": contacts,
        }

    def hss_payload(self) -> dict[str, Any]:
        with self.lock:
            values = [dict(zone) for zone in self.hss_zones]
        return {"sunucusaati": server_time(), "hss_koordinat_bilgileri": values}

    def log(self, key: str, *args: Any) -> None:
        if not self.quiet and key in CONSOLE_PACKET_LOG_KEYS:
            logging.getLogger("fake-teknofest").info(
                console_log_message(self.language, key),
                *args,
            )


class FakeTeknofestHandler(BaseHTTPRequestHandler):
    server_version = "FakeTeknofest/2026"

    @property
    def state(self) -> FakeTeknofestState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/health":
            self._send(200, {"durum": "hazir", "rakip_iha_sayisi": self.state.competitor_count})
            return
        if self._authenticated_team() is None:
            self._send(401, {"mesaj": "Önce /api/giris ile oturum açılmalı"})
            return
        if path == "/api/sunucusaati":
            self.state.log("control_time_request", self._authenticated_team())
            self.state.log("control_time_response", self._authenticated_team())
            self._send(200, server_time())
        elif path == "/api/qr_koordinati":
            latitude, longitude, _altitude = self.state.anchor
            self.state.log("reference_qr_request", self._authenticated_team())
            self.state.log(
                "reference_qr_response",
                self._authenticated_team(),
                QR_LATITUDE,
                QR_LONGITUDE,
            )
            self._send(200, {"qrEnlem": QR_LATITUDE, "qrBoylam": QR_LONGITUDE})
        elif path == "/api/hss_koordinatlari":
            self.state.log("reference_hss_request", self._authenticated_team())
            payload = self.state.hss_payload()
            self.state.log(
                "reference_hss_response",
                self._authenticated_team(),
                len(payload["hss_koordinat_bilgileri"]),
            )
            self._send(200, payload)
        else:
            self._send(404, {"mesaj": "Geçersiz URL"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        payload = self._read_json()
        if payload is None:
            return
        if path == "/api/giris":
            self.state.log(
                "login_received",
                payload.get("kadi"),
                self.client_address[0],
            )
            token = self.state.login(str(payload.get("kadi", "")), str(payload.get("sifre", "")))
            if token is None:
                self.state.log(
                    "login_rejected",
                    payload.get("kadi"),
                    self.client_address[0],
                )
                self._send(400, {"hata_kodu": 1, "mesaj": "Kullanıcı adı veya şifre hatalı"})
                return
            self.state.log(
                "login_accepted",
                self.state.own_team_number,
                self.client_address[0],
            )
            self._send(
                200,
                {"takim_numarasi": self.state.own_team_number},
                headers={"Set-Cookie": f"teknofest_session={token}; Path=/; HttpOnly; SameSite=Strict"},
            )
            return
        team = self._authenticated_team()
        if team is None:
            self._send(401, {"mesaj": "Oturum geçersiz veya süresi dolmuş"})
            return
        if path == "/api/telemetri_gonder":
            if int(payload.get("takim_numarasi", 0)) != team:
                self._send(403, {"mesaj": "Başka takım adına veri gönderilemez"})
                return
            status, response = self.state.telemetry_response(payload)
            if status == 200:
                ages = [int(item["zaman_farki"]) for item in response["konumBilgileri"]]
                self.state.log(
                    "telemetry_received",
                    team,
                    float(payload["iha_enlem"]),
                    float(payload["iha_boylam"]),
                    json.dumps(payload.get("gps_saati", {}), ensure_ascii=False),
                    int(payload.get("iha_kilitlenme", 0)),
                )
                self.state.log(
                    "telemetry_response",
                    team,
                    len(response["konumBilgileri"]),
                    min(ages, default=0),
                    max(ages, default=0),
                )
            else:
                self.state.log(
                    "telemetry_rejected",
                    team,
                    status,
                    response.get("mesaj"),
                )
            self._send(status, response)
        elif path == "/api/kilitlenme_bilgisi":
            try:
                autonomous_lock = int(payload.get("otonom_kilitlenme", -1))
            except (TypeError, ValueError):
                autonomous_lock = -1
            if not valid_event_time(payload.get("kilitlenmeBitisZamani")) or autonomous_lock not in {0, 1}:
                self._send(400, {"hata_kodu": 2, "mesaj": "Kilitlenme paketi geçersiz"})
                return
            with self.state.lock:
                self.state.received_lock_events.append(dict(payload))
            self.state.log(
                "lock_received",
                team,
                json.dumps(payload, ensure_ascii=False),
            )
            self.state.log("lock_accepted", team)
            self._send(200, {"durum": "kaydedildi"})
        elif path == "/api/kamikaze_bilgisi":
            if (
                not valid_event_time(payload.get("kamikazeBaslangicZamani"))
                or not valid_event_time(payload.get("kamikazeBitisZamani"))
                or not str(payload.get("qrMetni", "")).strip()
            ):
                self._send(400, {"hata_kodu": 2, "mesaj": "Kamikaze paketi geçersiz"})
                return
            with self.state.lock:
                self.state.received_kamikaze_events.append(dict(payload))
            self.state.log(
                "kamikaze_received",
                team,
                json.dumps(payload, ensure_ascii=False),
            )
            self.state.log("kamikaze_accepted", team)
            self._send(200, {"durum": "kaydedildi"})
        else:
            self._send(404, {"mesaj": "Geçersiz URL"})

    def _authenticated_team(self) -> int | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("teknofest_session")
        return self.state.team_for_session(morsel.value if morsel else None)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            self._send(204, None)
            return None

    def _send(
        self,
        status: int,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        raw = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class FakeTeknofestServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: FakeTeknofestState) -> None:
        super().__init__(address, FakeTeknofestHandler)
        self.state = state


def create_server(
    host: str = "127.0.0.1",
    port: int = 5000,
    *,
    username: str = "kirlangic",
    password: str = "kirlangic-demo",
    team_number: int = 1,
    competitor_count: int = 27,
    seed: int = 2026,
    hss_seed: int | None = None,
    quiet: bool = False,
    language: str = "en",
) -> FakeTeknofestServer:
    if competitor_count <= 0:
        raise ValueError("Competitor UAV count must be positive")
    if language not in LOG_MESSAGES:
        raise ValueError(f"Unsupported console language: {language}")
    return FakeTeknofestServer(
        (host, port),
        FakeTeknofestState(
            username=username,
            password=password,
            own_team_number=team_number,
            competitor_count=competitor_count,
            seed=seed,
            hss_seed=hss_seed,
            quiet=quiet,
            language=language,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TEKNOFEST 2026 fake communication server"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--username", default="kapsulkirlangic")
    parser.add_argument("--password", default="E9mB3tJwQS")
    parser.add_argument("--team-number", type=int, default=21)
    parser.add_argument("--competitors", type=int, default=27)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--lang",
        choices=tuple(LOG_MESSAGES),
        default="en",
        help="console log language (default: en)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03dZ [FAKE-TEKNOFEST] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logging.Formatter.converter = time.gmtime
    server = create_server(
        args.host,
        args.port,
        username=args.username,
        password=args.password,
        team_number=args.team_number,
        competitor_count=args.competitors,
        seed=args.seed,
        language=args.lang,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
