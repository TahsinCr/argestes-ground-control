from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemePalette:
    name: str
    background: str
    background_alt: str
    surface: str
    surface_alt: str
    surface_hover: str
    border: str
    border_strong: str
    accent: str
    accent_dim: str
    text: str
    muted_text: str
    success: str
    warning: str
    danger: str
    info: str


DARK = ThemePalette(
    name="dark",
    background="#202124",
    background_alt="#25272b",
    surface="#24262a",
    surface_alt="#2d3035",
    surface_hover="#34373d",
    border="#3a3d43",
    border_strong="#50545c",
    accent="#1f7ab8",
    accent_dim="#174f75",
    text="#d7d7d7",
    muted_text="#9aa0a6",
    success="#73a95b",
    warning="#d7a72f",
    danger="#e05d5d",
    info="#58a6ff",
)

LIGHT = ThemePalette(
    name="light",
    background="#dfe5ea",
    background_alt="#ced6de",
    surface="#f7f9fb",
    surface_alt="#ffffff",
    surface_hover="#edf1f5",
    border="#b9c5cf",
    border_strong="#98a8b5",
    accent="#315f75",
    accent_dim="#d7e4ea",
    text="#16222d",
    muted_text="#5e6c78",
    success="#1f9653",
    warning="#a66b07",
    danger="#c33535",
    info="#195fb8",
)

THEMES = {
    "dark": DARK,
    "light": LIGHT,
}


def palette_for(theme_name: str) -> ThemePalette:
    return THEMES.get(theme_name, DARK)
