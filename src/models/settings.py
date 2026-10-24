from __future__ import annotations
from copy import deepcopy

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.abstracts import Model


DEFAULT_RADII = {
    "takeoff": 35,
    "waypoint": 35,
    "loiter": 75,
    "rtl": 75,
    "land": 35,
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
        identifier, name, endpoint = (
            self.identifier.strip(),
            self.name.strip(),
            self.endpoint.strip(),
        )
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
class MissionDefaults:
    radii: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RADII))
    default_altitude_m: int = 100

    def __post_init__(self) -> None:
        self.radii = {key: int(value) for key, value in self.radii.items()}
        self.default_altitude_m = int(self.default_altitude_m)
        invalid = [key for key, value in self.radii.items() if value <= 0]
        if invalid:
            raise ValueError(
                f"Görev çapları pozitif olmalı: {', '.join(sorted(invalid))}"
            )
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
            raise ValueError(f"Desteklenmeyen batarya {name} birimi: {normalized_mode}")
        if normalized_threshold <= 0.0:
            raise ValueError(f"Batarya {name} eşiği pozitif olmalı")
        if normalized_threshold > 100.0:
            suffix = "%" if normalized_mode == "percent" else "V"
            raise ValueError(f"Batarya {name} eşiği 100 {suffix} değerini aşamaz")
        return normalized_mode


@dataclass(slots=True)
class BatteryTelemetrySettings:
    """Battery display source and 4S voltage-estimation calibration."""

    use_pixhawk_percent: bool = False
    cell_count: int = 4
    full_voltage: float = 16.8
    reserve_voltage: float = 14.8
    reserve_percent: float = 10.0
    median_window: int = 7
    discharge_filter_seconds: float = 15.0
    recovery_filter_seconds: float = 2.0

    def __post_init__(self) -> None:
        self.use_pixhawk_percent = bool(self.use_pixhawk_percent)
        self.cell_count = int(self.cell_count)
        self.full_voltage = float(self.full_voltage)
        self.reserve_voltage = float(self.reserve_voltage)
        self.reserve_percent = float(self.reserve_percent)
        self.median_window = int(self.median_window)
        self.discharge_filter_seconds = float(self.discharge_filter_seconds)
        self.recovery_filter_seconds = float(self.recovery_filter_seconds)
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
        return (self.reserve_voltage - reserve_fraction * self.full_voltage) / (
            1.0 - reserve_fraction
        )


@dataclass(slots=True)
class BackendSettings:
    mock_mode: bool = False
    cache: CacheSettings = field(default_factory=CacheSettings)
    mavlink: MavlinkSettings = field(default_factory=MavlinkSettings)
    mavlink_connections: list[MavlinkConnectionConfig] | None = None
    active_mavlink_connection_id: str = ""
    mission: MissionDefaults = field(default_factory=MissionDefaults)
    battery_alarm: BatteryAlarmSettings = field(default_factory=BatteryAlarmSettings)
    battery_telemetry: BatteryTelemetrySettings = field(
        default_factory=BatteryTelemetrySettings
    )

    def __post_init__(self) -> None:
        if self.mavlink_connections is None:
            self.mavlink_connections = [
                MavlinkConnectionConfig.from_mavlink(self.mavlink)
            ]
        else:
            self.mavlink_connections = [
                value
                if isinstance(value, MavlinkConnectionConfig)
                else MavlinkConnectionConfig(**value)
                for value in self.mavlink_connections
            ]
        identifiers = [profile.identifier for profile in self.mavlink_connections]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Telemetri profil kimlikleri benzersiz olmalı")
        if not self.mavlink_connections:
            self.active_mavlink_connection_id = ""
            self.mavlink.auto_connect = False
            return
        selected = next(
            (
                profile
                for profile in self.mavlink_connections
                if profile.identifier == self.active_mavlink_connection_id
            ),
            self.mavlink_connections[0],
        )
        self.active_mavlink_connection_id = selected.identifier
        self.mavlink = selected.to_mavlink(self.mavlink)


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
    extensions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SettingsModel":
        if not isinstance(payload, dict) or not {
            "backend",
            "ui",
            "extensions",
        }.issubset(payload):
            raise ValueError("Ayarlar güncel backend/ui/extensions biçiminde olmalı")
        if not all(
            isinstance(payload[key], dict) for key in ("backend", "ui", "extensions")
        ):
            raise ValueError("backend, ui ve extensions alanları nesne olmalı")
        backend_payload = dict(payload["backend"])
        ui_payload = dict(payload["ui"])
        if "mavlink_connections" not in backend_payload:
            raise ValueError("backend.mavlink_connections alanı gerekli")
        if "mavlink" not in backend_payload:
            raise ValueError("backend.mavlink alanı gerekli")
        if "cache" not in backend_payload:
            raise ValueError("backend.cache alanı gerekli")
        if "mission" not in backend_payload:
            raise ValueError("backend.mission alanı gerekli")
        if "battery_alarm" not in backend_payload:
            raise ValueError("backend.battery_alarm alanı gerekli")
        if "battery_telemetry" not in backend_payload:
            raise ValueError("backend.battery_telemetry alanı gerekli")
        cache = CacheSettings(
            **_known(CacheSettings, backend_payload.pop("cache", {}))
        )
        mavlink = MavlinkSettings(
            **_known(MavlinkSettings, backend_payload.pop("mavlink", {}))
        )
        mavlink_connections = [
            MavlinkConnectionConfig(**dict(item))
            for item in backend_payload.pop("mavlink_connections", ())
        ]
        active_mavlink_connection_id = str(
            backend_payload.pop("active_mavlink_connection_id", "")
        )
        battery_payload = dict(backend_payload.pop("battery_alarm", {}))
        battery_alarm = BatteryAlarmSettings(
            **_known(BatteryAlarmSettings, battery_payload)
        )
        battery_telemetry_payload = dict(
            backend_payload.pop("battery_telemetry", {})
        )
        battery_telemetry = BatteryTelemetrySettings(
            **_known(BatteryTelemetrySettings, battery_telemetry_payload)
        )
        mission_payload = dict(backend_payload.pop("mission", {}))
        radii = dict(DEFAULT_RADII)
        radii.update(
            {
                key: int(value)
                for key, value in mission_payload.get("radii", {}).items()
            }
        )
        backend = BackendSettings(
            **_known(BackendSettings, backend_payload),
            cache=cache,
            mavlink=mavlink,
            mavlink_connections=mavlink_connections,
            active_mavlink_connection_id=active_mavlink_connection_id,
            battery_alarm=battery_alarm,
            battery_telemetry=battery_telemetry,
            mission=MissionDefaults(
                radii=radii,
                default_altitude_m=int(
                    mission_payload.get("default_altitude_m", 100)
                ),
            ),
        )
        return cls(
            backend=backend,
            ui=UiSettings(**_known(UiSettings, ui_payload)),
            extensions=deepcopy(payload["extensions"]),
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
        return self.from_dict(payload)

    def patch(self, **changes: Any) -> "SettingsModel":
        result = self
        for path, value in changes.items():
            result = result.with_value(path, value)
        return result


def _known(model_type: type, payload: dict[str, Any]) -> dict[str, Any]:
    names = {item.name for item in fields(model_type)}
    return {key: value for key, value in payload.items() if key in names}
