"""TEKNOFEST 2026 hedef vuruş alanı geometrisi."""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


class VurusAlani:
    """Şartnamedeki sarı alanı ve hedef boyutu koşulunu değerlendirir."""

    def __init__(
        self,
        frame_width: int,
        frame_height: int,
        minimum_target_axis_ratio: float = 0.06,
    ) -> None:
        if frame_width <= 0 or frame_height <= 0:
            raise ValueError("Frame boyutları pozitif olmalı")
        if not 0.05 <= float(minimum_target_axis_ratio) <= 1.0:
            raise ValueError("Minimum hedef eksen oranı 0.05..1.0 aralığında olmalı")
        self.frame_width = int(frame_width)
        self.frame_height = int(frame_height)
        self.minimum_target_axis_ratio = float(minimum_target_axis_ratio)

        # Şekil 2: yatayda iki yanda %25, dikeyde üst/alt %10 marj.
        self.left = self.frame_width * 0.25
        self.right = self.frame_width * 0.75
        self.top = self.frame_height * 0.10
        self.bottom = self.frame_height * 0.90

        logger.debug(
            "VurusAlani: frame=%dx%d zone=[%.0f-%.0f, %.0f-%.0f] min_axis=%.1f%%",
            self.frame_width,
            self.frame_height,
            self.left,
            self.right,
            self.top,
            self.bottom,
            self.minimum_target_axis_ratio * 100.0,
        )

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return (
            round(self.left),
            round(self.top),
            round(self.right),
            round(self.bottom),
        )

    def is_inside(self, x: float, y: float, w: float, h: float) -> bool:
        """Hedef merkezi sarı alanda ve hedef boyutu güvenli eşikte mi?"""
        if w <= 0 or h <= 0:
            return False
        size_valid = (
            w / self.frame_width >= self.minimum_target_axis_ratio
            or h / self.frame_height >= self.minimum_target_axis_ratio
        )
        inside_x = self.left <= x <= self.right
        inside_y = self.top <= y <= self.bottom
        result = inside_x and inside_y and size_valid
        logger.debug(
            "VurusAlani: (%.0f, %.0f, %.0fx%.0f) -> %s (x:%s y:%s size:%s)",
            x,
            y,
            w,
            h,
            "ICERDE" if result else "DISARDA",
            inside_x,
            inside_y,
            size_valid,
        )
        return result
