from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import math
from typing import Any, ClassVar
from urllib.parse import urlsplit
from uuid import uuid4

from src.core.abstracts import Model
from src.models.geometry import Point
from src.models.mission import MapContact, MapOverlay


@dataclass(frozen=True, slots=True)
class CompetitionServerConfig(Model):
    """UI-independent connection settings for the TEKNOFEST HTTP server."""

    TELEMETRY_PERIOD_S: ClassVar[float] = 1.0
    identifier: str = field(default_factory=lambda: uuid4().hex)
    name: str = "TEKNOFEST"
    base_url: str = "http://127.0.0.1:5000"
    username: str = "argestes"
    password: str = field(default="", repr=False, compare=False)
    team_number: int = 1
    timeout_s: float = 3.0
    auto_connect: bool = False

    def __post_init__(self) -> None:
        identifier = self.identifier.strip()
        name = self.name.strip()
        base_url = self.base_url.strip().rstrip("/")
        username = self.username.strip()
        if not identifier:
            raise ValueError("TEKNOFEST sunucu kimliği boş olamaz")
        if not name:
            raise ValueError("TEKNOFEST sunucu adı boş olamaz")
        parsed_url = urlsplit(base_url)
        try:
            parsed_url.port
        except ValueError as exc:
            raise ValueError("TEKNOFEST sunucu adresindeki port geçersiz") from exc
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or any(character.isspace() for character in parsed_url.netloc)
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "TEKNOFEST sunucu adresi http:// veya https:// ile başlamalı"
            )
        if not username:
            raise ValueError("TEKNOFEST kullanıcı adı boş olamaz")
        if not 1 <= int(self.team_number) <= 9999:
            raise ValueError("Takım numarası 1..9999 aralığında olmalı")
        if not 0.1 <= float(self.timeout_s) <= 120.0:
            raise ValueError("Sunucu zaman aşımı 0.1..120 saniye aralığında olmalı")
        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "team_number", int(self.team_number))
        object.__setattr__(self, "timeout_s", float(self.timeout_s))

    def to_dict(self, *, include_password: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        if not include_password:
            payload.pop("password", None)
        return payload


@dataclass(frozen=True, slots=True)
class CompetitionConnectionState(Model):
    connected: bool
    authenticated: bool
    running: bool
    endpoint: str
    team_number: int
    competitor_count: int = 0
    hss_count: int = 0
    qr_available: bool = False
    telemetry_tx_active: bool = False
    last_error: str = ""
    last_success_at: datetime | None = None
    session_health: str = "offline"
    telemetry_health: str = "unknown"
    clock_health: str = "unknown"
    qr_health: str = "unknown"
    hss_health: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.last_success_at is not None:
            payload["last_success_at"] = self.last_success_at.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class CompetitionServerTime(Model):
    """Date fragment returned by the competition protocol."""

    day: int
    hour: int
    minute: int
    second: int
    millisecond: int = 0

    def __post_init__(self) -> None:
        if not 1 <= int(self.day) <= 31:
            raise ValueError("Sunucu günü 1..31 aralığında olmalı")
        if not 0 <= int(self.hour) <= 23:
            raise ValueError("Sunucu saati 0..23 aralığında olmalı")
        if not 0 <= int(self.minute) <= 59 or not 0 <= int(self.second) <= 59:
            raise ValueError("Sunucu dakika/saniye değeri geçersiz")
        if not 0 <= int(self.millisecond) <= 999:
            raise ValueError("Sunucu milisaniyesi 0..999 aralığında olmalı")

    @classmethod
    def from_payload(cls, payload: Any) -> "CompetitionServerTime":
        if not isinstance(payload, dict):
            raise ValueError("Sunucu saati JSON nesnesi olmalı")
        return cls(
            day=int(payload["gun"]),
            hour=int(payload["saat"]),
            minute=int(payload["dakika"]),
            second=int(payload["saniye"]),
            millisecond=int(payload.get("milisaniye", 0)),
        )

    def resolve_near(self, reference: datetime) -> datetime:
        """Resolve the day-only protocol value to the closest calendar month."""

        reference = reference.astimezone(UTC)
        candidates: list[datetime] = []
        for month_delta in (-1, 0, 1):
            month_index = reference.year * 12 + reference.month - 1 + month_delta
            year, zero_based_month = divmod(month_index, 12)
            try:
                candidates.append(
                    datetime(
                        year,
                        zero_based_month + 1,
                        self.day,
                        self.hour,
                        self.minute,
                        self.second,
                        self.millisecond * 1000,
                        tzinfo=UTC,
                    )
                )
            except ValueError:
                continue
        if not candidates:
            raise ValueError("Sunucu günü yakın takvim aylarında geçerli değil")
        return min(
            candidates, key=lambda value: abs((value - reference).total_seconds())
        )


@dataclass(frozen=True, slots=True)
class CompetitionClockSample(Model):
    server_time: CompetitionServerTime
    received_monotonic: float
    request_started_monotonic: float
    resolved_at: datetime

    @property
    def rtt_s(self) -> float:
        return max(0.0, self.received_monotonic - self.request_started_monotonic)

    def estimate(self, monotonic_now: float) -> datetime:
        elapsed = max(0.0, float(monotonic_now) - self.received_monotonic)
        return self.resolved_at + timedelta(seconds=elapsed)


def _competition_time(value: datetime) -> dict[str, int]:
    timestamp = value.astimezone(UTC)
    return {
        "saat": timestamp.hour,
        "dakika": timestamp.minute,
        "saniye": timestamp.second,
        "milisaniye": timestamp.microsecond // 1000,
    }


def _vehicle_source(value: str) -> str:
    source = value.strip().lower()
    if not source:
        raise ValueError("SİHA olay kaynağı boş olamaz")
    if source in {"ui", "yki", "gcs", "ground-control", "yer-kontrol"}:
        raise ValueError(
            "Kilitlenme/kamikaze verisini YKİ üretemez; SİHA kaynağı gerekli"
        )
    return source


@dataclass(frozen=True, slots=True)
class LockEvent(Model):
    ended_at: datetime
    autonomous: bool
    vehicle_source: str

    def __post_init__(self) -> None:
        if self.ended_at.tzinfo is None:
            raise ValueError("Kilitlenme bitiş zamanı saat dilimi içermeli")
        object.__setattr__(self, "vehicle_source", _vehicle_source(self.vehicle_source))

    def to_payload(self) -> dict[str, Any]:
        return {
            "kilitlenmeBitisZamani": _competition_time(self.ended_at),
            "otonom_kilitlenme": int(self.autonomous),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ended_at": self.ended_at.isoformat(),
            "autonomous": self.autonomous,
            "vehicle_source": self.vehicle_source,
        }


@dataclass(frozen=True, slots=True)
class KamikazeEvent(Model):
    started_at: datetime
    ended_at: datetime
    qr_text: str
    vehicle_source: str

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("Kamikaze zamanları saat dilimi içermeli")
        if self.ended_at < self.started_at:
            raise ValueError("Kamikaze bitişi başlangıçtan önce olamaz")
        qr_text = self.qr_text.strip()
        if not qr_text:
            raise ValueError("QR metni SİHA tarafından okunmuş olmalı")
        object.__setattr__(self, "qr_text", qr_text)
        object.__setattr__(self, "vehicle_source", _vehicle_source(self.vehicle_source))

    def to_payload(self) -> dict[str, Any]:
        return {
            "kamikazeBaslangicZamani": _competition_time(self.started_at),
            "kamikazeBitisZamani": _competition_time(self.ended_at),
            "qrMetni": self.qr_text,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "qr_text": self.qr_text,
            "vehicle_source": self.vehicle_source,
        }


@dataclass(frozen=True, slots=True)
class HssZone(Model):
    identifier: int
    latitude: float
    longitude: float
    radius_m: float

    def __post_init__(self) -> None:
        Point(float(self.latitude), float(self.longitude))
        if int(self.identifier) < 0:
            raise ValueError("HSS kimliği negatif olamaz")
        if not math.isfinite(float(self.radius_m)) or float(self.radius_m) <= 0:
            raise ValueError("HSS yarıçapı pozitif olmalı")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HssZone":
        return cls(
            identifier=int(payload["id"]),
            latitude=float(payload["hssEnlem"]),
            longitude=float(payload["hssBoylam"]),
            radius_m=float(payload["hssYaricap"]),
        )

    def to_overlay(self) -> "MapOverlay":
        return MapOverlay(
            overlay_id=f"teknofest-hss-{self.identifier}",
            overlay_type="hss",
            label=f"HSS {self.identifier}",
            points=[[self.latitude, self.longitude]],
            color="#eb6464",
            radius_m=self.radius_m,
            metadata={
                "source": "teknofest-hss",
                "hss_id": self.identifier,
                "read_only": True,
                "persistent": False,
                "mission_upload": False,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class QrCoordinate(Model):
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        Point(float(self.latitude), float(self.longitude))

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "QrCoordinate":
        return cls(float(payload["qrEnlem"]), float(payload["qrBoylam"]))

    def to_overlay(self) -> "MapOverlay":
        return MapOverlay(
            overlay_id="teknofest-qr",
            overlay_type="target",
            label="TEKNOFEST QR",
            points=[[self.latitude, self.longitude]],
            color="#f4b942",
            radius_m=20.0,
            metadata={
                "source": "teknofest-qr",
                "kind": "qr_coordinate",
                "read_only": True,
                "persistent": False,
                "mission_upload": False,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CompetitorTelemetry(Model):
    team_number: int
    latitude: float
    longitude: float
    altitude_m: float
    pitch: float
    heading: float
    roll: float
    speed_m_s: float
    data_age_ms: int
    received_at: datetime
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        Point(float(self.latitude), float(self.longitude), float(self.altitude_m))
        numeric_values = (self.pitch, self.heading, self.roll, self.speed_m_s)
        if not all(math.isfinite(float(value)) for value in numeric_values):
            raise ValueError("Rakip telemetrisi sonlu sayılardan oluşmalı")
        if int(self.team_number) <= 0:
            raise ValueError("Rakip takım numarası pozitif olmalı")
        if int(self.data_age_ms) < 0:
            raise ValueError("Rakip veri yaşı negatif olamaz")

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], received_at: datetime
    ) -> "CompetitorTelemetry":
        return cls(
            team_number=int(payload["takim_numarasi"]),
            latitude=float(payload["iha_enlem"]),
            longitude=float(payload["iha_boylam"]),
            altitude_m=float(payload["iha_irtifa"]),
            pitch=float(payload.get("iha_dikilme", 0.0)),
            heading=float(payload.get("iha_yonelme", 0.0)) % 360.0,
            roll=float(payload.get("iha_yatis", 0.0)),
            speed_m_s=float(payload.get("iha_hizi", payload.get("iha_hiz", 0.0))),
            data_age_ms=max(0, int(payload.get("zaman_farki", 0))),
            received_at=received_at,
            raw=dict(payload),
        )

    def to_map_contact(self) -> MapContact:
        return MapContact(
            contact_id=f"teknofest-{self.team_number}",
            label=f"Takım {self.team_number}",
            latitude=self.latitude,
            longitude=self.longitude,
            color="#ff6b6b",
            heading=self.heading,
            speed=self.speed_m_s,
            source="siha_teknofest_server",
            metadata={
                "team_number": self.team_number,
                "altitude_m": self.altitude_m,
                "pitch": self.pitch,
                "roll": self.roll,
                "data_age_ms": self.data_age_ms,
                "received_at": self.received_at.isoformat(),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["received_at"] = self.received_at.isoformat()
        return payload
