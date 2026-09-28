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
    border_subtle: str
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
    border_subtle="#34373d",
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
    background="#edf2f7",
    background_alt="#e3eaf2",
    surface="#f8fafc",
    surface_alt="#ffffff",
    surface_hover="#e9f1fa",
    border="#cbd5e1",
    border_subtle="#dfe6ee",
    border_strong="#aebccc",
    accent="#2563a6",
    accent_dim="#d8e9fa",
    text="#1b2a3a",
    muted_text="#526579",
    success="#17804a",
    warning="#9b6405",
    danger="#b82f3b",
    info="#1762ad",
)

THEMES = {
    "dark": DARK,
    "light": LIGHT,
}


def palette_for(theme_name: str) -> ThemePalette:
    return THEMES.get(theme_name, DARK)
