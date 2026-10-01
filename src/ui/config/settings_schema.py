from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class ChoiceSpec:
    value: str
    label_key: str


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Describe a supported user setting field."""

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
        "dialog.settings.section.battery_alarm",
        (
            FieldSpec("backend.battery_telemetry.use_pixhawk_percent", "dialog.settings.battery_use_pixhawk_percent", "check"),
            FieldSpec("backend.battery_telemetry.full_voltage", "dialog.settings.battery_estimate_full_voltage", "double", minimum=1.0, maximum=100.0, suffix=" V", decimals=2),
            FieldSpec("backend.battery_telemetry.reserve_voltage", "dialog.settings.battery_estimate_min_voltage", "double", minimum=1.0, maximum=100.0, suffix=" V", decimals=2),
            FieldSpec("backend.battery_telemetry.reserve_percent", "dialog.settings.battery_estimate_min_percent", "double", minimum=0.1, maximum=99.0, suffix=" %", decimals=1, single_step=1.0),
            FieldSpec("backend.battery_alarm.warning_mode", "dialog.settings.battery_warning_mode", "combo", (ChoiceSpec("percent", "battery.alarm.mode.percent"), ChoiceSpec("voltage", "battery.alarm.mode.voltage"))),
            FieldSpec("backend.battery_alarm.warning_threshold", "dialog.settings.battery_warning_threshold", "double", minimum=0.1, maximum=100.0),
            FieldSpec("backend.battery_alarm.failsafe_enabled", "dialog.settings.battery_failsafe_enabled", "check"),
            FieldSpec("backend.battery_alarm.failsafe_mode", "dialog.settings.battery_failsafe_mode", "combo", (ChoiceSpec("percent", "battery.alarm.mode.percent"), ChoiceSpec("voltage", "battery.alarm.mode.voltage"))),
            FieldSpec("backend.battery_alarm.failsafe_threshold", "dialog.settings.battery_failsafe_threshold", "double", minimum=0.1, maximum=100.0),
        ),
    ),
)


def iter_fields():
    for section in SETTINGS_SECTIONS:
        yield from section.fields
