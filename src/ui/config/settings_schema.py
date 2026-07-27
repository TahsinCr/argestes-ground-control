from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class ChoiceSpec:
    value: str
    label_key: str


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Describe a supported field without changing dialog code."""

    path: str
    label_key: str
    kind: Literal["combo", "check", "spin", "double", "text"]
    choices: tuple[ChoiceSpec, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    suffix: str = ""
    decimals: int = 1
    single_step: float = 0.1


@dataclass(frozen=True, slots=True)
class SectionSpec:
    title_key: str
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)


SETTINGS_SECTIONS = (
    SectionSpec(
        "dialog.settings.section.ui",
        (
            FieldSpec("ui.language", "dialog.settings.language", "combo", (
                ChoiceSpec("tr", "language.tr"), ChoiceSpec("en", "language.en")
            )),
            FieldSpec("ui.theme", "dialog.settings.theme", "combo", (
                ChoiceSpec("dark", "theme.dark"), ChoiceSpec("light", "theme.light")
            )),
        ),
    ),
    SectionSpec(
        "dialog.settings.section.map",
        (
            FieldSpec("ui.map_layer", "dialog.settings.map_layer", "combo", (
                ChoiceSpec("vector", "map.layer.vector"),
                ChoiceSpec("satellite", "map.layer.satellite"),
            )),
            FieldSpec("ui.map_center_min_zoom", "dialog.settings.center_zoom", "spin", minimum=10, maximum=21),
            FieldSpec("ui.vehicle_trail_limit", "dialog.settings.vehicle_trail", "spin", minimum=10, maximum=300),
            FieldSpec("ui.contact_trail_limit", "dialog.settings.contact_trail", "spin", minimum=10, maximum=160),
        ),
    ),
    SectionSpec(
        "dialog.settings.section.backend",
        (
            FieldSpec("backend.mock_mode", "dialog.settings.mock", "check"),
            FieldSpec("backend.cache.enabled", "dialog.settings.cache", "check"),
            FieldSpec("backend.cache.limit_mb", "dialog.settings.cache_limit", "spin", minimum=256, maximum=8192, suffix=" MB"),
            FieldSpec("backend.cache.path", "dialog.settings.cache_path", "text"),
        ),
    ),
    SectionSpec(
        "dialog.settings.section.competition",
        (
            FieldSpec("backend.competition.base_url", "dialog.settings.competition_url", "text"),
            FieldSpec("backend.competition.username", "dialog.settings.competition_username", "text"),
            FieldSpec("backend.competition.password", "dialog.settings.competition_password", "text"),
            FieldSpec("backend.competition.team_number", "dialog.settings.competition_team", "spin", minimum=1, maximum=9999),
            FieldSpec("backend.competition.timeout_s", "dialog.settings.competition_timeout", "spin", minimum=1, maximum=120, suffix=" s"),
            FieldSpec("backend.competition.telemetry_period_ms", "dialog.settings.competition_period", "spin", minimum=500, maximum=60000, suffix=" ms"),
            FieldSpec("backend.competition.auto_connect", "dialog.settings.competition_auto_connect", "check"),
        ),
    ),
    SectionSpec(
        "dialog.settings.section.mavlink",
        (
            FieldSpec("backend.mavlink.endpoint", "dialog.settings.mavlink_endpoint", "text"),
            FieldSpec("backend.mavlink.baud", "dialog.settings.mavlink_baud", "spin", minimum=1200, maximum=3000000),
            FieldSpec("backend.mavlink.heartbeat_timeout", "dialog.settings.mavlink_timeout", "spin", minimum=1, maximum=120, suffix=" s"),
            FieldSpec("backend.mavlink.operation_timeout", "dialog.settings.mavlink_operation_timeout", "spin", minimum=1, maximum=120, suffix=" s"),
            FieldSpec("backend.mavlink.dialect", "dialog.settings.mavlink_dialect", "combo", (
                ChoiceSpec("ardupilotmega", "mavlink.dialect.ardupilotmega"),
                ChoiceSpec("common", "mavlink.dialect.common"),
            )),
            FieldSpec("backend.mavlink.source_system", "dialog.settings.mavlink_source_system", "spin", minimum=0, maximum=255),
            FieldSpec("backend.mavlink.source_component", "dialog.settings.mavlink_source_component", "spin", minimum=0, maximum=255),
            FieldSpec("backend.mavlink.autoreconnect", "dialog.settings.mavlink_autoreconnect", "check"),
            FieldSpec("backend.mavlink.sync_parameters_on_connect", "dialog.settings.mavlink_sync_parameters", "check"),
            FieldSpec("backend.mavlink.sync_mission_on_connect", "dialog.settings.mavlink_sync_mission", "check"),
            FieldSpec("backend.mavlink.auto_connect", "dialog.settings.mavlink_auto_connect", "check"),
        ),
    ),
    SectionSpec(
        "dialog.settings.section.battery_alarm",
        (
            FieldSpec(
                "backend.battery_alarm.warning_mode",
                "dialog.settings.battery_warning_mode",
                "combo",
                (
                    ChoiceSpec("percent", "battery.alarm.mode.percent"),
                    ChoiceSpec("voltage", "battery.alarm.mode.voltage"),
                ),
            ),
            FieldSpec(
                "backend.battery_alarm.warning_threshold",
                "dialog.settings.battery_warning_threshold",
                "double",
                minimum=0.1,
                maximum=100.0,
                decimals=1,
                single_step=0.1,
            ),
            FieldSpec(
                "backend.battery_alarm.failsafe_enabled",
                "dialog.settings.battery_failsafe_enabled",
                "check",
            ),
            FieldSpec(
                "backend.battery_alarm.failsafe_mode",
                "dialog.settings.battery_failsafe_mode",
                "combo",
                (
                    ChoiceSpec("percent", "battery.alarm.mode.percent"),
                    ChoiceSpec("voltage", "battery.alarm.mode.voltage"),
                ),
            ),
            FieldSpec(
                "backend.battery_alarm.failsafe_threshold",
                "dialog.settings.battery_failsafe_threshold",
                "double",
                minimum=0.1,
                maximum=100.0,
                decimals=1,
                single_step=0.1,
            ),
        ),
    ),
    SectionSpec(
        "dialog.settings.section.mission",
        (
            FieldSpec(
                "backend.mission.default_altitude_m",
                "dialog.settings.mission_altitude",
                "spin",
                minimum=10,
                maximum=10000,
                suffix=" m",
            ),
            *tuple(
            FieldSpec(
                f"backend.mission.radii.{item_type}",
                f"dialog.settings.radius_{item_type}",
                "spin",
                minimum=5,
                maximum=3000,
                suffix=" m",
            )
            for item_type in ("takeoff", "waypoint", "loiter", "rtl", "land", "hss")
            ),
        ),
    ),
    SectionSpec(
        "dialog.settings.section.orin",
        (
            FieldSpec("backend.orin.enabled", "dialog.settings.orin_enabled", "check"),
            FieldSpec("backend.orin.component_id", "dialog.settings.orin_component", "spin", minimum=1, maximum=255),
            FieldSpec("backend.orin.network_id", "dialog.settings.orin_network", "spin", minimum=0, maximum=255),
            FieldSpec("backend.orin.message_type", "dialog.settings.orin_message_type", "spin", minimum=32768, maximum=65535),
            FieldSpec("backend.orin.heartbeat_timeout_s", "dialog.settings.orin_heartbeat_timeout", "spin", minimum=3, maximum=120, suffix=" s"),
            FieldSpec("backend.orin.command_timeout_s", "dialog.settings.orin_command_timeout", "spin", minimum=1, maximum=120, suffix=" s"),
        ),
    ),
    SectionSpec(
        "dialog.settings.section.video",
        (
            FieldSpec(
                "backend.video_auto_connect",
                "dialog.settings.video_auto_connect",
                "check",
            ),
            FieldSpec("backend.video_source_type", "dialog.settings.video_type", "combo", (
                ChoiceSpec("mock", "video.source.mock"),
                ChoiceSpec("http", "video.source.http"),
                ChoiceSpec("rtsp", "video.source.rtsp"),
                ChoiceSpec("udp", "video.source.udp"),
                ChoiceSpec("usb", "video.source.usb"),
            )),
            FieldSpec("backend.video_codec", "dialog.settings.video_codec", "combo", (
                ChoiceSpec("h265", "video.codec.h265"),
                ChoiceSpec("h264", "video.codec.h264"),
                ChoiceSpec("mjpeg", "video.codec.mjpeg"),
            )),
            FieldSpec("backend.video_source_uri", "dialog.settings.video_uri", "text"),
            FieldSpec("backend.video_latency_ms", "dialog.settings.video_latency", "spin", minimum=0, maximum=10000, suffix=" ms"),
            FieldSpec("backend.video_frame_timeout_s", "dialog.settings.video_timeout", "spin", minimum=1, maximum=60, suffix=" s"),
        ),
    ),
)


def iter_fields():
    for section in SETTINGS_SECTIONS:
        yield from section.fields
