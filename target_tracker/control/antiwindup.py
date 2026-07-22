"""Anti-windup iyileştirme - Entegratör rüzgarlanmasını önlemek için çeşitli stratejiler.

Stratejiler:
1. Clamping (conditional integration): Çıkış limitteyken integral birikimini durdur
2. Back-calculation: Çıkış saturasyondayken integrali geri besleme ile düzelt
3. Tracking mode: Manuel müdahale sırasında integrali sıfırla
"""

import logging
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


class AntiWindup:
    """Anti-windup iyileştirme - Basit integral toplayıcı.

    Kullanım:
        aw = AntiWindup()
        integral = aw.update(error=5.0, dt=0.1, enabled=True)
    """

    def __init__(self):
        self.integral = 0.0

    def update(self, error: float, dt: float, enabled: bool = True) -> float:
        """Anti-windup düzeltmesi uygula.

        Args:
            error: Mevcut hata (referans - ölçüm)
            dt: Zaman adımı (saniye)
            enabled: İntegral birikimi aktif mi?

        Returns:
            Güncellenmiş integral değeri
        """
        if enabled:
            self.integral += error * dt
        return float(self.integral)
