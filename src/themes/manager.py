from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from src.themes.palette import ThemePalette, palette_for
from src.themes.stylesheet import build_stylesheet


def apply_theme(app: QApplication, theme_name: str) -> ThemePalette:
    theme = palette_for(theme_name)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.background))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.surface_alt))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.surface))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme.surface_alt))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.background))
    app.setPalette(palette)
    app.setStyleSheet(build_stylesheet(theme))
    return theme
