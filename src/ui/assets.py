"""Helpers converting UI visual assets into Qt objects."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtGui import QBitmap, QIcon, QImage, QPixmap, QRegion


@lru_cache(maxsize=4)
def application_icon(base_dir: Path) -> QIcon:
    """Create a legible app icon by trimming transparent space from the team logo."""

    image = QImage(str(Path(base_dir) / "src" / "assets" / "logo.png"))
    if image.isNull():
        return QIcon()
    if image.hasAlphaChannel():
        bounds = QRegion(QBitmap.fromImage(image.createAlphaMask())).boundingRect()
        if bounds.isValid() and not bounds.isEmpty():
            padding = max(4, min(bounds.width(), bounds.height()) // 24)
            bounds = bounds.adjusted(-padding, -padding, padding, padding).intersected(
                image.rect()
            )
            image = image.copy(bounds)
    return QIcon(QPixmap.fromImage(image))
