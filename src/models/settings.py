from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.abstracts import Model
from src.models.competition import CompetitionServerConfig
from src.models.joystick import JoystickSettings
from src.models.orin import OrinLinkConfig
from src.models.video import VideoShareConfig, VideoSourceConfig


DEFAULT_RADII = {
    "takeoff": 35,
    "waypoint": 35,
    "loiter": 75,
    "rtl": 75,
    "land": 35,
    "hss": 100,
}
SAFE_AREA_POINT_KEYS = ("point_1", "point_2", "point_3", "point_4")


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


@dataclass(frozen=True, slots=True)
class MavlinkConnectionConfig(Model):
    """A named, selectable MAVLink transport profile.

    Only one profile is applied to ``BackendSettings.mavlink`` at a time, so
    the existing single-client MAVLink ownership model remains intact.
    """

    identifier: str = field(default_factory=lambda: uuid4().hex)
    name: str = "Telemetri"
    endpoint: str = "udp:127.0.0.1:14550"
    baud: int = 115200
    heartbeat_timeout: int = 10
    auto_connect: bool = False

    def __post_init__(self) -> None:
        identifier, name, endpoint = self.identifier.strip(), self.name.strip(), self.endpoint.strip()
        if not identifier or not name or not endpoint:
            raise ValueError("Telemetri profil kimliği, adı ve adresi boş olamaz")
        if int(self.baud) <= 0 or int(self.heartbeat_timeout) <= 0:
            raise ValueError("Telemetri profil baud ve zaman aşımı pozitif olmalı")
        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "baud", int(self.baud))
        object.__setattr__(self, "heartbeat_timeout", int(self.heartbeat_timeout))
        object.__setattr__(self, "auto_connect", bool(self.auto_connect))

    @classmethod
    def from_mavlink(cls, mavlink: MavlinkSettings) -> "MavlinkConnectionConfig":
        return cls(
            name="Telemetri",
            endpoint=mavlink.endpoint,
            baud=mavlink.baud,
            heartbeat_timeout=mavlink.heartbeat_timeout,
            auto_connect=mavlink.auto_connect,
        )

    def to_mavlink(self, current: MavlinkSettings) -> MavlinkSettings:
        return MavlinkSettings(
            endpoint=self.endpoint,
            baud=self.baud,
            heartbeat_timeout=self.heartbeat_timeout,
            auto_connect=self.auto_connect,
            dialect=current.dialect,
            source_system=current.source_system,
            source_component=current.source_component,
            autoreconnect=current.autoreconnect,
            operation_timeout=current.operation_timeout,
            sync_parameters_on_connect=current.sync_parameters_on_connect,
            sync_mission_on_connect=current.sync_mission_on_connect,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SafeAreaPoint:
    """One two-dimensional safe-area corner; altitude is intentionally absent."""

    latitude: float = 0.0
    longitude: float = 0.0

    def __post_init__(self) -> None:
        self.latitude = float(self.latitude)
        self.longitude = float(self.longitude)
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError("Güvenli alan enlemi -90..90 aralığında olmalı")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError("Güvenli alan boylamı -180..180 aralığında olmalı")


@dataclass(slots=True)
class SafeAreaSettings:
    point_1: SafeAreaPoint = field(default_factory=SafeAreaPoint)
    point_2: SafeAreaPoint = field(default_factory=SafeAreaPoint)
    point_3: SafeAreaPoint = field(default_factory=SafeAreaPoint)
    point_4: SafeAreaPoint = field(default_factory=SafeAreaPoint)

    @property
    def corners(self) -> tuple[SafeAreaPoint, ...]:
        return tuple(getattr(self, key) for key in SAFE_AREA_POINT_KEYS)

    def ordered_corners(self) -> tuple[SafeAreaPoint, ...]:
        """Return the four corners in perimeter order without changing their labels.

        Settings intentionally keep the operator's Point 1..4 inputs intact.
        The map receives a centroid-angle ordering instead, so a rectangle is
        rendered as a simple polygon even when its corners were entered in an
        arbitrary order.
        """

        corners = self.corners
        coordinates = {(corner.latitude, corner.longitude) for corner in corners}
        if len(coordinates) != len(corners):
            return ()
        center_latitude = sum(corner.latitude for corner in corners) / len(corners)
        center_longitude = sum(corner.longitude for corner in corners) / len(corners)
        longitude_scale = math.cos(math.radians(center_latitude))
        return tuple(
            sorted(
                corners,
                key=lambda corner: math.atan2(
                    corner.latitude - center_latitude,
                    (corner.longitude - center_longitude) * longitude_scale,
                ),
            )
        )


@dataclass(slots=True)
class MissionDefaults:
    radii: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RADII))
    default_altitude_m: int = 100
    safe_area: SafeAreaSettings = field(default_factory=SafeAreaSettings)

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
class BatteryTelemetrySettings:
    """Battery display source and 4S voltage-estimation calibration."""

    use_pixhawk_percent: bool | None = None
    mode: str = "voltage"
    cell_count: int = 4
    full_voltage: float = 16.8
    reserve_voltage: float = 14.8
    reserve_percent: float = 10.0
    median_window: int = 7
    discharge_filter_seconds: float = 15.0
    recovery_filter_seconds: float = 2.0

    def __post_init__(self) -> None:
        self.mode = str(self.mode).strip().lower()
        self.cell_count = int(self.cell_count)
        self.full_voltage = float(self.full_voltage)
        self.reserve_voltage = float(self.reserve_voltage)
        self.reserve_percent = float(self.reserve_percent)
        self.median_window = int(self.median_window)
        self.discharge_filter_seconds = float(self.discharge_filter_seconds)
        self.recovery_filter_seconds = float(self.recovery_filter_seconds)
        if self.mode not in {"voltage", "autopilot"}:
            raise ValueError(f"Desteklenmeyen batarya telemetri modu: {self.mode}")
        # ``mode`` remains in persisted settings for backward compatibility.
        # The boolean is the user-facing and authoritative percentage source.
        if self.use_pixhawk_percent is None:
            self.use_pixhawk_percent = self.mode == "autopilot"
        else:
            self.use_pixhawk_percent = bool(self.use_pixhawk_percent)
        self.mode = "autopilot" if self.use_pixhawk_percent else "voltage"
        if self.cell_count <= 0:
            raise ValueError("Batarya hücre sayısı pozitif olmalı")
        if not 0.0 < self.reserve_percent < 100.0:
            raise ValueError("Batarya rezerv yüzdesi 0..100 aralığında olmalı")
        if not 0.0 < self.reserve_voltage < self.full_voltage:
            raise ValueError("Batarya rezerv voltajı tam dolu voltajından düşük olmalı")
        if self.empty_voltage <= 0.0:
            raise ValueError("Hesaplanan minimum batarya voltajı pozitif olmalı")
        if self.median_window < 3 or self.median_window % 2 == 0:
            raise ValueError("Batarya medyan penceresi tek ve en az 3 olmalı")
        if self.discharge_filter_seconds <= 0.0 or self.recovery_filter_seconds <= 0.0:
            raise ValueError("Batarya filtre süreleri pozitif olmalı")

    @property
    def empty_voltage(self) -> float:
        reserve_fraction = self.reserve_percent / 100.0
        return (
            self.reserve_voltage - reserve_fraction * self.full_voltage
        ) / (1.0 - reserve_fraction)


@dataclass(slots=True)
class BackendSettings:
    mock_mode: bool = False
    video_auto_connect: bool = False
    video_source_type: str = "udp"
    video_source_uri: str = ""
    video_codec: str = "h265"
    video_latency_ms: int = 100
    video_frame_timeout_s: int = 3
    video_sources: list[VideoSourceConfig] = field(default_factory=list)
    active_video_source_id: str = ""
    video_shares: list[VideoShareConfig] = field(default_factory=list)
    competition_servers: list[CompetitionServerConfig] = field(default_factory=list)
    cache: CacheSettings = field(default_factory=CacheSettings)
    mavlink: MavlinkSettings = field(default_factory=MavlinkSettings)
    mavlink_connections: list[MavlinkConnectionConfig] = field(default_factory=list)
    active_mavlink_connection_id: str = ""
    orin: OrinLinkConfig = field(default_factory=OrinLinkConfig)
    competition: CompetitionServerConfig = field(default_factory=CompetitionServerConfig)
    mission: MissionDefaults = field(default_factory=MissionDefaults)
    battery_alarm: BatteryAlarmSettings = field(default_factory=BatteryAlarmSettings)
    battery_telemetry: BatteryTelemetrySettings = field(
        default_factory=BatteryTelemetrySettings
    )
    joystick: JoystickSettings = field(default_factory=JoystickSettings)

    def __post_init__(self) -> None:
        self.video_auto_connect = bool(self.video_auto_connect)
        self.video_source_type = self.video_source_type.strip().lower()
        self.video_source_uri = self.video_source_uri.strip()
        self.video_codec = self.video_codec.strip().lower()
        self.video_latency_ms = int(self.video_latency_ms)
        self.video_frame_timeout_s = int(self.video_frame_timeout_s)
        self.video_sources = [
            value if isinstance(value, VideoSourceConfig) else VideoSourceConfig(**value)
            for value in self.video_sources
        ]
        if not self.video_sources:
            self.video_sources = [
                VideoSourceConfig(
                    identifier="video-primary",
                    name="Kamera 1",
                    source_type=self.video_source_type,
                    source_uri=self.video_source_uri,
                    codec=self.video_codec,
                    latency_ms=self.video_latency_ms,
                    frame_timeout_s=self.video_frame_timeout_s,
                    auto_connect=self.video_auto_connect,
                )
            ]
        source_identifiers = [source.identifier for source in self.video_sources]
        if len(source_identifiers) != len(set(source_identifiers)):
            raise ValueError("Video bağlantı kimlikleri benzersiz olmalı")
        active_source = next(
            (source for source in self.video_sources if source.identifier == self.active_video_source_id),
            self.video_sources[0],
        )
        self.active_video_source_id = active_source.identifier
        # Legacy fields remain a backwards-compatible view of the active source.
        self.video_auto_connect = active_source.auto_connect
        self.video_source_type = active_source.source_type
        self.video_source_uri = active_source.source_uri
        self.video_codec = active_source.codec
        self.video_latency_ms = active_source.latency_ms
        self.video_frame_timeout_s = int(active_source.frame_timeout_s)
        self.video_shares = [
            value if isinstance(value, VideoShareConfig) else VideoShareConfig(**value)
            for value in self.video_shares
        ]
        self.mavlink_connections = [
            value if isinstance(value, MavlinkConnectionConfig) else MavlinkConnectionConfig(**value)
            for value in self.mavlink_connections
        ]
        if not self.mavlink_connections:
            self.mavlink_connections = [MavlinkConnectionConfig.from_mavlink(self.mavlink)]
        identifiers = [profile.identifier for profile in self.mavlink_connections]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Telemetri profil kimlikleri benzersiz olmalı")
        selected = next(
            (profile for profile in self.mavlink_connections if profile.identifier == self.active_mavlink_connection_id),
            self.mavlink_connections[0],
        )
        self.active_mavlink_connection_id = selected.identifier
        self.mavlink = selected.to_mavlink(self.mavlink)
        identifiers = [share.identifier for share in self.video_shares]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Video paylaşım kimlikleri benzersiz olmalı")
        self.competition_servers = [
            value if isinstance(value, CompetitionServerConfig) else CompetitionServerConfig(**value)
            for value in self.competition_servers
        ]
        if not self.competition_servers:
            self.competition_servers = [self.competition]
        server_identifiers = [server.identifier for server in self.competition_servers]
        if len(server_identifiers) != len(set(server_identifiers)):
            raise ValueError("TEKNOFEST sunucu kimlikleri benzersiz olmalı")
        if not any(server.identifier == self.competition.identifier for server in self.competition_servers):
            self.competition = self.competition_servers[0]
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
            mavlink_connections = [
                MavlinkConnectionConfig(**dict(item))
                for item in backend_payload.pop("mavlink_connections", ())
            ]
            active_mavlink_connection_id = str(backend_payload.pop("active_mavlink_connection_id", ""))
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
            battery_telemetry_payload = dict(
                backend_payload.pop("battery_telemetry", {})
            )
            # Migrate the former string mode without changing existing profiles.
            battery_telemetry_payload.setdefault(
                "use_pixhawk_percent",
                battery_telemetry_payload.get("mode") == "autopilot",
            )
            battery_telemetry = BatteryTelemetrySettings(
                **_known(BatteryTelemetrySettings, battery_telemetry_payload)
            )
            joystick = JoystickSettings(
                **_known(
                    JoystickSettings,
                    dict(backend_payload.pop("joystick", {})),
                )
            )
            video_shares = [
                VideoShareConfig(**dict(item))
                for item in backend_payload.pop("video_shares", ())
            ]
            video_sources = [
                VideoSourceConfig(**dict(item))
                for item in backend_payload.pop("video_sources", ())
            ]
            active_video_source_id = str(
                backend_payload.pop("active_video_source_id", "")
            )
            competition_servers = [
                CompetitionServerConfig(**dict(item))
                for item in backend_payload.pop("competition_servers", ())
            ]
            mission_payload = dict(backend_payload.pop("mission", {}))
            radii = dict(DEFAULT_RADII)
            radii.update({key: int(value) for key, value in mission_payload.get("radii", {}).items()})
            safe_area_payload = dict(mission_payload.get("safe_area", {}))
            safe_area = SafeAreaSettings(
                **{
                    key: SafeAreaPoint(
                        **_known(
                            SafeAreaPoint,
                            dict(safe_area_payload.get(key, {})),
                        )
                    )
                    for key in SAFE_AREA_POINT_KEYS
                }
            )
            backend = BackendSettings(
                **_known(BackendSettings, backend_payload),
                cache=cache,
                mavlink=mavlink,
                mavlink_connections=mavlink_connections,
                active_mavlink_connection_id=active_mavlink_connection_id,
                orin=orin,
                competition=competition,
                competition_servers=competition_servers,
                battery_alarm=battery_alarm,
                battery_telemetry=battery_telemetry,
                joystick=joystick,
                video_shares=video_shares,
                video_sources=video_sources,
                active_video_source_id=active_video_source_id,
                mission=MissionDefaults(
                    radii=radii,
                    default_altitude_m=int(mission_payload.get("default_altitude_m", 100)),
                    safe_area=safe_area,
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
                video_shares=[],
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
        if path.startswith("backend.mavlink."):
            backend = payload["backend"]
            active_id = backend.get("active_mavlink_connection_id", "")
            for profile in backend.get("mavlink_connections", []):
                if profile.get("identifier") == active_id:
                    key = parts[-1]
                    if key in {"endpoint", "baud", "heartbeat_timeout", "auto_connect"}:
                        profile[key] = value
                    break
        if path.startswith("backend.video_"):
            backend = payload["backend"]
            active_id = backend.get("active_video_source_id", "")
            field_name = parts[-1]
            source_field = {
                "video_auto_connect": "auto_connect",
                "video_source_type": "source_type",
                "video_source_uri": "source_uri",
                "video_codec": "codec",
                "video_latency_ms": "latency_ms",
                "video_frame_timeout_s": "frame_timeout_s",
            }.get(field_name)
            if source_field:
                for source in backend.get("video_sources", []):
                    if source.get("identifier") == active_id:
                        source[source_field] = value
                        break
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
