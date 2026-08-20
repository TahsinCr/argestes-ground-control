from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from src.core.abstracts import Model
from src.models.competition import QrCoordinate
from src.models.geometry import Point


# 1: Top-5'in birincisi yeni plan oluşturulunca otomatik seçilir.
# 0: Hiçbir koridor seçilmez; birinci yalnızca "recommended" olur.
AUTO_SELECT_MODE = 1


@dataclass(frozen=True, slots=True)
class KamikazeApproachConfig(Model):
    """Tek noktadan değiştirilen kamikaze yaklaşma geometrisi.

    P1 (dive entry) sabit bir yarıçap değildir. Kamera/uçuş ekseninin QR
    merkezini göstermesi için START_ALTITUDE ve DIVE_ANGLE'dan türetilir.
    P2 (alignment) P1'in aynı radyal üzerinde arkasındadır ve HSS'ye denk
    gelirse tanımlı min/max aralığında otomatik kaydırılır.
    """

    # Dalış geometrisi -- bunlar değişince bütün türetilmiş noktalar değişir.
    start_altitude_m: float = 110.0
    pullup_altitude_m: float = 70.0
    target_altitude_m: float = 0.0
    dive_angle_deg: float = 50.0

    # Yalnız metadata/UI için; geometri hedef merkezi target_altitude_m'dedir.
    qr_plate_height_m: float = 2.5

    # QR -> P1 yaklaşma koridorunun fiziksel genişliği.
    corridor_width_m: float = 0.50

    # P2 -> P1 düz hizalama mesafesi.
    alignment_nominal_m: float = 50.0
    alignment_min_m: float = 40.0
    alignment_max_m: float = 100.0
    alignment_search_step_m: float = 1.0
    invalidate_if_no_alignment_point: bool = True

    # HSS ve aday seçimi.
    hss_margin_m: float = 0.0
    angle_step_deg: int = 1
    max_candidates: int = 5
    preferred_spacing_deg: float = 8.0
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
        if self.alignment_min_m <= 0.0:
            raise ValueError("Minimum P2-P1 mesafesi pozitif olmalı")
        if not self.alignment_min_m <= self.alignment_nominal_m <= self.alignment_max_m:
            raise ValueError("P2 nominal mesafesi min/max aralığında olmalı")
        if self.alignment_search_step_m <= 0.0:
            raise ValueError("P2 arama adımı pozitif olmalı")
        if self.hss_margin_m < 0.0:
            raise ValueError("HSS marjı negatif olamaz")
        if self.angle_step_deg <= 0 or 360 % self.angle_step_deg != 0:
            raise ValueError("Açı adımı 360'ı tam bölmeli")
        if self.max_candidates <= 0:
            raise ValueError("Top-N koridor sayısı pozitif olmalı")
        if self.preferred_spacing_deg < 0.0:
            raise ValueError("Koridor açı aralığı negatif olamaz")
        if int(self.auto_select_mode) not in (0, 1):
            raise ValueError("auto_select_mode yalnızca 0 veya 1 olabilir")

    @property
    def dive_start_radius_m(self) -> float:
        """QR merkezinden P1'e yatay mesafe.

        P1'deki uçuş/kamera ekseni dive_angle_deg ile QR merkezine bakar.
        110 m ve 50 derece için yaklaşık 92.3 m'dir.
        """
        height_m = self.start_altitude_m - self.target_altitude_m
        return height_m / math.tan(math.radians(self.dive_angle_deg))

    @property
    def pullup_radius_m(self) -> float:
        """QR merkezinden 70 m pull-up noktasına kalan yatay mesafe."""
        height_m = self.pullup_altitude_m - self.target_altitude_m
        return height_m / math.tan(math.radians(self.dive_angle_deg))

    @property
    def descent_horizontal_distance_m(self) -> float:
        """P1'den pull-up noktasına 50 derece dalışta alınan yatay yol."""
        return self.dive_start_radius_m - self.pullup_radius_m

    @property
    def nominal_alignment_radius_m(self) -> float:
        """QR merkezinden nominal P2'ye yatay mesafe."""
        return self.dive_start_radius_m + self.alignment_nominal_m

    # Eski entegrasyon isimlerini okuma tarafında koruyoruz. Değerler artık
    # sabit değil, yukarıdaki tek geometri kaynağından türetiliyor.
    @property
    def decision_altitude_m(self) -> float:
        return self.pullup_altitude_m

    @property
    def dive_horizontal_radius_m(self) -> float:
        return self.dive_start_radius_m

    @property
    def corridor_length_m(self) -> float:
        return self.dive_start_radius_m

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
    wind_error_deg: float
    corridor_clearance_m: float | None
    alignment_point_clearance_m: float | None
    entry_point_clearance_m: float | None
    alignment_distance_m: float
    alignment_point_valid: bool
    alignment_fallback_used: bool
    entry: Point
    lineup: Point
    dive_start: Point
    pullup: Point
    polygon: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    selected: bool = False
    recommended: bool = False

    @property
    def lineup_clearance_m(self) -> float | None:
        """Eski isimle uyumluluk: artık segment değil yalnız P2 noktasıdır."""
        return self.alignment_point_clearance_m

    def to_dict(self) -> dict[str, Any]:
        return {
            "angle_deg": self.angle_deg,
            "inbound_heading_deg": round(self.inbound_heading_deg, 6),
            "wind_error_deg": round(self.wind_error_deg, 6),
            "corridor_clearance_m": _round_or_none(self.corridor_clearance_m),
            "alignment_point_clearance_m": _round_or_none(self.alignment_point_clearance_m),
            "lineup_clearance_m": _round_or_none(self.alignment_point_clearance_m),
            "entry_point_clearance_m": _round_or_none(self.entry_point_clearance_m),
            "alignment_distance_m": round(self.alignment_distance_m, 3),
            "alignment_point_valid": self.alignment_point_valid,
            "alignment_fallback_used": self.alignment_fallback_used,
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
    wind_from_deg: float
    wind_speed_mps: float | None
    hss_count: int
    hss_entry_point_rejected_count: int
    hss_corridor_rejected_count: int
    hss_alignment_point_rejected_count: int
    safe_corridor_count: int
    headwind_candidate_count: int
    wind_pool_count: int
    used_spacing_deg: float
    ownship: dict[str, Any] = field(default_factory=dict)

    @property
    def hss_lineup_rejected_count(self) -> int:
        """Eski isimle uyumluluk."""
        return self.hss_alignment_point_rejected_count

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
        """Seçilen koridorun iki-noktalı fixed-wing handoff'u.

        Navigasyon önce P2/alignment'a, sonra P1/dive_entry'ye gider. Planner
        MAVLink göndermez; arrival/state-machine kaptanın entegrasyonunda kalır.
        """
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
            "inbound_heading_deg": round(selected.inbound_heading_deg, 6),
            "alignment_distance_m": round(selected.alignment_distance_m, 3),
            "alignment_point_valid": selected.alignment_point_valid,
            # Yeni açık isimler.
            "alignment_point": alignment,
            "dive_entry_point": dive_entry,
            "pullup_point": pullup,
            "waypoints": [
                {"role": "alignment", "parameters": alignment},
                {"role": "dive_entry", "parameters": dive_entry},
            ],
            "alignment_goto_parameters": alignment,
            "dive_entry_goto_parameters": dive_entry,
            # Eski entegrasyon isimleri.
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
            "selection_locked_until_operator_change": True,
            "auto_replan_on_hss_update": False,
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
                "dive_horizontal_radius_m": round(cfg.dive_start_radius_m, 3),
                "pullup_radius_m": round(cfg.pullup_radius_m, 3),
                "descent_horizontal_distance_m": round(cfg.descent_horizontal_distance_m, 3),
                "corridor_length_m": round(cfg.corridor_length_m, 3),
                "corridor_width_m": cfg.corridor_width_m,
                "alignment_nominal_m": cfg.alignment_nominal_m,
                "alignment_min_m": cfg.alignment_min_m,
                "alignment_max_m": cfg.alignment_max_m,
                "alignment_search_step_m": cfg.alignment_search_step_m,
                "invalidate_if_no_alignment_point": cfg.invalidate_if_no_alignment_point,
                "nominal_alignment_radius_m": round(cfg.nominal_alignment_radius_m, 3),
            },
            "wind": {
                "direction_deg": round(self.wind_from_deg, 3),
                "speed_mps": None if self.wind_speed_mps is None else round(self.wind_speed_mps, 3),
            },
            "ownship": dict(self.ownship),
            "hss_count": self.hss_count,
            "hss_entry_point_rejected_count": self.hss_entry_point_rejected_count,
            "hss_corridor_rejected_count": self.hss_corridor_rejected_count,
            "hss_alignment_point_rejected_count": self.hss_alignment_point_rejected_count,
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
