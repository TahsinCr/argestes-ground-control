"""TEKNOFEST 2026 uyumlu 4 saniyelik kilit zamanlayıcısı."""

from __future__ import annotations


class LockTimer:
    def __init__(
        self,
        lock_duration: float = 4.0,
        tolerance: float = 1.0,
    ) -> None:
        self.lock_duration = float(lock_duration)
        self.tolerance = float(tolerance)
        if self.lock_duration <= 0 or self.tolerance < 0:
            raise ValueError("Kilit süresi pozitif, tolerans negatif olmayan değer olmalı")
        self.reset()

    @property
    def current_progress(self) -> float:
        return min(1.0, max(0.0, self.elapsed / self.lock_duration))

    def reset(self) -> None:
        self._clear_timing()
        self.just_failed = False
        self.last_attempt_elapsed = 0.0
        self.last_attempt_window_elapsed = 0.0
        self.last_attempt_invalid_elapsed = 0.0

    def _clear_timing(self) -> None:
        self.elapsed = 0.0
        self.window_elapsed = 0.0
        self.invalid_elapsed = 0.0
        self.started = False
        self.locked = False

    def update(self, dt: float, in_lock_zone: bool) -> bool:
        self.just_failed = False
        # Pencere gerçek duvar saatini temsil eder. CPU/ONNX çıkarımı
        # 500 ms'den uzun sürdüğünde zamanı kırpmak, ekrandaki hedef
        # doğru olsa bile 4 saniyenin birikmesini engelliyordu.
        dt = max(0.0, float(dt))
        if self.locked:
            return True

        if not self.started:
            if not in_lock_zone:
                return False
            self.started = True

        self.window_elapsed += dt
        if in_lock_zone:
            self.elapsed += dt
        else:
            self.invalid_elapsed += dt

        maximum_window = self.lock_duration + self.tolerance
        self.locked = bool(
            self.elapsed >= self.lock_duration
            and self.window_elapsed <= maximum_window + 1e-9
        )
        if not self.locked and self.window_elapsed >= maximum_window - 1e-9:
            self.last_attempt_elapsed = self.elapsed
            self.last_attempt_window_elapsed = self.window_elapsed
            self.last_attempt_invalid_elapsed = self.invalid_elapsed
            self._clear_timing()
            self.just_failed = True
        return self.locked
