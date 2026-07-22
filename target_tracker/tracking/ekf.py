"""Jacobian tabanlı EKF (Extended Kalman Filter) - Manevralı hedefler için sonraki kare tahmini.

State: [cx, cy, vx, vy, w, h, vw, vh]ᵀ
- cx, cy: merkez koordinatları
- vx, vy: piksel/saniye hız bileşenleri
- w, h: bounding box genişlik ve yükseklik
- vw, vh: genişlik ve yükseklik değişim hızları

Ölçüm: [cx, cy, w, h]ᵀ (detection'dan)
"""

import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ExtendedKalmanFilter:
    """Jacobian tabanlı EKF (Kim & Bang modeli, TEKNOFEST 2026 UAV varyantı)."""

    def __init__(self, dt: float = 1.0 / 30.0):
        """
        Args:
            dt: Zaman adımı (saniye), varsayılan ~30 FPS.
        """
        # State: [cx, cy, vx, vy, w, h, vw, vh]
        self.dt = dt

        # Durum vektörü
        self.x = np.zeros((8, 1), dtype=np.float64)

        # Kovaryans matrisi
        self.P = np.eye(8, dtype=np.float64) * 100.0

        # İşlem gürültüsü (Q) - Kullanıcıdan alınan değerler
        self.Q = np.diag([
            0.5,  # cx
            0.5,  # cy
            0.1,  # vx
            0.1,  # vy
            1.0,  # w
            1.0,  # h
            0.2,  # vw
            0.2,  # vh
        ]).astype(np.float64)

        # Ölçüm gürültüsü (R) - Kullanıcıdan alınan değerler (YOLO'ya güven arttı)
        self.R = np.diag([2.5, 2.5, 5.0, 5.0]).astype(np.float64)

        # Ölçüm matrisi (H): [cx, cy, w, h] ölçülür
        self.H = np.zeros((4, 8), dtype=np.float64)
        self.H[0, 0] = 1.0
        self.H[1, 1] = 1.0
        self.H[2, 4] = 1.0
        self.H[3, 5] = 1.0

        self._initialized = False
        logger.debug("EKF baslatildi. dt=%.4f sn", dt)

    def _build_F(self, dt: float) -> np.ndarray:
        """Durum geçiş matrisini dt'ye göre oluştur."""
        F = np.eye(8, dtype=np.float64)
        F[0, 2] = dt   # cx += vx * dt
        F[1, 3] = dt   # cy += vy * dt
        F[4, 6] = dt   # w += vw * dt
        F[5, 7] = dt   # h += vh * dt
        return F

    def initialize(self, cx: float, cy: float, w: float, h: float):
        """EKF state'ini ilk detection ile başlat.

        Args:
            cx: Bounding box merkez x
            cy: Bounding box merkez y
            w: Bounding box genişlik
            h: Bounding box yükseklik
        """
        self.x = np.array([[cx], [cy], [0.0], [0.0], [w], [h], [0.0], [0.0]], dtype=np.float64)
        self.P = np.eye(8, dtype=np.float64) * 50.0
        self._initialized = True
        logger.debug("EKF initialize: cx=%.1f cy=%.1f w=%.1f h=%.1f", cx, cy, w, h)

    def predict(self) -> Optional[Tuple[float, float, float, float]]:
        """Bir adım ileri tahmin yap (zaman güncellemesi).

        Returns:
            (cx, cy, w, h) tahmin edilen bounding box veya None.
        """
        if not self._initialized:
            return None

        F = self._build_F(self.dt)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + self.Q

        cx, cy, w, h = float(self.x[0, 0]), float(self.x[1, 0]), float(self.x[4, 0]), float(self.x[5, 0])

        # Fiziksel sınırlar
        w = max(w, 2.0)
        h = max(h, 2.0)

        return (cx, cy, w, h)

    def update(self, measurement: Tuple[float, float, float, float], confidence: float = 1.0) -> Optional[Tuple[float, float, float, float]]:
        """Detection ölçümü ile state güncelle (ölçüm güncellemesi).

        Args:
            measurement: (cx, cy, w, h) detection bounding box
            confidence: Ölçüm güven skoru (Kullanıcı min_conf_for_update değeri)

        Returns:
            Güncellenmiş (cx, cy, w, h) veya None.
        """
        cx_m, cy_m, w_m, h_m = measurement

        if not self._initialized:
            self.initialize(cx_m, cy_m, w_m, h_m)
            return (cx_m, cy_m, w_m, h_m)

        # Güven skoruna göre R (Ölçüm Gürültüsü) matrisini dinamik olarak ölçeklendir
        # conf düşükse R_effective çok yüksek olur, EKF ölçüme daha az güvenir.
        # Bu sayede 0.60 sert eşiği yerine yumuşak ve sürekli bir geçiş sağlanır.
        conf_clamped = max(confidence, 0.01)
        R_effective = self.R / conf_clamped

        z = np.array([[cx_m], [cy_m], [w_m], [h_m]], dtype=np.float64)

        # Kalman kazancı
        S = self.H @ self.P @ self.H.T + R_effective
        K = self.P @ self.H.T @ np.linalg.inv(S)

        # State güncelleme
        y = z - self.H @ self.x  # innovation
        self.x = self.x + K @ y
        self.P = (np.eye(8) - K @ self.H) @ self.P

        cx, cy, w, h = float(self.x[0, 0]), float(self.x[1, 0]), float(self.x[4, 0]), float(self.x[5, 0])

        # Fiziksel sınırlar
        w = max(w, 2.0)
        h = max(h, 2.0)

        # NaN kontrolü
        if any(np.isnan(v) for v in (cx, cy, w, h)):
            logger.warning("EKF NaN state, re-initializing...")
            self.initialize(cx_m, cy_m, w_m, h_m)
            return (cx_m, cy_m, w_m, h_m)

        return (cx, cy, w, h)

    def get_velocity(self) -> Tuple[float, float]:
        """Tahmin edilen hız bileşenlerini döndür.

        Returns:
            (vx, vy) piksel/saniye cinsinden hız.
        """
        return (float(self.x[2, 0]), float(self.x[3, 0]))

    def get_covariance(self) -> np.ndarray:
        """Kovaryans matrisini döndür."""
        return self.P.copy()

    def reset(self):
        """EKF state'ini sıfırla."""
        self.x = np.zeros((8, 1), dtype=np.float64)
        self.P = np.eye(8, dtype=np.float64) * 100.0
        self._initialized = False
        logger.debug("EKF sifirlandi.")