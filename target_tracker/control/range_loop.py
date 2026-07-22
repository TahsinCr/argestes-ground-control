"""Mesafe/Gaz Döngüsü - Hedef boyutuna göre UAV irtifa/gaz ayarı.

Prensipler:
1. Hedef bounding box genişliği referans genişlik ile karşılaştırılır
2. Hedef çok küçükse -> alçal/gaz artır (yaklaş)
3. Hedef çok büyükse -> yüksel/gaz azalt (uzaklaş)
4. Hedef kaybolursa -> son gaz değerini koru (hold)

Referans genişlik: Hedef frame genişliğinin %X'i kadar olmalı.
Örn: 640 px frame'de hedef 80 px (12.5%) ideal.
"""

import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class RangeLoop:
    """Mesafe/Gaz Döngüsü - Hedef alan hatasına göre gaz/irtifa ayarı.

    Kullanım:
        rl = RangeLoop()
        throttle = rl.compute_throttle(area_error=1000.0, dt=0.1)
    """

    def __init__(self, kp: float = 0.0003, ki: float = 0.0001, min_throttle: float = 0.0, max_throttle: float = 1.0):
        """
        Args:
            kp: Oransal kazanç
            ki: İntegral kazanç
            min_throttle: Minimum gaz değeri (0-1)
            max_throttle: Maksimum gaz değeri (0-1)
        """
        self.kp = kp
        self.ki = ki
        self.min_throttle = min_throttle
        self.max_throttle = max_throttle
        self._integral = 0.0

    def compute_throttle(self, area_error: float, dt: float) -> float:
        """Alan hatasına göre gaz komutu hesapla.

        Args:
            area_error: Hedef alan hatası (piksel²)
            dt: Zaman adımı (saniye)

        Returns:
            Gaz komutu (0.0 ile 1.0 arası)
        """
        p_term = self.kp * area_error
        self._integral += area_error * dt * self.ki
        self._integral = np.clip(self._integral, -0.5, 0.5)

        throttle = 0.5 - (p_term + self._integral)
        throttle = float(np.clip(throttle, self.min_throttle, self.max_throttle))
        return throttle
