from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class TargetSelectionConfig(Model):
    """Tunable limits for the GCS-only fighter target selector.

    The model is intentionally lightweight: the final simulation is a
    deterministic 3-DOF point-mass approximation, not an autopilot model.
    Limits are kept explicit so flight-test values can replace the conservative
    defaults without touching the scoring code.
    """

    max_target_age_ms: int = 4500
    max_ownship_age_ms: int = 1200
    history_limit: int = 12
    prefilter_limit: int = 10
    final_limit: int = 5
    refresh_interval_s: float = 2.0
    selected_target_max_age_ms: int = 4500

    tail_distance_m: float = 55.0
    precheck_horizon_s: float = 12.0
    precheck_step_s: float = 0.5
    simulation_horizon_s: float = 16.0
    simulation_dt_s: float = 0.05
    lock_duration_s: float = 4.0

    own_min_speed_m_s: float = 12.0
    own_max_speed_m_s: float = 38.0
    own_max_bank_deg: float = 45.0
    own_max_accel_m_s2: float = 3.0
    own_max_decel_m_s2: float = 4.0
    own_max_climb_m_s: float = 8.0
    own_max_descent_m_s: float = 10.0

    lock_range_ideal_m: float = 65.0
    lock_range_min_m: float = 20.0
    lock_range_max_m: float = 190.0
    camera_sigma_deg: float = 15.0
    tail_sigma_deg: float = 20.0
    heading_sigma_deg: float = 23.0
    altitude_sigma_m: float = 28.0
    closure_sigma_m_s: float = 12.0

    def __post_init__(self) -> None:
        if self.max_target_age_ms <= 0 or self.max_ownship_age_ms <= 0:
            raise ValueError("Telemetri yaş limitleri pozitif olmalı")
        if self.history_limit < 2:
            raise ValueError("Hedef geçmişi en az iki örnek tutmalı")
        if not 0.5 <= self.refresh_interval_s <= 60.0:
            raise ValueError("Hedef listesi yenileme aralığı 0.5..60 saniye olmalı")
        if self.selected_target_max_age_ms <= 0:
            raise ValueError("Seçili hedef telemetri yaş limiti pozitif olmalı")
        if self.final_limit <= 0 or self.prefilter_limit < self.final_limit:
            raise ValueError("Ön eleme/final hedef limitleri geçersiz")
        if self.simulation_horizon_s <= self.lock_duration_s:
            raise ValueError("Simülasyon ufku kilit süresinden uzun olmalı")
        if not 0.01 <= self.simulation_dt_s <= 0.25:
            raise ValueError("Simülasyon dt değeri 10..250 ms aralığında olmalı")
        if not 0.1 <= self.precheck_step_s <= 2.0:
            raise ValueError("Ön kontrol adımı geçersiz")
        if self.tail_distance_m <= 0:
            raise ValueError("Arka yerleşim mesafesi pozitif olmalı")
        if not 5.0 <= self.own_max_bank_deg < 80.0:
            raise ValueError("Maksimum bank açısı 5..80 derece aralığında olmalı")


@dataclass(frozen=True, slots=True)
class TargetCandidateScore(Model):
    team_number: int
    final_score: float
    lock_probability: float
    pre_score: float
    tail_access_score: float
    data_confidence: float
    distance_m: float
    altitude_delta_m: float
    data_age_ms: int
    merge_eta_s: float
    best_lock_start_s: float | None
    best_lock_duration_s: float
    heading_alignment_deg: float
    turn_required_deg: float
    simulation_quality: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TargetSelectionResult(Model):
    candidates: tuple[TargetCandidateScore, ...] = field(default_factory=tuple)
    considered_count: int = 0
    valid_count: int = 0
    simulated_count: int = 0
    elapsed_ms: float = 0.0
    reference_time_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [item.to_dict() for item in self.candidates],
            "considered_count": self.considered_count,
            "valid_count": self.valid_count,
            "simulated_count": self.simulated_count,
            "elapsed_ms": self.elapsed_ms,
            "reference_time_utc": self.reference_time_utc,
        }
