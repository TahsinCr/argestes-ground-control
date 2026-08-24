from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import math
import threading
import time
from typing import Any, Iterable

from src.core.events import Subscription
from src.models.competition import HssZone, QrCoordinate
from src.models.geometry import EARTH_RADIUS_M, Point
from src.models.kamikaze_approach import (
    ApproachCorridor,
    KamikazeApproachConfig,
    KamikazeApproachPlan,
)
from src.models.telemetry import TelemetrySnapshot
from src.services.abstracts.competition import CompetitionService
from src.services.abstracts.onboard import OnboardMissionServiceContract
from src.services.abstracts.telemetry import TelemetryService
from src.services.logging_service import ServiceLogger


STATE_IDLE = "idle"
STATE_SELECTING = "selecting"
STATE_CORRIDOR_SELECTED = "corridor_selected"
STATE_MISSION_ACTIVE = "mission_active"


def angular_distance_deg(a_deg: float, b_deg: float) -> float:
    delta = (float(a_deg) - float(b_deg) + 180.0) % 360.0 - 180.0
    return abs(delta)


class KamikazeApproachService:
    """GCS-only P2 -> P1 -> QR corridor selector.

    The service owns geometry, HSS hard filtering, wind ranking, Top-5
    diversification and selection locking. It intentionally does not touch UI
    widgets and does not send MAVLink/navigation commands itself.
    """

    def __init__(
        self,
        competition: CompetitionService,
        telemetry: TelemetryService,
        logger: ServiceLogger | None = None,
        config: KamikazeApproachConfig | None = None,
        onboard: OnboardMissionServiceContract | None = None,
    ) -> None:
        self._competition = competition
        self._telemetry = telemetry
        self._logger = logger
        self._config = config or KamikazeApproachConfig()
        self._onboard = onboard
        self._lock = threading.RLock()
        self._revision = 0
        self._last_plan: KamikazeApproachPlan | None = None
        self._selection_state = STATE_IDLE
        self._last_wind_refresh_monotonic: float | None = None
        self._subscription: Subscription | None = None

    @property
    def config(self) -> KamikazeApproachConfig:
        with self._lock:
            return self._config

    @property
    def last_plan(self) -> KamikazeApproachPlan | None:
        with self._lock:
            return self._last_plan

    @property
    def selection_state(self) -> str:
        with self._lock:
            return self._selection_state

    def start(self) -> None:
        with self._lock:
            if self._subscription is not None:
                return
            updated = getattr(self._telemetry, "updated", None)
            if updated is not None:
                self._subscription = updated.subscribe(self._on_telemetry_updated)

    def stop(self) -> None:
        with self._lock:
            subscription = self._subscription
            self._subscription = None
        if subscription is not None:
            subscription.cancel()

    def update_config(self, **changes: Any) -> KamikazeApproachConfig:
        """Update geometry/policy settings for the next unlocked calculation."""
        with self._lock:
            if self._selection_state in {STATE_CORRIDOR_SELECTED, STATE_MISSION_ACTIVE}:
                raise RuntimeError("Seçili/aktif koridor kilitliyken yaklaşma ayarları değiştirilemez")
            self._config = replace(self._config, **changes)
            return self._config

    def begin_selection(self, *, force_rebuild: bool = True) -> KamikazeApproachPlan:
        """Open a new operator selection session and build current Top-5."""
        with self._lock:
            if self._selection_state == STATE_MISSION_ACTIVE:
                raise RuntimeError("Aktif görev varken yeni koridor seçimi başlatılamaz")
            self._selection_state = STATE_SELECTING
            self._last_wind_refresh_monotonic = None
        return self.build_plan(force=force_rebuild)

    def build_plan(self, *, force: bool = False) -> KamikazeApproachPlan:
        with self._lock:
            if (
                not force
                and self._last_plan is not None
                and self._selection_state in {STATE_CORRIDOR_SELECTED, STATE_MISSION_ACTIVE}
            ):
                return self._last_plan
            if self._selection_state == STATE_IDLE:
                self._selection_state = STATE_SELECTING

        qr = self._competition.qr_coordinate
        if qr is None:
            raise ValueError("TEKNOFEST QR koordinatı henüz mevcut değil")

        telemetry = self._telemetry.current
        wind_from, wind_speed, wind_status = self._wind_from_telemetry(telemetry)
        ownship = {
            "latitude": float(telemetry.latitude),
            "longitude": float(telemetry.longitude),
            "altitude_agl": float(telemetry.altitude_agl),
            "heading": float(telemetry.heading),
            "ground_speed": float(telemetry.ground_speed),
            "position_valid": bool(telemetry.position_valid),
        }
        plan = self.build_plan_from_values(
            qr=qr,
            zones=self._competition.hss_zones,
            wind_from_deg=wind_from,
            wind_speed_mps=wind_speed,
            wind_status=wind_status,
            ownship=ownship,
        )
        with self._lock:
            self._last_wind_refresh_monotonic = time.monotonic()
        return plan

    def build_plan_from_values(
        self,
        *,
        qr: QrCoordinate,
        zones: Iterable[HssZone],
        wind_from_deg: float | None,
        wind_speed_mps: float | None = None,
        wind_status: str | None = None,
        ownship: dict[str, Any] | None = None,
    ) -> KamikazeApproachPlan:
        cfg = self.config
        zones = tuple(zones)

        normalized_wind: float | None = None
        normalized_speed: float | None = None
        if wind_from_deg is not None:
            normalized_wind = _finite(wind_from_deg, "wind_from_deg") % 360.0
        if wind_speed_mps is not None:
            normalized_speed = max(0.0, _finite(wind_speed_mps, "wind_speed_mps"))

        if wind_status is None:
            if normalized_wind is None or normalized_speed is None:
                wind_status = "unavailable"
            elif normalized_speed < cfg.wind_min_speed_mps:
                wind_status = "too_weak"
            else:
                wind_status = "available"
        wind_rank_applied = wind_status == "available" and normalized_wind is not None

        p1_radius_m = cfg.dive_start_radius_m
        p2_radius_m = cfg.nominal_alignment_radius_m
        pullup_radius_m = cfg.pullup_radius_m
        half_width_m = cfg.corridor_width_m / 2.0
        qr_point = Point(qr.latitude, qr.longitude, cfg.target_altitude_m)
        hss_local = [self._zone_local(zone, qr_point) for zone in zones]

        safe: list[ApproachCorridor] = []
        rejected_entry = 0
        rejected_dive = 0
        rejected_alignment_point = 0
        rejected_alignment_segment = 0

        for angle in range(0, 360, cfg.angle_step_deg):
            theta = math.radians(angle)
            radial_n = math.cos(theta)
            radial_e = math.sin(theta)
            p1_n = radial_n * p1_radius_m
            p1_e = radial_e * p1_radius_m
            p2_n = radial_n * p2_radius_m
            p2_e = radial_e * p2_radius_m

            p2_clearance = self._minimum_point_clearance(
                hss_local,
                point_n=p2_n,
                point_e=p2_e,
                extra_margin_m=half_width_m,
            )
            if p2_clearance is not None and p2_clearance <= 0.0:
                rejected_alignment_point += 1
                continue

            p1_clearance = self._minimum_point_clearance(
                hss_local,
                point_n=p1_n,
                point_e=p1_e,
                extra_margin_m=half_width_m,
            )
            if p1_clearance is not None and p1_clearance <= 0.0:
                rejected_entry += 1
                continue

            alignment_clearance = self._minimum_clearance(
                hss_local,
                start_n=p2_n,
                start_e=p2_e,
                end_n=p1_n,
                end_e=p1_e,
                half_width_m=half_width_m,
            )
            if alignment_clearance is not None and alignment_clearance <= 0.0:
                rejected_alignment_segment += 1
                continue

            dive_clearance = self._minimum_clearance(
                hss_local,
                start_n=p1_n,
                start_e=p1_e,
                end_n=0.0,
                end_e=0.0,
                half_width_m=half_width_m,
            )
            if dive_clearance is not None and dive_clearance <= 0.0:
                rejected_dive += 1
                continue

            full_clearance = _minimum_optional(
                p2_clearance,
                p1_clearance,
                alignment_clearance,
                dive_clearance,
            )
            inbound = (float(angle) + 180.0) % 360.0
            wind_error = (
                angular_distance_deg(inbound, normalized_wind)
                if wind_rank_applied and normalized_wind is not None
                else None
            )
            p1 = self._point_from_local(p1_n, p1_e, qr_point, cfg.start_altitude_m)
            p2 = self._point_from_local(p2_n, p2_e, qr_point, cfg.start_altitude_m)
            pullup = self._point_from_local(
                radial_n * pullup_radius_m,
                radial_e * pullup_radius_m,
                qr_point,
                cfg.pullup_altitude_m,
            )
            safe.append(
                ApproachCorridor(
                    angle_deg=angle,
                    inbound_heading_deg=inbound,
                    wind_error_deg=wind_error,
                    wind_rank_applied=wind_rank_applied,
                    corridor_clearance_m=full_clearance,
                    alignment_segment_clearance_m=alignment_clearance,
                    dive_segment_clearance_m=dive_clearance,
                    alignment_point_clearance_m=p2_clearance,
                    entry_point_clearance_m=p1_clearance,
                    alignment_distance_m=cfg.alignment_nominal_m,
                    entry=p1,
                    lineup=p2,
                    dive_start=p1,
                    pullup=pullup,
                    polygon=self._segment_polygon(
                        qr_point,
                        start_n=p2_n,
                        start_e=p2_e,
                        end_n=0.0,
                        end_e=0.0,
                        width_m=cfg.corridor_width_m,
                    ),
                )
            )

        if not safe:
            raise ValueError("HSS hard-filter sonrası güvenli P2→P1→QR koridoru kalmadı")

        if wind_rank_applied:
            ordered = sorted(
                safe,
                key=lambda item: (
                    float(item.wind_error_deg if item.wind_error_deg is not None else 180.0),
                    -self._clearance_sort_value(item.corridor_clearance_m),
                    item.angle_deg,
                ),
            )
        else:
            ordered = sorted(
                safe,
                key=lambda item: (
                    -self._clearance_sort_value(item.corridor_clearance_m),
                    item.angle_deg,
                ),
            )

        top, used_spacing = self._adaptive_spacing_select(ordered, cfg.max_candidates)
        if not top:
            raise ValueError("Güvenli Top-5 koridor üretilemedi")

        recommended_angle = top[0].angle_deg
        headwind_count = sum(
            1
            for candidate in safe
            if candidate.wind_error_deg is not None and candidate.wind_error_deg <= 90.0
        )

        with self._lock:
            self._revision += 1
            selected_angle: int | None = None
            selection_source = "none"
            if cfg.auto_select_mode == 1:
                selected_angle = recommended_angle
                selection_source = "auto"
                self._selection_state = STATE_CORRIDOR_SELECTED
            elif self._selection_state == STATE_IDLE:
                self._selection_state = STATE_SELECTING

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
                selection_state=self._selection_state,
                wind_from_deg=normalized_wind,
                wind_speed_mps=normalized_speed,
                wind_status=wind_status,
                wind_rank_applied=wind_rank_applied,
                hss_count=len(zones),
                hss_entry_point_rejected_count=rejected_entry,
                hss_corridor_rejected_count=rejected_dive,
                hss_alignment_point_rejected_count=rejected_alignment_point,
                hss_alignment_segment_rejected_count=rejected_alignment_segment,
                safe_corridor_count=len(safe),
                headwind_candidate_count=headwind_count,
                wind_pool_count=len(safe),
                used_spacing_deg=used_spacing,
                ownship=dict(ownship or {}),
            )
            self._last_plan = plan

        self._log_wind_status(plan)
        if self._logger is not None:
            self._logger.event(
                "kamikaze_approach.plan_built",
                "Kamikaze Top-5 yaklaşma koridorları hesaplandı",
                details={
                    "revision": plan.revision,
                    "recommended_angle_deg": plan.recommended_angle_deg,
                    "selected_angle_deg": plan.selected_angle_deg,
                    "selection_state": plan.selection_state,
                    "safe_corridor_count": plan.safe_corridor_count,
                    "used_spacing_deg": plan.used_spacing_deg,
                    "wind_status": plan.wind_status,
                    "wind_rank_applied": plan.wind_rank_applied,
                    "p1_radius_m": round(cfg.dive_start_radius_m, 3),
                    "p2_p1_distance_m": round(cfg.alignment_nominal_m, 3),
                },
            )
        return plan

    def select(self, angle_deg: int, *, plan_revision: int | None = None) -> KamikazeApproachPlan:
        requested = int(angle_deg) % 360
        with self._lock:
            if self._selection_state == STATE_MISSION_ACTIVE:
                raise RuntimeError("Aktif görev sırasında koridor değiştirilemez")
            plan = self._last_plan
            if plan is None:
                raise ValueError("Aktif yaklaşma planı yok")
            if plan_revision is not None and int(plan_revision) != plan.revision:
                raise ValueError("Eski yaklaşma planı revision'ı ile seçim yapılamaz")
            allowed = {corridor.angle_deg for corridor in plan.corridors}
            if requested not in allowed:
                raise ValueError("Seçilen koridor mevcut Top-5 içinde değil")
            self._selection_state = STATE_CORRIDOR_SELECTED
            updated = self._with_selection(
                plan,
                requested,
                source="operator",
                selection_state=self._selection_state,
            )
            self._last_plan = updated

        if self._logger is not None:
            self._logger.event(
                "kamikaze_approach.corridor_locked",
                "Kamikaze koridoru operatör tarafından seçildi ve kilitlendi",
                details={"revision": updated.revision, "angle_deg": requested},
            )
        return updated

    def clear_selection(self, *, plan_revision: int | None = None) -> KamikazeApproachPlan:
        with self._lock:
            if self._selection_state == STATE_MISSION_ACTIVE:
                raise RuntimeError("Aktif görev sırasında koridor kilidi kaldırılamaz")
            plan = self._last_plan
            if plan is None:
                raise ValueError("Aktif yaklaşma planı yok")
            if plan_revision is not None and int(plan_revision) != plan.revision:
                raise ValueError("Eski yaklaşma planı revision'ı ile seçim temizlenemez")
            self._selection_state = STATE_SELECTING
            self._last_wind_refresh_monotonic = None
            updated = self._with_selection(
                plan,
                None,
                source="none",
                selection_state=self._selection_state,
            )
            self._last_plan = updated
            return updated

    def start_selected_mission(
        self,
        *,
        plan_revision: int | None = None,
        source: str = "ui",
    ):
        """Send the locked corridor to onboard and start `kamikaze.transit`.

        UI code only needs to call this backend method after `select()`.  The
        corridor is marked mission-active only after onboard accepted both the
        immutable approach contract and task start request.
        """
        with self._lock:
            plan = self._last_plan
            if plan is None or plan.selected_corridor is None:
                raise RuntimeError("Görev başlamadan önce Top-5 içinden koridor seçilmeli")
            if plan_revision is not None and int(plan_revision) != plan.revision:
                raise ValueError("Eski yaklaşma planı revision'ı ile görev başlatılamaz")
            if self._selection_state == STATE_MISSION_ACTIVE:
                raise RuntimeError("Kamikaze transit görevi zaten aktif")
            onboard = self._onboard
        if onboard is None:
            raise RuntimeError("Onboard mission service kamikaze yaklaşım servisine bağlı değil")

        receipt = onboard.start_kamikaze_transit(plan, source=source)
        if not receipt.accepted:
            raise RuntimeError(receipt.message or "Onboard kamikaze transit başlangıcını reddetti")
        updated = self.mark_mission_started(plan_revision=plan.revision)
        if self._logger is not None:
            self._logger.event(
                "kamikaze_approach.mission_started",
                "Kilitli koridor onboard kamikaze.transit görevine aktarıldı",
                details={
                    "revision": updated.revision,
                    "angle_deg": updated.selected_angle_deg,
                    "request_id": receipt.request_id,
                    "task": "kamikaze.transit",
                },
            )
        return receipt

    def mark_mission_started(self, *, plan_revision: int | None = None) -> KamikazeApproachPlan:
        with self._lock:
            plan = self._last_plan
            if plan is None or plan.selected_corridor is None:
                raise RuntimeError("Görev başlamadan önce Top-5 içinden koridor seçilmeli")
            if plan_revision is not None and int(plan_revision) != plan.revision:
                raise ValueError("Eski yaklaşma planı revision'ı ile görev başlatılamaz")
            self._selection_state = STATE_MISSION_ACTIVE
            updated = replace(plan, selection_state=self._selection_state)
            self._last_plan = updated
            return updated

    def mark_mission_finished(self) -> None:
        with self._lock:
            self._selection_state = STATE_IDLE
            self._last_plan = None
            self._last_wind_refresh_monotonic = None

    def selected_navigation_handoff(self) -> dict[str, Any]:
        with self._lock:
            if self._last_plan is None:
                return {"ready": False, "reason": "no_plan"}
            return self._last_plan.navigation_handoff()

    def refresh_if_due(self, *, now_monotonic: float | None = None) -> KamikazeApproachPlan | None:
        """Recompute Top-5 at most once per configured wind refresh interval.

        Selection/mission locks are absolute: after operator selection this
        method becomes a no-op until the selection is explicitly cleared.
        """
        current = time.monotonic() if now_monotonic is None else float(now_monotonic)
        with self._lock:
            if self._selection_state != STATE_SELECTING or self._last_plan is None:
                return self._last_plan
            previous = self._last_wind_refresh_monotonic
            if previous is not None and current - previous < self._config.wind_refresh_interval_s:
                return self._last_plan
        return self.build_plan(force=True)

    def _on_telemetry_updated(self, _snapshot: TelemetrySnapshot) -> None:
        try:
            self.refresh_if_due()
        except Exception as exc:
            if self._logger is not None:
                self._logger.event(
                    "kamikaze_approach.wind_refresh_failed",
                    f"Rüzgar tabanlı koridor yenilemesi başarısız: {exc}",
                    severity="warning",
                )

    def _wind_from_telemetry(
        self,
        telemetry: TelemetrySnapshot,
    ) -> tuple[float | None, float | None, str]:
        received_at = getattr(telemetry, "wind_received_at", None)
        if not telemetry.autopilot_link or received_at is None:
            return None, None, "unavailable"
        if received_at.tzinfo is None:
            return None, None, "unavailable"
        wind_age_s = (datetime.now(UTC) - received_at.astimezone(UTC)).total_seconds()
        if wind_age_s < 0.0 or wind_age_s > self.config.wind_max_age_s:
            return None, None, "stale"
        try:
            direction = _finite(telemetry.wind_heading, "wind_heading") % 360.0
            speed = max(0.0, _finite(telemetry.wind_speed, "wind_speed"))
        except ValueError:
            return None, None, "unavailable"
        if speed < self.config.wind_min_speed_mps:
            return direction, speed, "too_weak"
        return direction, speed, "available"

    def _log_wind_status(self, plan: KamikazeApproachPlan) -> None:
        if self._logger is None:
            return
        if plan.wind_status == "unavailable":
            self._logger.event(
                "kamikaze_approach.wind_unavailable",
                "ArduPilot WIND verisi alınamadı; koridorlar yalnız HSS güvenliğine göre sıralandı",
                severity="warning",
            )
        elif plan.wind_status == "stale":
            self._logger.event(
                "kamikaze_approach.wind_stale",
                "ArduPilot WIND verisi eski; mevcut yenileme periyodu korunarak rüzgar sıralaması devre dışı bırakıldı",
                severity="warning",
            )
        elif plan.wind_status == "too_weak":
            self._logger.event(
                "kamikaze_approach.wind_too_weak",
                "Rüzgar çok düşük; rüzgar sıralaması devre dışı bırakıldı",
                details={"speed_mps": plan.wind_speed_mps},
            )

    @staticmethod
    def _with_selection(
        plan: KamikazeApproachPlan,
        angle_deg: int | None,
        *,
        source: str,
        selection_state: str,
    ) -> KamikazeApproachPlan:
        corridors = tuple(
            replace(corridor, selected=(angle_deg is not None and corridor.angle_deg == angle_deg))
            for corridor in plan.corridors
        )
        return replace(
            plan,
            corridors=corridors,
            selected_angle_deg=angle_deg,
            selection_source=source,
            selection_state=selection_state,
        )

    def _adaptive_spacing_select(
        self,
        ordered: list[ApproachCorridor],
        limit: int,
    ) -> tuple[list[ApproachCorridor], float]:
        target_count = min(max(1, int(limit)), len(ordered))
        configured = tuple(float(value) for value in self.config.spacing_sequence_deg)
        sequence = tuple(dict.fromkeys((self.config.preferred_spacing_deg, *configured, 1.0)))
        best: list[ApproachCorridor] = []
        used = sequence[-1]
        for spacing in sequence:
            chosen: list[ApproachCorridor] = []
            for candidate in ordered:
                if any(
                    angular_distance_deg(candidate.angle_deg, previous.angle_deg) < spacing
                    for previous in chosen
                ):
                    continue
                chosen.append(candidate)
                if len(chosen) >= target_count:
                    return chosen[:target_count], spacing
            if len(chosen) > len(best):
                best = chosen
                used = spacing
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
            clearance = distance - (radius + self.config.hss_margin_m + half_width_m)
            minimum = clearance if minimum is None else min(minimum, clearance)
        return minimum

    def _minimum_point_clearance(
        self,
        zones: Iterable[tuple[float, float, float]],
        *,
        point_n: float,
        point_e: float,
        extra_margin_m: float = 0.0,
    ) -> float | None:
        minimum: float | None = None
        for zone_n, zone_e, radius in zones:
            distance = math.hypot(point_n - zone_n, point_e - zone_e)
            clearance = distance - (radius + self.config.hss_margin_m + extra_margin_m)
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
            (point_n - start_n) * segment_n + (point_e - start_e) * segment_e
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
    def _point_from_local(north: float, east: float, origin: Point, altitude: float) -> Point:
        lat0 = math.radians(origin.latitude)
        latitude = origin.latitude + math.degrees(north / EARTH_RADIUS_M)
        longitude = origin.longitude + math.degrees(
            east / (EARTH_RADIUS_M * max(0.05, math.cos(lat0)))
        )
        return Point(latitude, longitude, altitude)

    def _segment_polygon(
        self,
        origin: Point,
        *,
        start_n: float,
        start_e: float,
        end_n: float,
        end_e: float,
        width_m: float,
    ) -> tuple[tuple[float, float], ...]:
        delta_n = end_n - start_n
        delta_e = end_e - start_e
        length = math.hypot(delta_n, delta_e)
        if length <= 1e-9:
            return ()
        right_n = -delta_e / length
        right_e = delta_n / length
        half = width_m / 2.0
        local = (
            (start_n - right_n * half, start_e - right_e * half),
            (start_n + right_n * half, start_e + right_e * half),
            (end_n + right_n * half, end_e + right_e * half),
            (end_n - right_n * half, end_e - right_e * half),
        )
        points = tuple(self._point_from_local(n, e, origin, 0.0) for n, e in local)
        return tuple((point.latitude, point.longitude) for point in points)


def _minimum_optional(*values: float | None) -> float | None:
    present = [float(value) for value in values if value is not None]
    return min(present) if present else None


def _finite(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} sayısal olmalı") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} sonlu olmalı")
    return number
