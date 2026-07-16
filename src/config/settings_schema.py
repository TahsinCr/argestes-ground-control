from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class ChoiceSpec:
    value: str
    label_key: str


@dataclass(frozen=True, slots=True)
class FieldSpec:
    key: str
    label_key: str
    kind: Literal["combo", "check", "spin", "text"]
    choices: tuple[ChoiceSpec, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    suffix: str = ""


@dataclass(frozen=True, slots=True)
class SectionSpec:
    title_key: str
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)


SETTINGS_SECTIONS = (
    SectionSpec(
        title_key="dialog.settings.section.general",
        fields=(
            FieldSpec(
                key="language",
                label_key="dialog.settings.language",
                kind="combo",
                choices=(
                    ChoiceSpec("tr", "language.tr"),
                    ChoiceSpec("en", "language.en"),
                ),
            ),
            FieldSpec(
                key="theme",
                label_key="dialog.settings.theme",
                kind="combo",
                choices=(
                    ChoiceSpec("dark", "theme.dark"),
                    ChoiceSpec("light", "theme.light"),
                ),
            ),
            FieldSpec(
                key="mock_mode",
                label_key="dialog.settings.mock",
                kind="check",
            ),
        ),
    ),
    SectionSpec(
        title_key="dialog.settings.section.map",
        fields=(
            FieldSpec(
                key="map_layer",
                label_key="dialog.settings.map_layer",
                kind="combo",
                choices=(
                    ChoiceSpec("vector", "map.layer.vector"),
                    ChoiceSpec("satellite", "map.layer.satellite"),
                ),
            ),
            FieldSpec(
                key="cache_enabled",
                label_key="dialog.settings.cache",
                kind="check",
            ),
            FieldSpec(
                key="cache_limit_mb",
                label_key="dialog.settings.cache_limit",
                kind="spin",
                minimum=256,
                maximum=8192,
                suffix=" MB",
            ),
            FieldSpec(
                key="cache_path",
                label_key="dialog.settings.cache_path",
                kind="text",
            ),
            FieldSpec(
                key="map_center_min_zoom",
                label_key="dialog.settings.center_zoom",
                kind="spin",
                minimum=10,
                maximum=21,
            ),
            FieldSpec(
                key="vehicle_trail_limit",
                label_key="dialog.settings.vehicle_trail",
                kind="spin",
                minimum=10,
                maximum=300,
            ),
            FieldSpec(
                key="contact_trail_limit",
                label_key="dialog.settings.contact_trail",
                kind="spin",
                minimum=10,
                maximum=160,
            ),
        ),
    ),
    SectionSpec(
        title_key="dialog.settings.section.mission",
        fields=(
            FieldSpec(
                key="default_radius_takeoff",
                label_key="dialog.settings.radius_takeoff",
                kind="spin",
                minimum=5,
                maximum=1000,
                suffix=" m",
            ),
            FieldSpec(
                key="default_radius_waypoint",
                label_key="dialog.settings.radius_waypoint",
                kind="spin",
                minimum=5,
                maximum=1000,
                suffix=" m",
            ),
            FieldSpec(
                key="default_radius_loiter",
                label_key="dialog.settings.radius_loiter",
                kind="spin",
                minimum=10,
                maximum=2000,
                suffix=" m",
            ),
            FieldSpec(
                key="default_radius_land",
                label_key="dialog.settings.radius_land",
                kind="spin",
                minimum=5,
                maximum=1000,
                suffix=" m",
            ),
            FieldSpec(
                key="default_radius_hss",
                label_key="dialog.settings.radius_hss",
                kind="spin",
                minimum=10,
                maximum=3000,
                suffix=" m",
            ),
        ),
    ),
    SectionSpec(
        title_key="dialog.settings.section.video",
        fields=(
            FieldSpec(
                key="video_source_type",
                label_key="dialog.settings.video_type",
                kind="combo",
                choices=(
                    ChoiceSpec("mock", "video.source.mock"),
                    ChoiceSpec("rtsp", "video.source.rtsp"),
                    ChoiceSpec("udp", "video.source.udp"),
                    ChoiceSpec("usb", "video.source.usb"),
                ),
            ),
            FieldSpec(
                key="video_source_uri",
                label_key="dialog.settings.video_uri",
                kind="text",
            ),
        ),
    ),
)
