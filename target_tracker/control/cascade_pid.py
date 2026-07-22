"""Cascade PID Kontrolcü - Dış Döngü (Piksel Hatası -> Açısal Hız) + İç Döngü (Açısal Hız -> Servo).

Mimari:
  error_px ──[OUTER PID]──> yaw_rate_cmd ──[INNER PID]──> servo_cmd
                    ^                                ^
                    |                                |
              Px hızı (piksel/s)              Açısal hız (derece/s)

İç döngü dış döngüden ~5x hızlı çalışır (örn. 50 Hz vs 10 Hz).
Ancak bu implementasyonda her iki döngü aynı dt ile çalışır ve
iç döngü dış döngünün çıktısını referans olarak alır.

TEKNOFEST 2026 KTR uyumu:
  - max_yaw_rate:  45 °/sn
  - max_pitch_rate: 30 °/sn
  - dead_zone: 5 px (merkez etrafı)
  - outer/inner gain ratio: ~1:3 (hızlı tepki + kararlılık)
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class PIDController:
    """Tek bir PID kontrolcü (paralel form).

    output = Kp * error + Ki * integral + Kd * derivative
    """

    def __init__(self, kp: float, ki: float, kd: float, output_limit: float = float("inf")):
        """
        Args:
            kp: Oransal kazanç
            ki: İntegral kazanç
            kd: Türevsel kazanç
            output_limit: Çıkış sınırı (mutlak değer)
        """
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_limit = output_limit

        self._integral = 0.0
        self._prev_error = 0.0
        self._prev_output = 0.0

    def compute(self, error: float, dt: float) -> float:
        """PID hesaplaması yap.

        Args:
            error: Referans - ölçüm hatası
            dt: Zaman adımı (saniye)

        Returns:
            Kontrol çıkışı
        """
        if dt <= 0.0:
            return 0.0

        # P term
        p_term = self.kp * error

        # I term (anti-windup: integral sınırlama)
        self._integral += error * dt
        i_term = self.ki * self._integral

        # D term (türev: error değişimi)
        derivative = (error - self._prev_error) / dt
        d_term = self.kd * derivative

        output = p_term + i_term + d_term

        # Çıkış sınırlama
        if abs(output) > self.output_limit:
            output = self.output_limit if output > 0 else -self.output_limit
            # Anti-windup: integrali geriye al
            self._integral -= error * dt

        self._prev_error = error
        self._prev_output = output

        return output

    def reset(self):
        """İntegral ve önceki hatayı sıfırla."""
        self._integral = 0.0
        self._prev_error = 0.0
        self._prev_output = 0.0


class CascadePID:
    """Çift katmanlı cascade PID kontrolcü.

    Dış döngü: Piksel hatası -> Açısal hız komutu
    İç döngü:  Açısal hız hatası -> Servo komutu

    Not: İç döngü için açısal hız feedback'i yoksa (IMU yok),
    iç döngü doğrudan dış döngü çıktısını ölçekler.
    """

    def __init__(self, outer_gains: Dict[str, float], inner_gains: Dict[str, float]):
        """
        Args:
            outer_gains: {"kp": float, "ki": float, "kd": float} - dış döngü
            inner_gains: {"kp": float, "ki": float, "kd": float} - iç döngü
        """
        self.outer = PIDController(
            kp=outer_gains.get("kp", 0.08),
            ki=outer_gains.get("ki", 0.004),
            kd=outer_gains.get("kd", 0.002),
            output_limit=45.0,  # max yaw/pitch rate
        )
        self.inner = PIDController(
            kp=inner_gains.get("kp", 0.15),
            ki=inner_gains.get("ki", 0.007),
            kd=inner_gains.get("kd", 0.003),
            output_limit=1.0,  # normalize servo (0-1)
        )
        logger.info(
            "CascadePID: outer=(%.3f, %.4f, %.4f) inner=(%.3f, %.4f, %.4f)",
            self.outer.kp, self.outer.ki, self.outer.kd,
            self.inner.kp, self.inner.ki, self.inner.kd,
        )

    def compute(self, error: float, dt: float) -> float:
        """Cascade PID hesaplaması.

        Args:
            error: Piksel cinsinden hata
            dt: Zaman adımı (saniye)

        Returns:
            Normalize edilmiş servo komutu (-1.0 ile 1.0 arası)
        """
        # Dış döngü: piksel hatası -> açısal hız komutu
        angular_rate_cmd = self.outer.compute(error, dt)

        # İç döngü: açısal hız komutu -> servo sinyali
        # Açısal hız feedback'i yok, inner PID direkt outer çıktısını filtreler
        servo_cmd = self.inner.compute(angular_rate_cmd, dt)

        return servo_cmd

    def reset(self):
        """Her iki PID kontrolcüyü sıfırla."""
        self.outer.reset()
        self.inner.reset()
        logger.debug("CascadePID sifirlandi.")