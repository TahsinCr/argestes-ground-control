from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math
import time
import unittest

from src.core.events import EventBus
from src.models.competition import HssZone, QrCoordinate
from src.models.geometry import Point
from src.models.kamikaze_approach import KamikazeApproachConfig
from src.models.telemetry import TelemetrySnapshot
from src.models.orin import OrinCommandReceipt
from src.services.kamikaze_approach import (
    STATE_CORRIDOR_SELECTED,
    STATE_MISSION_ACTIVE,
    STATE_SELECTING,
    KamikazeApproachService,
)


class _Competition:
    def __init__(self, qr: QrCoordinate, zones: tuple[HssZone, ...] = ()) -> None:
        self.qr_coordinate = qr
        self.hss_zones = zones

    def refresh_qr_coordinate(self) -> None:
        return None


class _Onboard:
    def __init__(self) -> None:
        self.plans = []

    def start_kamikaze_transit(self, plan, *, source="ui"):
        self.plans.append((plan, source))
        return OrinCommandReceipt(77, True, "started", {"execution": "starting"})


class _Telemetry:
    def __init__(self, snapshot: TelemetrySnapshot) -> None:
        self.current = snapshot
        self.updated = EventBus[TelemetrySnapshot]()


class KamikazeApproachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.qr = QrCoordinate(39.925533, 32.866287)
        self.telemetry = _Telemetry(self._snapshot(wind_heading=0.0, wind_speed=5.0))
        self.competition = _Competition(self.qr)

    def _snapshot(
        self,
        *,
        wind_heading: float,
        wind_speed: float,
        wind_received: bool = True,
    ) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            team_id=1,
            latitude=39.926,
            longitude=32.866,
            altitude_agl=130.0,
            altitude_msl=130.0,
            ground_speed=24.0,
            air_speed=24.0,
            roll=0.0,
            pitch=0.0,
            yaw=0.0,
            heading=180.0,
            climb_rate=0.0,
            battery_percent=90,
            battery_voltage=22.0,
            link_quality=100,
            link_quality_source="test",
            gps_satellites=15,
            gps_fix="3D",
            gps_hdop=0.8,
            flight_mode="GUIDED",
            mission_index=0,
            mission_total=0,
            armed=True,
            telemetry_link=True,
            video_link=True,
            autopilot_link=True,
            server_link=True,
            target_distance=0.0,
            wind_speed=wind_speed,
            wind_heading=wind_heading,
            cpu_load=10,
            packets_in=1,
            packets_out=1,
            bandwidth_kbps=1.0,
            position_valid=True,
            wind_received_at=datetime.now(UTC) if wind_received else None,
        )

    def _service(self, config: KamikazeApproachConfig | None = None) -> KamikazeApproachService:
        return KamikazeApproachService(
            self.competition,  # type: ignore[arg-type]
            self.telemetry,  # type: ignore[arg-type]
            config=config or KamikazeApproachConfig(),
        )

    def _point_on_angle(
        self,
        service: KamikazeApproachService,
        angle_deg: float,
        radius_m: float,
    ) -> Point:
        theta = math.radians(angle_deg)
        qr = Point(self.qr.latitude, self.qr.longitude)
        return service._point_from_local(
            math.cos(theta) * radius_m,
            math.sin(theta) * radius_m,
            qr,
            0.0,
        )

    def test_default_geometry_is_130_90_45_and_p2_is_fixed_150m_behind_p1(self) -> None:
        cfg = KamikazeApproachConfig()
        self.assertEqual(cfg.start_altitude_m, 130.0)
        self.assertEqual(cfg.pullup_altitude_m, 90.0)
        self.assertEqual(cfg.dive_angle_deg, 45.0)
        self.assertEqual(cfg.alignment_nominal_m, 150.0)
        self.assertAlmostEqual(cfg.dive_start_radius_m, 130.0, places=6)
        self.assertAlmostEqual(cfg.pullup_radius_m, 90.0, places=6)
        self.assertAlmostEqual(cfg.descent_horizontal_distance_m, 40.0, places=6)
        self.assertAlmostEqual(cfg.nominal_alignment_radius_m, 280.0, places=6)

    def test_changing_altitudes_and_angle_rederives_all_distances(self) -> None:
        service = self._service()
        service.update_config(
            start_altitude_m=110.0,
            pullup_altitude_m=70.0,
            dive_angle_deg=50.0,
            alignment_nominal_m=175.0,
        )
        cfg = service.config
        self.assertAlmostEqual(cfg.dive_start_radius_m, 92.3009594295, places=6)
        self.assertAlmostEqual(cfg.pullup_radius_m, 58.7369741824, places=6)
        self.assertAlmostEqual(cfg.descent_horizontal_distance_m, 33.5639852471, places=6)
        self.assertAlmostEqual(cfg.nominal_alignment_radius_m, 267.3009594295, places=6)

    def test_one_degree_generation_and_headwind_first_top5(self) -> None:
        service = self._service()
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=(),
            wind_from_deg=0.0,
            wind_speed_mps=5.0,
        )
        self.assertEqual(plan.safe_corridor_count, 360)
        self.assertEqual(plan.recommended_corridor.inbound_heading_deg, 0.0)
        self.assertEqual(plan.recommended_corridor.angle_deg, 180)
        self.assertEqual(plan.used_spacing_deg, 8.0)
        self.assertEqual(len(plan.corridors), 5)
        self.assertIsNone(plan.selected_angle_deg)
        self.assertEqual(plan.selection_state, STATE_SELECTING)
        for index, corridor in enumerate(plan.corridors):
            for other in plan.corridors[index + 1 :]:
                delta = abs((corridor.angle_deg - other.angle_deg + 180) % 360 - 180)
                self.assertGreaterEqual(delta, 8.0)

    def test_hss_at_p2_rejects_that_corridor_without_moving_p2(self) -> None:
        service = self._service()
        p2 = self._point_on_angle(service, 180.0, service.config.nominal_alignment_radius_m)
        zones = (HssZone(1, p2.latitude, p2.longitude, 2.0),)
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=zones,
            wind_from_deg=0.0,
            wind_speed_mps=5.0,
        )
        self.assertGreater(plan.hss_alignment_point_rejected_count, 0)
        self.assertNotIn(180, {item.angle_deg for item in plan.corridors})
        self.assertTrue(all(item.alignment_distance_m == 150.0 for item in plan.corridors))

    def test_hss_between_p2_and_p1_hard_rejects_corridor(self) -> None:
        service = self._service()
        radius = service.config.dive_start_radius_m + service.config.alignment_nominal_m / 2.0
        blocker = self._point_on_angle(service, 180.0, radius)
        zones = (HssZone(2, blocker.latitude, blocker.longitude, 3.0),)
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=zones,
            wind_from_deg=0.0,
            wind_speed_mps=5.0,
        )
        self.assertGreater(plan.hss_alignment_segment_rejected_count, 0)
        self.assertNotIn(180, {item.angle_deg for item in plan.corridors})

    def test_hss_between_p1_and_qr_hard_rejects_corridor(self) -> None:
        service = self._service()
        blocker = self._point_on_angle(service, 180.0, service.config.dive_start_radius_m / 2.0)
        zones = (HssZone(3, blocker.latitude, blocker.longitude, 3.0),)
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=zones,
            wind_from_deg=0.0,
            wind_speed_mps=5.0,
        )
        self.assertGreater(plan.hss_corridor_rejected_count, 0)
        self.assertNotIn(180, {item.angle_deg for item in plan.corridors})

    def test_hss_margin_and_corridor_half_width_are_both_enforced(self) -> None:
        cfg = KamikazeApproachConfig(hss_margin_m=5.0, corridor_width_m=1.0)
        service = self._service(cfg)
        # Put HSS centre 7 m laterally from the P2->P1 centreline. Radius 2 m
        # leaves 5 m geometric clearance, but required inflation is 5.5 m.
        p1_radius = cfg.dive_start_radius_m
        midpoint_radius = p1_radius + cfg.alignment_nominal_m / 2.0
        theta = math.radians(180.0)
        qr_point = Point(self.qr.latitude, self.qr.longitude)
        centre_n = math.cos(theta) * midpoint_radius
        centre_e = math.sin(theta) * midpoint_radius + 7.0
        blocker = service._point_from_local(centre_n, centre_e, qr_point, 0.0)
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=(HssZone(4, blocker.latitude, blocker.longitude, 2.0),),
            wind_from_deg=0.0,
            wind_speed_mps=5.0,
        )
        self.assertNotIn(180, {item.angle_deg for item in plan.corridors})

    def test_weak_wind_does_not_affect_ranking(self) -> None:
        service = self._service()
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=(),
            wind_from_deg=270.0,
            wind_speed_mps=0.4,
        )
        self.assertEqual(plan.wind_status, "too_weak")
        self.assertFalse(plan.wind_rank_applied)
        self.assertTrue(all(item.wind_error_deg is None for item in plan.corridors))

    def test_missing_autopilot_wind_is_detected_as_unavailable(self) -> None:
        self.telemetry.current = self._snapshot(
            wind_heading=0.0,
            wind_speed=0.0,
            wind_received=False,
        )
        service = self._service()
        plan = service.build_plan()
        self.assertEqual(plan.wind_status, "unavailable")
        self.assertFalse(plan.wind_rank_applied)
        self.assertIsNone(plan.wind_from_deg)

    def test_stale_wind_is_ignored_without_changing_refresh_cadence(self) -> None:
        snapshot = self._snapshot(wind_heading=90.0, wind_speed=8.0)
        snapshot.wind_received_at = datetime.now(UTC) - timedelta(seconds=10)
        self.telemetry.current = snapshot
        service = self._service(
            KamikazeApproachConfig(
                wind_max_age_s=5.0,
                wind_refresh_interval_s=120.0,
            )
        )

        plan = service.build_plan()

        self.assertEqual(plan.wind_status, "stale")
        self.assertFalse(plan.wind_rank_applied)
        self.assertEqual(service.config.wind_refresh_interval_s, 120.0)

    def test_manual_selection_locks_plan_and_mission_lock_blocks_changes(self) -> None:
        service = self._service()
        plan = service.build_plan()
        chosen = plan.corridors[1]
        selected = service.select(chosen.angle_deg, plan_revision=plan.revision)
        self.assertEqual(selected.selection_state, STATE_CORRIDOR_SELECTED)
        self.assertEqual(selected.selected_angle_deg, chosen.angle_deg)

        old_revision = selected.revision
        self.telemetry.current = self._snapshot(wind_heading=180.0, wind_speed=8.0)
        refreshed = service.refresh_if_due(now_monotonic=time.monotonic() + 1000.0)
        assert refreshed is not None
        self.assertEqual(refreshed.revision, old_revision)
        self.assertEqual(refreshed.selected_angle_deg, chosen.angle_deg)

        active = service.mark_mission_started(plan_revision=old_revision)
        self.assertEqual(active.selection_state, STATE_MISSION_ACTIVE)
        with self.assertRaises(RuntimeError):
            service.update_config(dive_angle_deg=50.0)
        with self.assertRaises(RuntimeError):
            service.clear_selection()

    def test_unlocked_plan_can_refresh_after_two_minutes(self) -> None:
        service = self._service()
        plan = service.build_plan()
        self.assertEqual(plan.recommended_corridor.inbound_heading_deg, 0.0)
        self.telemetry.current = self._snapshot(wind_heading=90.0, wind_speed=5.0)
        refreshed = service.refresh_if_due(now_monotonic=time.monotonic() + 121.0)
        assert refreshed is not None
        self.assertGreater(refreshed.revision, plan.revision)
        self.assertEqual(refreshed.recommended_corridor.inbound_heading_deg, 90.0)

    def test_spacing_relaxes_to_one_degree_when_only_close_options_are_available(self) -> None:
        service = self._service()
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=(),
            wind_from_deg=0.0,
            wind_speed_mps=5.0,
        )
        base = plan.corridors[0]
        ordered = [replace(base, angle_deg=angle) for angle in range(100, 105)]
        chosen, used = service._adaptive_spacing_select(ordered, 5)
        self.assertEqual(len(chosen), 5)
        self.assertEqual(used, 1.0)

    def test_start_selected_mission_hands_locked_plan_to_onboard_and_freezes_state(self) -> None:
        onboard = _Onboard()
        service = KamikazeApproachService(
            self.competition,  # type: ignore[arg-type]
            self.telemetry,  # type: ignore[arg-type]
            onboard=onboard,  # type: ignore[arg-type]
        )
        plan = service.build_plan()
        selected = service.select(plan.corridors[0].angle_deg, plan_revision=plan.revision)
        receipt = service.start_selected_mission(plan_revision=selected.revision)
        self.assertTrue(receipt.accepted)
        self.assertEqual(len(onboard.plans), 1)
        self.assertEqual(onboard.plans[0][0].selected_angle_deg, selected.selected_angle_deg)
        self.assertEqual(service.selection_state, STATE_MISSION_ACTIVE)
        frozen = service.refresh_if_due(now_monotonic=time.monotonic() + 1000.0)
        assert frozen is not None
        self.assertEqual(frozen.revision, selected.revision)

    def test_navigation_handoff_is_not_ready_until_operator_selects(self) -> None:
        service = self._service()
        plan = service.build_plan()
        self.assertFalse(plan.navigation_handoff()["ready"])
        selected = service.select(plan.corridors[0].angle_deg, plan_revision=plan.revision)
        handoff = selected.navigation_handoff()
        self.assertTrue(handoff["ready"])
        self.assertEqual([item["role"] for item in handoff["waypoints"]], ["alignment", "dive_entry"])
        self.assertAlmostEqual(
            Point(**{
                "latitude": handoff["alignment_point"]["latitude"],
                "longitude": handoff["alignment_point"]["longitude"],
                "altitude": handoff["alignment_point"]["altitude"],
            }).distance_to(
                Point(**{
                    "latitude": handoff["dive_entry_point"]["latitude"],
                    "longitude": handoff["dive_entry_point"]["longitude"],
                    "altitude": handoff["dive_entry_point"]["altitude"],
                })
            ),
            150.0,
            delta=0.05,
        )


if __name__ == "__main__":
    unittest.main()
