#!/usr/bin/env python3
"""TEKNOFEST 2026 Savaşan İHA HTTP protokolü için yerel test sunucusu.

Uygulamadan ayrı bir süreç olarak çalıştırılır:
    python fake_teknofest_server.py
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
            # Rakip akışı HTTP sorguları arasında da ilerler. Kaçırılan
            # periyotları burada tamamlamak, zaman_farki değerini istek
            # sıklığından bağımsız ve gerçek bir örnek yaşı hâline getirir.
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
        quiet: bool = False,
    ) -> None:
        self.username = username
        self.password = password
        self.own_team_number = own_team_number
        self.competitor_count = competitor_count
        self.quiet = quiet
        self.sessions: dict[str, int] = {}
        self.last_telemetry_at: dict[int, float] = {}
        self.anchor = (39.8680, 32.7485, 1100.0)
        self.last_own_telemetry: dict[str, Any] | None = None
        self.telemetry_history: deque[dict[str, Any]] = deque(maxlen=200)
        self.received_lock_events: list[dict[str, Any]] = []
        self.received_kamikaze_events: list[dict[str, Any]] = []
        self.lock = threading.RLock()
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
            latitude, longitude, _altitude = self.anchor
        offsets = ((520, 180, 50), (-420, 640, 75), (900, -310, 100), (-760, -540, 150))
        values = []
        longitude_scale = max(0.05, math.cos(math.radians(latitude)))
        for identifier, (north_m, east_m, radius_m) in enumerate(offsets):
            values.append(
                {
                    "id": identifier,
                    "hssEnlem": round(latitude + math.degrees(north_m / EARTH_RADIUS_M), 8),
                    "hssBoylam": round(
                        longitude + math.degrees(east_m / (EARTH_RADIUS_M * longitude_scale)), 8
                    ),
                    "hssYaricap": radius_m,
                }
            )
        return {"sunucusaati": server_time(), "hss_koordinat_bilgileri": values}

    def log(self, message: str, *args: Any) -> None:
        if not self.quiet:
            logging.getLogger("fake-teknofest").info(message, *args)


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
            self.state.log("KONTROL RX SUNUCU_SAATI_REQUEST takım=%d", self._authenticated_team())
            self.state.log("KONTROL TX SUNUCU_SAATI takım=%d", self._authenticated_team())
            self._send(200, server_time())
        elif path == "/api/qr_koordinati":
            latitude, longitude, _altitude = self.state.anchor
            self.state.log("REFERANS RX QR_KOORDINATI_REQUEST takım=%d", self._authenticated_team())
            self.state.log(
                "REFERANS TX QR_KOORDINATI takım=%d konum=%.7f,%.7f",
                self._authenticated_team(),
                latitude + 0.0012,
                longitude - 0.0012,
            )
            self._send(200, {"qrEnlem": latitude + 0.0012, "qrBoylam": longitude - 0.0012})
        elif path == "/api/hss_koordinatlari":
            self.state.log("REFERANS RX HSS_KOORDINATLARI_REQUEST takım=%d", self._authenticated_team())
            payload = self.state.hss_payload()
            self.state.log(
                "REFERANS TX HSS_KOORDINATLARI takım=%d hss=%d",
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
                "RX LOGIN kullanıcı=%s istemci=%s",
                payload.get("kadi"),
                self.client_address[0],
            )
            token = self.state.login(str(payload.get("kadi", "")), str(payload.get("sifre", "")))
            if token is None:
                self.state.log("TX LOGIN_RED kullanıcı=%s istemci=%s", payload.get("kadi"), self.client_address[0])
                self._send(400, {"hata_kodu": 1, "mesaj": "Kullanıcı adı veya şifre hatalı"})
                return
            self.state.log("TX LOGIN_ACK takım=%d istemci=%s", self.state.own_team_number, self.client_address[0])
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
                    "TELEMETRI RX SIHA_TELEMETRI takım=%d konum=%.7f,%.7f gps=%s kilit=%d",
                    team,
                    float(payload["iha_enlem"]),
                    float(payload["iha_boylam"]),
                    json.dumps(payload.get("gps_saati", {}), ensure_ascii=False),
                    int(payload.get("iha_kilitlenme", 0)),
                )
                self.state.log(
                    "TELEMETRI TX RAKIP_TELEMETRI takım=%d rakip=%d veri_yasi=%d..%d ms",
                    team,
                    len(response["konumBilgileri"]),
                    min(ages, default=0),
                    max(ages, default=0),
                )
            else:
                self.state.log("TELEMETRI RED takım=%d HTTP=%d mesaj=%s", team, status, response.get("mesaj"))
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
            self.state.log("RX KILITLENME takım=%d veri=%s", team, json.dumps(payload, ensure_ascii=False))
            self.state.log("TX KILITLENME_ACK takım=%d", team)
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
            self.state.log("RX KAMIKAZE takım=%d veri=%s", team, json.dumps(payload, ensure_ascii=False))
            self.state.log("TX KAMIKAZE_ACK takım=%d", team)
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
    quiet: bool = False,
) -> FakeTeknofestServer:
    if competitor_count <= 0:
        raise ValueError("Rakip İHA sayısı pozitif olmalı")
    return FakeTeknofestServer(
        (host, port),
        FakeTeknofestState(
            username=username,
            password=password,
            own_team_number=team_number,
            competitor_count=competitor_count,
            seed=seed,
            quiet=quiet,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="TEKNOFEST 2026 sahte haberleşme sunucusu")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--username", default="kirlangic")
    parser.add_argument("--password", default="kirlangic-demo")
    parser.add_argument("--team-number", type=int, default=1)
    parser.add_argument("--competitors", type=int, default=27)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03dZ [SAHTE-TEKNOFEST] %(message)s",
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
    )
    host, port = server.server_address[:2]
    logging.getLogger("fake-teknofest").info(
        "Sunucu hazır http://%s:%d | takım=%d | rakip=%d | kullanıcı=%s",
        host,
        port,
        args.team_number,
        args.competitors,
        args.username,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        logging.getLogger("fake-teknofest").info("Sunucu kapatılıyor")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
