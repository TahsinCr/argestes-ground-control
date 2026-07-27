from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from src.core.abstracts import Model
from src.models.competition import CompetitionServerConfig
from src.models.orin import OrinLinkConfig


DEFAULT_RADII = {
    "takeoff": 35,
    "waypoint": 35,
    "loiter": 75,
    "rtl": 75,
    "land": 35,
    "hss": 100,
}


@dataclass(slots=True)
class CacheSettings:
    enabled: bool = True
    limit_mb: int = 2048
    path: str = ""

    def __post_init__(self) -> None:
        self.limit_mb = int(self.limit_mb)
        if self.limit_mb <= 0:
            raise ValueError("Cache limiti pozitif olmalı")


@dataclass(slots=True)
class MavlinkSettings:
    endpoint: str = "udp:127.0.0.1:14550"
    baud: int = 115200
    heartbeat_timeout: int = 10
    auto_connect: bool = False
    dialect: str = "ardupilotmega"
    source_system: int = 255
    source_component: int = 0
    autoreconnect: bool = True
    operation_timeout: int = 5
    sync_parameters_on_connect: bool = True
    sync_mission_on_connect: bool = True

    def __post_init__(self) -> None:
        self.endpoint = self.endpoint.strip()
        self.baud = int(self.baud)
        self.heartbeat_timeout = int(self.heartbeat_timeout)
        self.dialect = self.dialect.strip()
        self.source_system = int(self.source_system)
        self.source_component = int(self.source_component)
        self.operation_timeout = int(self.operation_timeout)
        if not self.endpoint:
            raise ValueError("MAVLink bağlantı adresi boş olamaz")
        if self.baud <= 0:
            raise ValueError("MAVLink baud değeri pozitif olmalı")
        if self.heartbeat_timeout <= 0:
            raise ValueError("Heartbeat zaman aşımı pozitif olmalı")
        if not self.dialect:
            raise ValueError("MAVLink dialect boş olamaz")
        if not 0 <= self.source_system <= 255 or not 0 <= self.source_component <= 255:
            raise ValueError("MAVLink kaynak system/component 0..255 aralığında olmalı")
        if self.operation_timeout <= 0:
            raise ValueError("MAVLink işlem zaman aşımı pozitif olmalı")


@dataclass(slots=True)
class MissionDefaults:
    radii: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RADII))
    default_altitude_m: int = 100

    def __post_init__(self) -> None:
        self.radii = {key: int(value) for key, value in self.radii.items()}
        self.default_altitude_m = int(self.default_altitude_m)
        invalid = [key for key, value in self.radii.items() if value <= 0]
        if invalid:
            raise ValueError(f"Görev çapları pozitif olmalı: {', '.join(sorted(invalid))}")
        if self.default_altitude_m <= 0:
            raise ValueError("Varsayılan görev irtifası pozitif olmalı")

    def radius_for(self, item_type: str, fallback: int = 35) -> int:
        return int(self.radii.get(item_type, fallback))


@dataclass(slots=True)
class BatteryAlarmSettings:
    warning_mode: str = "percent"
    warning_threshold: float = 20.0
    failsafe_enabled: bool = False
    failsafe_mode: str = "percent"
    failsafe_threshold: float = 10.0

    def __post_init__(self) -> None:
        self.failsafe_enabled = bool(self.failsafe_enabled)
        self.warning_mode = self._validate_threshold(
            self.warning_mode,
            self.warning_threshold,
            "uyarı",
        )
        self.warning_threshold = float(self.warning_threshold)
        self.failsafe_mode = self._validate_threshold(
            self.failsafe_mode,
            self.failsafe_threshold,
            "failsafe",
        )
        self.failsafe_threshold = float(self.failsafe_threshold)

    @staticmethod
    def _validate_threshold(mode: str, threshold: float, name: str) -> str:
        normalized_mode = str(mode).strip().lower()
        normalized_threshold = float(threshold)
        if normalized_mode not in {"percent", "voltage"}:
            raise ValueError(
                f"Desteklenmeyen batarya {name} birimi: {normalized_mode}"
            )
        if normalized_threshold <= 0.0:
            raise ValueError(f"Batarya {name} eşiği pozitif olmalı")
        if normalized_threshold > 100.0:
            suffix = "%" if normalized_mode == "percent" else "V"
            raise ValueError(
                f"Batarya {name} eşiği 100 {suffix} değerini aşamaz"
            )
        return normalized_mode


@dataclass(slots=True)
class BackendSettings:
    mock_mode: bool = False
    video_auto_connect: bool = False
    video_source_type: str = "udp"
    video_source_uri: str = ""
    video_codec: str = "h265"
    video_latency_ms: int = 100
    video_frame_timeout_s: int = 3
    cache: CacheSettings = field(default_factory=CacheSettings)
    mavlink: MavlinkSettings = field(default_factory=MavlinkSettings)
    orin: OrinLinkConfig = field(default_factory=OrinLinkConfig)
    competition: CompetitionServerConfig = field(default_factory=CompetitionServerConfig)
    mission: MissionDefaults = field(default_factory=MissionDefaults)
    battery_alarm: BatteryAlarmSettings = field(default_factory=BatteryAlarmSettings)

    def __post_init__(self) -> None:
        self.video_auto_connect = bool(self.video_auto_connect)
        self.video_source_type = self.video_source_type.strip().lower()
        self.video_source_uri = self.video_source_uri.strip()
        self.video_codec = self.video_codec.strip().lower()
        self.video_latency_ms = int(self.video_latency_ms)
        self.video_frame_timeout_s = int(self.video_frame_timeout_s)
        if self.video_source_type not in {
            "mock", "http", "rtsp", "udp", "usb", "file", "pipeline"
        }:
            raise ValueError(f"Desteklenmeyen video kaynağı: {self.video_source_type}")
        if self.video_codec not in {"h264", "h265", "mjpeg"}:
            raise ValueError(f"Desteklenmeyen video codec: {self.video_codec}")
        if not 0 <= self.video_latency_ms <= 10_000:
            raise ValueError("Video gecikmesi 0..10000 ms aralığında olmalı")
        if not 1 <= self.video_frame_timeout_s <= 60:
            raise ValueError("Video kare zaman aşımı 1..60 saniye aralığında olmalı")


@dataclass(slots=True)
class UiSettings:
    language: str = "tr"
    theme: str = "dark"
    map_layer: str = "vector"
    map_center_min_zoom: int = 16
    vehicle_trail_limit: int = 80
    contact_trail_limit: int = 36

    def __post_init__(self) -> None:
        if self.language not in {"tr", "en"}:
            raise ValueError(f"Desteklenmeyen dil: {self.language}")
        if self.theme not in {"dark", "light"}:
            raise ValueError(f"Desteklenmeyen tema: {self.theme}")
        if self.map_layer not in {"vector", "satellite"}:
            raise ValueError(f"Desteklenmeyen harita katmanı: {self.map_layer}")
        if self.map_center_min_zoom < 0:
            raise ValueError("Harita zoom değeri negatif olamaz")
        if self.vehicle_trail_limit <= 0 or self.contact_trail_limit <= 0:
            raise ValueError("İz limitleri pozitif olmalı")


@dataclass(slots=True)
class SettingsModel(Model):
    """Root model keeping backend and UI settings under separate ownership."""

    backend: BackendSettings = field(default_factory=BackendSettings)
    ui: UiSettings = field(default_factory=UiSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SettingsModel":
        if "backend" in payload or "ui" in payload:
            backend_payload = dict(payload.get("backend", {}))
            ui_payload = dict(payload.get("ui", {}))
            cache = CacheSettings(**_known(CacheSettings, backend_payload.pop("cache", {})))
            mavlink = MavlinkSettings(**_known(MavlinkSettings, backend_payload.pop("mavlink", {})))
            orin = OrinLinkConfig(**_known(OrinLinkConfig, backend_payload.pop("orin", {})))
            competition = CompetitionServerConfig(
                **_known(CompetitionServerConfig, backend_payload.pop("competition", {}))
            )
            battery_payload = dict(backend_payload.pop("battery_alarm", {}))
            legacy_mode = battery_payload.pop("mode", None)
            legacy_threshold = battery_payload.pop("threshold", None)
            if legacy_mode is not None:
                battery_payload.setdefault("warning_mode", legacy_mode)
                battery_payload.setdefault("failsafe_mode", legacy_mode)
            if legacy_threshold is not None:
                battery_payload.setdefault(
                    "warning_threshold",
                    legacy_threshold,
                )
                battery_payload.setdefault(
                    "failsafe_threshold",
                    legacy_threshold,
                )
            battery_alarm = BatteryAlarmSettings(
                **_known(BatteryAlarmSettings, battery_payload)
            )
            mission_payload = dict(backend_payload.pop("mission", {}))
            radii = dict(DEFAULT_RADII)
            radii.update({key: int(value) for key, value in mission_payload.get("radii", {}).items()})
            backend = BackendSettings(
                **_known(BackendSettings, backend_payload),
                cache=cache,
                mavlink=mavlink,
                orin=orin,
                competition=competition,
                battery_alarm=battery_alarm,
                mission=MissionDefaults(
                    radii=radii,
                    default_altitude_m=int(mission_payload.get("default_altitude_m", 100)),
                ),
            )
            return cls(backend=backend, ui=UiSettings(**_known(UiSettings, ui_payload)))
        return cls._from_legacy(payload)

    @classmethod
    def _from_legacy(cls, payload: dict[str, Any]) -> "SettingsModel":
        radii = dict(DEFAULT_RADII)
        for item_type in DEFAULT_RADII:
            legacy_key = f"default_radius_{item_type}"
            if legacy_key in payload:
                radii[item_type] = int(payload[legacy_key])
        return cls(
            backend=BackendSettings(
                mock_mode=bool(payload.get("mock_mode", False)),
                video_auto_connect=bool(payload.get("video_auto_connect", False)),
                video_source_type=str(payload.get("video_source_type", "udp")),
                video_source_uri=str(payload.get("video_source_uri", "")),
                video_codec=str(payload.get("video_codec", "h265")),
                video_latency_ms=int(payload.get("video_latency_ms", 100)),
                video_frame_timeout_s=int(payload.get("video_frame_timeout_s", 3)),
                cache=CacheSettings(
                    enabled=bool(payload.get("cache_enabled", True)),
                    limit_mb=int(payload.get("cache_limit_mb", 2048)),
                    path=str(payload.get("cache_path", "")),
                ),
                mavlink=MavlinkSettings(
                    auto_connect=bool(payload.get("auto_connect", True)),
                ),
                mission=MissionDefaults(radii=radii),
            ),
            ui=UiSettings(
                language=str(payload.get("language", "tr")),
                theme=str(payload.get("theme", "dark")),
                map_layer=str(payload.get("map_layer", "vector")),
                map_center_min_zoom=int(payload.get("map_center_min_zoom", 16)),
                vehicle_trail_limit=int(payload.get("vehicle_trail_limit", 80)),
                contact_trail_limit=int(payload.get("contact_trail_limit", 36)),
            ),
        )

    @classmethod
    def default(cls, base_dir: Path) -> "SettingsModel":
        model = cls()
        model.backend.cache.path = str(base_dir / "data" / "map-cache")
        return model

    def value_at(self, path: str) -> Any:
        value: Any = self
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else getattr(value, part)
        return value

    def with_value(self, path: str, value: Any) -> "SettingsModel":
        payload = self.to_dict()
        target: dict[str, Any] = payload
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
        return self.from_dict(payload)

    def patch(self, **changes: Any) -> "SettingsModel":
        result = self
        for legacy_name, value in changes.items():
            result = result.with_value(_LEGACY_PATHS.get(legacy_name, legacy_name), value)
        return result

    @property
    def language(self) -> str: return self.ui.language
    @property
    def theme(self) -> str: return self.ui.theme
    @property
    def map_layer(self) -> str: return self.ui.map_layer
    @property
    def map_center_min_zoom(self) -> int: return self.ui.map_center_min_zoom
    @property
    def vehicle_trail_limit(self) -> int: return self.ui.vehicle_trail_limit
    @property
    def contact_trail_limit(self) -> int: return self.ui.contact_trail_limit
    @property
    def mock_mode(self) -> bool: return self.backend.mock_mode
    @property
    def video_auto_connect(self) -> bool: return self.backend.video_auto_connect
    @property
    def video_source_type(self) -> str: return self.backend.video_source_type
    @property
    def video_source_uri(self) -> str: return self.backend.video_source_uri
    @property
    def video_codec(self) -> str: return self.backend.video_codec
    @property
    def video_latency_ms(self) -> int: return self.backend.video_latency_ms
    @property
    def video_frame_timeout_s(self) -> int: return self.backend.video_frame_timeout_s
    @property
    def cache_enabled(self) -> bool: return self.backend.cache.enabled
    @property
    def cache_limit_mb(self) -> int: return self.backend.cache.limit_mb
    @property
    def cache_path(self) -> str: return self.backend.cache.path
    @property
    def default_radius_takeoff(self) -> int: return self.backend.mission.radius_for("takeoff")
    @property
    def default_radius_waypoint(self) -> int: return self.backend.mission.radius_for("waypoint")
    @property
    def default_radius_loiter(self) -> int: return self.backend.mission.radius_for("loiter")
    @property
    def default_radius_rtl(self) -> int: return self.backend.mission.radius_for("rtl")
    @property
    def default_radius_land(self) -> int: return self.backend.mission.radius_for("land")
    @property
    def default_radius_hss(self) -> int: return self.backend.mission.radius_for("hss")


def _known(model_type: type, payload: dict[str, Any]) -> dict[str, Any]:
    names = {item.name for item in fields(model_type)}
    return {key: value for key, value in payload.items() if key in names}


_LEGACY_PATHS = {
    "language": "ui.language",
    "theme": "ui.theme",
    "map_layer": "ui.map_layer",
    "map_center_min_zoom": "ui.map_center_min_zoom",
    "vehicle_trail_limit": "ui.vehicle_trail_limit",
    "contact_trail_limit": "ui.contact_trail_limit",
    "mock_mode": "backend.mock_mode",
    "video_auto_connect": "backend.video_auto_connect",
    "video_source_type": "backend.video_source_type",
    "video_source_uri": "backend.video_source_uri",
    "video_codec": "backend.video_codec",
    "video_latency_ms": "backend.video_latency_ms",
    "video_frame_timeout_s": "backend.video_frame_timeout_s",
    "cache_enabled": "backend.cache.enabled",
    "cache_limit_mb": "backend.cache.limit_mb",
    "cache_path": "backend.cache.path",
    **{
        f"default_radius_{item_type}": f"backend.mission.radii.{item_type}"
        for item_type in DEFAULT_RADII
    },
}
