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
            FieldSpec(
                "ui.language",
                "dialog.settings.language",
                "combo",
                (ChoiceSpec("tr", "language.tr"), ChoiceSpec("en", "language.en")),
            ),
            FieldSpec(
                "ui.theme",
                "dialog.settings.theme",
                "combo",
                (ChoiceSpec("dark", "theme.dark"), ChoiceSpec("light", "theme.light")),
            ),
        ),
    ),
)


def iter_fields():
    for section in SETTINGS_SECTIONS:
        yield from section.fields
