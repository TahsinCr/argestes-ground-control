from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from src.core.abstracts import Model
from src.models.competition import QrCoordinate
from src.models.geometry import Point


AUTO_SELECT_MODE = 0


@dataclass(frozen=True, slots=True)
class KamikazeApproachConfig(Model):
    """Single source of truth for GCS kamikaze approach geometry.

    P1 is derived from start altitude and dive angle so the nominal dive axis
    intersects the QR target. P2 is a fixed, configurable distance behind P1 on
    the same radial. No distance is baked into the planner: changing these
    fields re-derives the whole geometry on the next unlocked plan build.
    """

    start_altitude_m: float = 130.0
    pullup_altitude_m: float = 90.0
    target_altitude_m: float = 0.0
    dive_angle_deg: float = 45.0
    qr_plate_height_m: float = 2.0

    # Desired navigation corridor around P2 -> P1 -> QR centreline.
    corridor_width_m: float = 1.0

    # P2 is always this far behind P1. It is not shifted around HSS zones.
    alignment_nominal_m: float = 150.0

    # HSS hard-filter inflation beyond the published HSS radius. The corridor
    # half-width is added separately by the segment-clearance calculation.
    hss_margin_m: float = 5.0

    # Candidate generation and Top-5 diversification.
    angle_step_deg: int = 1
    max_candidates: int = 5
    preferred_spacing_deg: float = 8.0
    spacing_sequence_deg: tuple[float, ...] = (8.0, 6.0, 4.0, 3.0, 2.0, 1.0)

    # WIND telemetry policy while the operator is still choosing a corridor.
    wind_min_speed_mps: float = 1.0
    wind_max_age_s: float = 5.0
    wind_refresh_interval_s: float = 120.0

    # Top-5 is recommendation-only. Operator selection locks the plan.
    auto_select_mode: int = AUTO_SELECT_MODE

    def __post_init__(self) -> None:
        if self.start_altitude_m <= self.pullup_altitude_m:
            raise ValueError("Başlangıç irtifası pull-up irtifasından büyük olmalı")
        if self.pullup_altitude_m <= self.target_altitude_m:
            raise ValueError("Pull-up irtifası hedef irtifasından büyük olmalı")
        if not 1.0 <= self.dive_angle_deg < 89.0:
            raise ValueError("Dalış açısı 1..89 derece aralığında olmalı")
        if self.qr_plate_height_m <= 0.0:
            raise ValueError("QR plaka yüksekliği pozitif olmalı")
        if self.corridor_width_m <= 0.0:
            raise ValueError("Koridor genişliği pozitif olmalı")
        if self.alignment_nominal_m <= 0.0:
            raise ValueError("P2-P1 mesafesi pozitif olmalı")
        if self.hss_margin_m < 0.0:
            raise ValueError("HSS marjı negatif olamaz")
        if self.angle_step_deg <= 0 or 360 % self.angle_step_deg != 0:
            raise ValueError("Açı adımı 360'ı tam bölmeli")
        if self.max_candidates <= 0:
            raise ValueError("Top-N koridor sayısı pozitif olmalı")
        if self.preferred_spacing_deg < 0.0:
            raise ValueError("Koridor açı aralığı negatif olamaz")
        if not self.spacing_sequence_deg:
            raise ValueError("Koridor spacing dizisi boş olamaz")
        if any(value <= 0.0 for value in self.spacing_sequence_deg):
            raise ValueError("Koridor spacing değerleri pozitif olmalı")
        if self.wind_min_speed_mps < 0.0:
            raise ValueError("Minimum anlamlı rüzgar hızı negatif olamaz")
        if self.wind_max_age_s <= 0.0:
            raise ValueError("Rüzgar telemetri yaş sınırı pozitif olmalı")
        if self.wind_refresh_interval_s <= 0.0:
            raise ValueError("Rüzgar yenileme aralığı pozitif olmalı")
        if int(self.auto_select_mode) not in (0, 1):
            raise ValueError("auto_select_mode yalnızca 0 veya 1 olabilir")

    @property
    def dive_start_radius_m(self) -> float:
        height_m = self.start_altitude_m - self.target_altitude_m
        return height_m / math.tan(math.radians(self.dive_angle_deg))

    @property
    def pullup_radius_m(self) -> float:
        height_m = self.pullup_altitude_m - self.target_altitude_m
        return height_m / math.tan(math.radians(self.dive_angle_deg))

    @property
    def descent_horizontal_distance_m(self) -> float:
        return self.dive_start_radius_m - self.pullup_radius_m

    @property
    def nominal_alignment_radius_m(self) -> float:
        return self.dive_start_radius_m + self.alignment_nominal_m

    # Compatibility names retained for existing backend consumers.
    @property
    def decision_altitude_m(self) -> float:
        return self.pullup_altitude_m

    @property
    def dive_horizontal_radius_m(self) -> float:
        return self.dive_start_radius_m

    @property
    def corridor_length_m(self) -> float:
        return self.nominal_alignment_radius_m

    @property
    def lineup_extension_m(self) -> float:
        return self.alignment_nominal_m

    @property
    def lineup_distance_from_qr_m(self) -> float:
        return self.nominal_alignment_radius_m


@dataclass(frozen=True, slots=True)
class ApproachCorridor(Model):
    angle_deg: int
    inbound_heading_deg: float
    wind_error_deg: float | None
    wind_rank_applied: bool

    # Clearances already include HSS margin and relevant corridor half-width.
    corridor_clearance_m: float | None
    alignment_segment_clearance_m: float | None
    dive_segment_clearance_m: float | None
    alignment_point_clearance_m: float | None
    entry_point_clearance_m: float | None

    alignment_distance_m: float
    entry: Point
    lineup: Point
    dive_start: Point
    pullup: Point
    polygon: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    selected: bool = False
    recommended: bool = False

    @property
    def alignment_point_valid(self) -> bool:
        return self.alignment_point_clearance_m is None or self.alignment_point_clearance_m > 0.0

    @property
    def alignment_fallback_used(self) -> bool:
        return False

    @property
    def lineup_clearance_m(self) -> float | None:
        return self.alignment_point_clearance_m

    def to_dict(self) -> dict[str, Any]:
        return {
            "angle_deg": self.angle_deg,
            "inbound_heading_deg": round(self.inbound_heading_deg, 6),
            "wind_error_deg": _round_or_none(self.wind_error_deg),
            "wind_rank_applied": self.wind_rank_applied,
            "corridor_clearance_m": _round_or_none(self.corridor_clearance_m),
            "alignment_segment_clearance_m": _round_or_none(self.alignment_segment_clearance_m),
            "dive_segment_clearance_m": _round_or_none(self.dive_segment_clearance_m),
            "alignment_point_clearance_m": _round_or_none(self.alignment_point_clearance_m),
            "lineup_clearance_m": _round_or_none(self.alignment_point_clearance_m),
            "entry_point_clearance_m": _round_or_none(self.entry_point_clearance_m),
            "alignment_distance_m": round(self.alignment_distance_m, 3),
            "alignment_point_valid": self.alignment_point_valid,
            "alignment_fallback_used": False,
            "entry": self.entry.to_dict(),
            "lineup": self.lineup.to_dict(),
            "dive_start": self.dive_start.to_dict(),
            "pullup": self.pullup.to_dict(),
            "polygon": [[round(lat, 8), round(lon, 8)] for lat, lon in self.polygon],
            "selected": self.selected,
            "recommended": self.recommended,
        }


@dataclass(frozen=True, slots=True)
class KamikazeApproachPlan(Model):
    revision: int
    config: KamikazeApproachConfig
    qr: QrCoordinate
    corridors: tuple[ApproachCorridor, ...]
    recommended_angle_deg: int
    selected_angle_deg: int | None
    selection_source: str
    selection_state: str

    wind_from_deg: float | None
    wind_speed_mps: float | None
    wind_status: str
    wind_rank_applied: bool

    hss_count: int
    hss_entry_point_rejected_count: int
    hss_corridor_rejected_count: int
    hss_alignment_point_rejected_count: int
    hss_alignment_segment_rejected_count: int
    safe_corridor_count: int
    headwind_candidate_count: int
    wind_pool_count: int
    used_spacing_deg: float
    ownship: dict[str, Any] = field(default_factory=dict)

    @property
    def hss_lineup_rejected_count(self) -> int:
        return self.hss_alignment_point_rejected_count

    @property
    def locked(self) -> bool:
        return self.selection_state in {"corridor_selected", "mission_active"}

    @property
    def recommended_corridor(self) -> ApproachCorridor:
        for corridor in self.corridors:
            if corridor.recommended:
                return corridor
        raise RuntimeError("Önerilen koridor bulunamadı")

    @property
    def selected_corridor(self) -> ApproachCorridor | None:
        for corridor in self.corridors:
            if corridor.selected:
                return corridor
        return None

    @property
    def selected_entry(self) -> Point | None:
        corridor = self.selected_corridor
        return None if corridor is None else corridor.entry

    def navigation_handoff(self) -> dict[str, Any]:
        selected = self.selected_corridor
        if selected is None:
            return {
                "ready": False,
                "reason": "no_corridor_selected",
                "recommended_angle_deg": self.recommended_angle_deg,
            }

        alignment = selected.lineup.to_dict()
        dive_entry = selected.dive_start.to_dict()
        pullup = selected.pullup.to_dict()
        return {
            "ready": True,
            "plan_revision": self.revision,
            "selected_angle_deg": selected.angle_deg,
            "selection_source": self.selection_source,
            "selection_state": self.selection_state,
            "inbound_heading_deg": round(selected.inbound_heading_deg, 6),
            "alignment_distance_m": round(selected.alignment_distance_m, 3),
            "alignment_point_valid": selected.alignment_point_valid,
            "alignment_point": alignment,
            "dive_entry_point": dive_entry,
            "pullup_point": pullup,
            "waypoints": [
                {"role": "alignment", "parameters": alignment},
                {"role": "dive_entry", "parameters": dive_entry},
            ],
            "alignment_goto_parameters": alignment,
            "dive_entry_goto_parameters": dive_entry,
            # compatibility aliases
            "lineup": alignment,
            "entry": dive_entry,
            "dive_start": dive_entry,
            "decision_point": pullup,
            "lineup_goto_parameters": alignment,
            "entry_goto_parameters": dive_entry,
        }

    def to_dict(self) -> dict[str, Any]:
        selected = self.selected_corridor
        recommended = self.recommended_corridor
        cfg = self.config
        return {
            "execution": "planned",
            "plan_revision": self.revision,
            "auto_select_mode": cfg.auto_select_mode,
            "selection_state": self.selection_state,
            "selection_locked": self.locked,
            "auto_replan_on_hss_update": False,
            "wind_refresh_interval_s": cfg.wind_refresh_interval_s,
            "qr": {
                "latitude": self.qr.latitude,
                "longitude": self.qr.longitude,
                "plate_height_m": cfg.qr_plate_height_m,
                "target_altitude_m": cfg.target_altitude_m,
            },
            "geometry": {
                "start_altitude_m": cfg.start_altitude_m,
                "pullup_altitude_m": cfg.pullup_altitude_m,
                "decision_altitude_m": cfg.pullup_altitude_m,
                "target_altitude_m": cfg.target_altitude_m,
                "dive_angle_deg": cfg.dive_angle_deg,
                "dive_start_radius_m": round(cfg.dive_start_radius_m, 3),
                "pullup_radius_m": round(cfg.pullup_radius_m, 3),
                "descent_horizontal_distance_m": round(cfg.descent_horizontal_distance_m, 3),
                "corridor_length_m": round(cfg.corridor_length_m, 3),
                "corridor_width_m": cfg.corridor_width_m,
                "alignment_nominal_m": cfg.alignment_nominal_m,
                "nominal_alignment_radius_m": round(cfg.nominal_alignment_radius_m, 3),
                "hss_margin_m": cfg.hss_margin_m,
                "angle_step_deg": cfg.angle_step_deg,
            },
            "wind": {
                "direction_deg": None if self.wind_from_deg is None else round(self.wind_from_deg, 3),
                "speed_mps": None if self.wind_speed_mps is None else round(self.wind_speed_mps, 3),
                "status": self.wind_status,
                "ranking_applied": self.wind_rank_applied,
                "minimum_meaningful_speed_mps": cfg.wind_min_speed_mps,
            },
            "ownship": dict(self.ownship),
            "hss_count": self.hss_count,
            "hss_entry_point_rejected_count": self.hss_entry_point_rejected_count,
            "hss_corridor_rejected_count": self.hss_corridor_rejected_count,
            "hss_alignment_point_rejected_count": self.hss_alignment_point_rejected_count,
            "hss_alignment_segment_rejected_count": self.hss_alignment_segment_rejected_count,
            "hss_lineup_rejected_count": self.hss_alignment_point_rejected_count,
            "safe_corridor_count": self.safe_corridor_count,
            "headwind_candidate_count": self.headwind_candidate_count,
            "wind_pool_count": self.wind_pool_count,
            "used_spacing_deg": self.used_spacing_deg,
            "corridors": [corridor.to_dict() for corridor in self.corridors],
            "recommended_angle_deg": self.recommended_angle_deg,
            "recommended_entry": recommended.entry.to_dict(),
            "recommended_lineup": recommended.lineup.to_dict(),
            "recommended_dive_start": recommended.dive_start.to_dict(),
            "recommended_pullup": recommended.pullup.to_dict(),
            "selected_angle_deg": self.selected_angle_deg,
            "selection_source": self.selection_source,
            "selected_entry": None if selected is None else selected.entry.to_dict(),
            "selected_lineup": None if selected is None else selected.lineup.to_dict(),
            "selected_dive_start": None if selected is None else selected.dive_start.to_dict(),
            "selected_pullup": None if selected is None else selected.pullup.to_dict(),
            "navigation_handoff": self.navigation_handoff(),
        }


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
