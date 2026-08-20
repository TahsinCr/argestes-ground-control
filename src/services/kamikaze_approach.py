from __future__ import annotations

from dataclasses import replace
import math
import threading
from typing import Any, Iterable

from src.models.competition import HssZone, QrCoordinate
from src.models.geometry import EARTH_RADIUS_M, Point
from src.models.kamikaze_approach import (
    ApproachCorridor,
    KamikazeApproachConfig,
    KamikazeApproachPlan,
)
from src.services.abstracts.competition import CompetitionService
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import ServiceLogger


def angular_distance_deg(a_deg: float, b_deg: float) -> float:
    delta = (float(a_deg) - float(b_deg) + 180.0) % 360.0 - 180.0
    return abs(delta)


class KamikazeApproachService:
    """GCS-only QR approach corridor planner.

    QR/HSS CompetitionService'den, rüzgar/ownship TelemetryService'den gelir.
    Servis MAVLink göndermez. Her build_plan çağrısı config'teki irtifa/açı
    değerlerinden P1, pull-up ve P2 geometrisini yeniden hesaplar.
    """

    def __init__(
        self,
        competition: CompetitionService,
        telemetry: TelemetryService,
        logger: ServiceLogger | None = None,
        config: KamikazeApproachConfig | None = None,
    ) -> None:
        self._competition = competition
        self._telemetry = telemetry
        self._logger = logger
        self._config = config or KamikazeApproachConfig()
        self._lock = threading.RLock()
        self._revision = 0
        self._last_plan: KamikazeApproachPlan | None = None

    @property
    def config(self) -> KamikazeApproachConfig:
        with self._lock:
            return self._config

    @property
    def last_plan(self) -> KamikazeApproachPlan | None:
        with self._lock:
            return self._last_plan

    def update_config(self, **changes: Any) -> KamikazeApproachConfig:
        """Config'i tek yerden değiştir; bir sonraki plan tamamen yeniden türetilir."""
        with self._lock:
            self._config = replace(self._config, **changes)
            return self._config

    def set_auto_select_mode(
        self,
        mode: int,
        *,
        apply_to_current_plan: bool = True,
    ) -> KamikazeApproachPlan | None:
        """0=manual/no default selection, 1=auto-select recommended on new plan."""
        mode = int(mode)
        if mode not in (0, 1):
            raise ValueError("AUTO_SELECT_MODE yalnızca 0 veya 1 olabilir")
        with self._lock:
            previous_mode = self._config.auto_select_mode
            self._config = replace(self._config, auto_select_mode=mode)
            plan = self._last_plan
            if not apply_to_current_plan or plan is None or mode == previous_mode:
                return plan
            if mode == 0:
                plan = self._with_selection(plan, None, source="none")
            elif plan.selected_corridor is None:
                plan = self._with_selection(plan, plan.recommended_angle_deg, source="auto")
            self._last_plan = plan
            return plan

    def build_plan(self) -> KamikazeApproachPlan:
        qr = self._competition.qr_coordinate
        if qr is None:
            raise ValueError("TEKNOFEST QR koordinatı henüz mevcut değil")
        telemetry = self._telemetry.current
        wind_heading = _finite(telemetry.wind_heading, "wind_heading")
        wind_speed = _finite(telemetry.wind_speed, "wind_speed")
        ownship = {
            "latitude": float(telemetry.latitude),
            "longitude": float(telemetry.longitude),
            "altitude_agl": float(telemetry.altitude_agl),
            "heading": float(telemetry.heading),
            "ground_speed": float(telemetry.ground_speed),
            "position_valid": bool(telemetry.position_valid),
        }
        return self.build_plan_from_values(
            qr=qr,
            zones=self._competition.hss_zones,
            wind_from_deg=wind_heading,
            wind_speed_mps=wind_speed,
            ownship=ownship,
        )

    def build_plan_from_values(
        self,
        *,
        qr: QrCoordinate,
        zones: Iterable[HssZone],
        wind_from_deg: float,
        wind_speed_mps: float | None = None,
        ownship: dict[str, Any] | None = None,
    ) -> KamikazeApproachPlan:
        wind_from_deg = _finite(wind_from_deg, "wind_from_deg") % 360.0
        if wind_speed_mps is not None:
            wind_speed_mps = _finite(wind_speed_mps, "wind_speed_mps")
        zones = tuple(zones)
        cfg = self.config

        # P1, pull-up ve koridor boyu aynı config'ten türetilir.
        p1_radius_m = cfg.dive_start_radius_m
        pullup_radius_m = cfg.pullup_radius_m
        half_width_m = cfg.corridor_width_m / 2.0
        qr_point = Point(qr.latitude, qr.longitude, cfg.target_altitude_m)
        hss_local = [self._zone_local(zone, qr_point) for zone in zones]

        safe: list[ApproachCorridor] = []
        rejected_entry = 0
        rejected_corridor = 0
        rejected_alignment = 0

        for angle in range(0, 360, cfg.angle_step_deg):
            theta = math.radians(angle)
            forward_n = math.cos(theta)
            forward_e = math.sin(theta)

            # P1 = 50 derece dalış ekseninin QR merkezini gördüğü başlangıç.
            p1_n = forward_n * p1_radius_m
            p1_e = forward_e * p1_radius_m

            p1_clearance = self._minimum_point_clearance(
                hss_local,
                point_n=p1_n,
                point_e=p1_e,
            )
            if p1_clearance is not None and p1_clearance <= 0.0:
                rejected_entry += 1
                continue

            # QR -> P1 gerçek dalış koridoru. P2 -> P1 segmenti kasıtlı olarak
            # HSS ile elenmez; hizalandıktan sonra tekrar yön bozmayacağız.
            corridor_clearance = self._minimum_clearance(
                hss_local,
                start_n=0.0,
                start_e=0.0,
                end_n=p1_n,
                end_e=p1_e,
                half_width_m=half_width_m,
            )
            if corridor_clearance is not None and corridor_clearance <= 0.0:
                rejected_corridor += 1
                continue

            (
                alignment_distance_m,
                p2_n,
                p2_e,
                alignment_clearance,
                alignment_valid,
                fallback_used,
            ) = self._find_alignment_point(
                hss_local,
                forward_n=forward_n,
                forward_e=forward_e,
                p1_radius_m=p1_radius_m,
                cfg=cfg,
            )
            if not alignment_valid and cfg.invalidate_if_no_alignment_point:
                rejected_alignment += 1
                continue

            inbound = (float(angle) + 180.0) % 360.0
            wind_error = angular_distance_deg(inbound, wind_from_deg)
            p1 = self._point_from_local(p1_n, p1_e, qr_point, cfg.start_altitude_m)
            p2 = self._point_from_local(p2_n, p2_e, qr_point, cfg.start_altitude_m)
            pullup = self._point_from_local(
                forward_n * pullup_radius_m,
                forward_e * pullup_radius_m,
                qr_point,
                cfg.pullup_altitude_m,
            )
            safe.append(
                ApproachCorridor(
                    angle_deg=angle,
                    inbound_heading_deg=inbound,
                    wind_error_deg=wind_error,
                    corridor_clearance_m=corridor_clearance,
                    alignment_point_clearance_m=alignment_clearance,
                    entry_point_clearance_m=p1_clearance,
                    alignment_distance_m=alignment_distance_m,
                    alignment_point_valid=alignment_valid,
                    alignment_fallback_used=fallback_used,
                    entry=p1,
                    lineup=p2,
                    dive_start=p1,
                    pullup=pullup,
                    polygon=self._polygon(
                        qr_point,
                        angle,
                        p1_radius_m,
                        cfg.corridor_width_m,
                    ),
                    selected=False,
                    recommended=False,
                )
            )

        if not safe:
            raise ValueError("HSS filtresi sonrası güvenli yaklaşma koridoru kalmadı")

        headwind = [candidate for candidate in safe if candidate.wind_error_deg <= 90.0]
        wind_pool = headwind if headwind else safe
        ordered = sorted(
            wind_pool,
            key=lambda item: (
                item.wind_error_deg,
                not item.alignment_point_valid,
                -self._clearance_sort_value(item.alignment_point_clearance_m),
                -self._clearance_sort_value(item.corridor_clearance_m),
                item.angle_deg,
            ),
        )
        top, used_spacing = self._adaptive_spacing_select(ordered, cfg.max_candidates)
        if not top:
            raise ValueError("Rüzgar filtresi sonrası koridor kalmadı")

        recommended_angle = top[0].angle_deg
        with self._lock:
            self._revision += 1
            selected_angle = recommended_angle if cfg.auto_select_mode == 1 else None
            selection_source = "auto" if selected_angle is not None else "none"
            corridors = tuple(
                replace(
                    corridor,
                    recommended=corridor.angle_deg == recommended_angle,
                    selected=corridor.angle_deg == selected_angle,
                )
                for corridor in top
            )
            plan = KamikazeApproachPlan(
                revision=self._revision,
                config=cfg,
                qr=qr,
                corridors=corridors,
                recommended_angle_deg=recommended_angle,
                selected_angle_deg=selected_angle,
                selection_source=selection_source,
                wind_from_deg=wind_from_deg,
                wind_speed_mps=wind_speed_mps,
                hss_count=len(zones),
                hss_entry_point_rejected_count=rejected_entry,
                hss_corridor_rejected_count=rejected_corridor,
                hss_alignment_point_rejected_count=rejected_alignment,
                safe_corridor_count=len(safe),
                headwind_candidate_count=len(headwind),
                wind_pool_count=len(wind_pool),
                used_spacing_deg=used_spacing,
                ownship=dict(ownship or {}),
            )
            self._last_plan = plan

        if self._logger is not None:
            self._logger.event(
                "kamikaze_approach.plan_built",
                "Kamikaze yaklaşma koridorları hesaplandı",
                details={
                    "revision": plan.revision,
                    "recommended_angle_deg": plan.recommended_angle_deg,
                    "selected_angle_deg": plan.selected_angle_deg,
                    "auto_select_mode": cfg.auto_select_mode,
                    "hss_count": len(zones),
                    "p1_radius_m": round(cfg.dive_start_radius_m, 3),
                    "pullup_radius_m": round(cfg.pullup_radius_m, 3),
                },
            )
        return plan

    def select(
        self,
        angle_deg: int,
        *,
        plan_revision: int | None = None,
    ) -> KamikazeApproachPlan:
        requested = int(angle_deg) % 360
        with self._lock:
            plan = self._last_plan
            if plan is None:
                raise ValueError("Aktif yaklaşma planı yok")
            if plan_revision is not None and int(plan_revision) != plan.revision:
                raise ValueError("Eski yaklaşma planı revision'ı ile seçim yapılamaz")
            updated = self._with_selection(plan, requested, source="operator")
            self._last_plan = updated
        if self._logger is not None:
            self._logger.event(
                "kamikaze_approach.corridor_selected",
                "Kamikaze yaklaşma koridoru operatör tarafından seçildi",
                details={"revision": updated.revision, "angle_deg": requested},
            )
        return updated

    def clear_selection(self, *, plan_revision: int | None = None) -> KamikazeApproachPlan:
        with self._lock:
            plan = self._last_plan
            if plan is None:
                raise ValueError("Aktif yaklaşma planı yok")
            if plan_revision is not None and int(plan_revision) != plan.revision:
                raise ValueError("Eski yaklaşma planı revision'ı ile seçim temizlenemez")
            updated = self._with_selection(plan, None, source="none")
            self._last_plan = updated
            return updated

    def selected_navigation_handoff(self) -> dict[str, Any]:
        with self._lock:
            if self._last_plan is None:
                return {"ready": False, "reason": "no_plan"}
            return self._last_plan.navigation_handoff()

    @staticmethod
    def _with_selection(
        plan: KamikazeApproachPlan,
        angle_deg: int | None,
        *,
        source: str,
    ) -> KamikazeApproachPlan:
        if angle_deg is not None:
            allowed = {corridor.angle_deg for corridor in plan.corridors}
            if angle_deg not in allowed:
                raise ValueError("Seçilen koridor mevcut Top-5 içinde değil")
        corridors = tuple(
            replace(corridor, selected=(angle_deg is not None and corridor.angle_deg == angle_deg))
            for corridor in plan.corridors
        )
        return replace(
            plan,
            corridors=corridors,
            selected_angle_deg=angle_deg,
            selection_source=source,
        )

    def _alignment_distance_candidates(self, cfg: KamikazeApproachConfig) -> tuple[float, ...]:
        """50 -> ... -> 100, sonra 49 -> ... -> 40 sırasını üretir."""
        nominal = float(cfg.alignment_nominal_m)
        step = float(cfg.alignment_search_step_m)
        maximum = float(cfg.alignment_max_m)
        minimum = float(cfg.alignment_min_m)
        eps = step * 1e-6 + 1e-9

        values: list[float] = [nominal]
        value = nominal + step
        while value <= maximum + eps:
            values.append(min(value, maximum))
            value += step

        value = nominal - step
        while value >= minimum - eps:
            values.append(max(value, minimum))
            value -= step

        # Floating-point sınırlarında tekrarları temizle.
        unique: list[float] = []
        for candidate in values:
            if not any(abs(candidate - existing) <= eps for existing in unique):
                unique.append(candidate)
        return tuple(unique)

    def _find_alignment_point(
        self,
        zones: Iterable[tuple[float, float, float]],
        *,
        forward_n: float,
        forward_e: float,
        p1_radius_m: float,
        cfg: KamikazeApproachConfig,
    ) -> tuple[float, float, float, float | None, bool, bool]:
        """P2'yi aynı radyal üzerinde bul; P2->P1 segmentine HSS manevrası ekleme."""
        candidates = self._alignment_distance_candidates(cfg)
        nominal_result: tuple[float, float, float, float | None] | None = None

        for index, distance_m in enumerate(candidates):
            radius_m = p1_radius_m + distance_m
            p2_n = forward_n * radius_m
            p2_e = forward_e * radius_m
            clearance = self._minimum_point_clearance(
                zones,
                point_n=p2_n,
                point_e=p2_e,
            )
            if index == 0:
                nominal_result = (distance_m, p2_n, p2_e, clearance)
            if clearance is None or clearance > 0.0:
                return distance_m, p2_n, p2_e, clearance, True, index != 0

        # Toggle kapalıysa, risk bilgisiyle nominal P2'yi döndür. Bu nokta
        # otomatik olarak güvenli ilan edilmez; handoff alignment_point_valid=False verir.
        assert nominal_result is not None
        distance_m, p2_n, p2_e, clearance = nominal_result
        return distance_m, p2_n, p2_e, clearance, False, False

    def _adaptive_spacing_select(
        self,
        ordered: list[ApproachCorridor],
        limit: int,
    ) -> tuple[list[ApproachCorridor], float]:
        target_count = min(max(1, int(limit)), len(ordered))
        preferred = max(0.0, float(self.config.preferred_spacing_deg))
        steps = sorted(set((preferred, 6.0, 4.0, 3.0, 2.0, 1.0, 0.0)), reverse=True)
        best: list[ApproachCorridor] = []
        used = 0.0
        for spacing in steps:
            chosen: list[ApproachCorridor] = []
            for candidate in ordered:
                if spacing > 0.0 and any(
                    angular_distance_deg(candidate.angle_deg, old.angle_deg) < spacing
                    for old in chosen
                ):
                    continue
                chosen.append(candidate)
                if len(chosen) >= target_count:
                    break
            if len(chosen) > len(best):
                best, used = chosen, spacing
            if len(chosen) >= target_count:
                return chosen[:target_count], spacing
        return best[:target_count], used

    def _zone_local(self, zone: HssZone, qr: Point) -> tuple[float, float, float]:
        north, east = self._to_local(zone.latitude, zone.longitude, qr)
        return north, east, float(zone.radius_m)

    def _minimum_clearance(
        self,
        zones: Iterable[tuple[float, float, float]],
        *,
        start_n: float,
        start_e: float,
        end_n: float,
        end_e: float,
        half_width_m: float,
    ) -> float | None:
        minimum: float | None = None
        for zone_n, zone_e, radius in zones:
            distance = self._distance_point_to_segment(
                zone_n, zone_e, start_n, start_e, end_n, end_e
            )
            clearance = distance - (radius + half_width_m + self.config.hss_margin_m)
            minimum = clearance if minimum is None else min(minimum, clearance)
        return minimum

    def _minimum_point_clearance(
        self,
        zones: Iterable[tuple[float, float, float]],
        *,
        point_n: float,
        point_e: float,
    ) -> float | None:
        minimum: float | None = None
        for zone_n, zone_e, radius in zones:
            distance = math.hypot(point_n - zone_n, point_e - zone_e)
            clearance = distance - (radius + self.config.hss_margin_m)
            minimum = clearance if minimum is None else min(minimum, clearance)
        return minimum

    @staticmethod
    def _clearance_sort_value(value: float | None) -> float:
        return math.inf if value is None else float(value)

    @staticmethod
    def _distance_point_to_segment(
        point_n: float,
        point_e: float,
        start_n: float,
        start_e: float,
        end_n: float,
        end_e: float,
    ) -> float:
        segment_n = end_n - start_n
        segment_e = end_e - start_e
        segment_sq = segment_n * segment_n + segment_e * segment_e
        if segment_sq <= 1e-12:
            return math.hypot(point_n - start_n, point_e - start_e)
        projection = (
            (point_n - start_n) * segment_n
            + (point_e - start_e) * segment_e
        ) / segment_sq
        projection = max(0.0, min(1.0, projection))
        nearest_n = start_n + projection * segment_n
        nearest_e = start_e + projection * segment_e
        return math.hypot(point_n - nearest_n, point_e - nearest_e)

    @staticmethod
    def _to_local(latitude: float, longitude: float, origin: Point) -> tuple[float, float]:
        lat0 = math.radians(origin.latitude)
        north = math.radians(latitude - origin.latitude) * EARTH_RADIUS_M
        east = (
            math.radians(longitude - origin.longitude)
            * EARTH_RADIUS_M
            * max(0.05, math.cos(lat0))
        )
        return north, east

    @staticmethod
    def _point_from_local(
        north: float,
        east: float,
        origin: Point,
        altitude: float,
    ) -> Point:
        lat0 = math.radians(origin.latitude)
        latitude = origin.latitude + math.degrees(north / EARTH_RADIUS_M)
        longitude = origin.longitude + math.degrees(
            east / (EARTH_RADIUS_M * max(0.05, math.cos(lat0)))
        )
        return Point(latitude, longitude, altitude)

    def _polygon(
        self,
        origin: Point,
        angle_deg: float,
        length_m: float,
        width_m: float,
    ) -> tuple[tuple[float, float], ...]:
        theta = math.radians(angle_deg)
        forward_n = math.cos(theta)
        forward_e = math.sin(theta)
        right_n = -math.sin(theta)
        right_e = math.cos(theta)
        half = width_m / 2.0
        end_n = forward_n * length_m
        end_e = forward_e * length_m
        local = (
            (-right_n * half, -right_e * half),
            (right_n * half, right_e * half),
            (end_n + right_n * half, end_e + right_e * half),
            (end_n - right_n * half, end_e - right_e * half),
        )
        points = tuple(self._point_from_local(n, e, origin, 0.0) for n, e in local)
        return tuple((point.latitude, point.longitude) for point in points)


def _finite(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} sayısal olmalı") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} sonlu olmalı")
    return number
