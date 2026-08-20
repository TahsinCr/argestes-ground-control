from __future__ import annotations

import math
import unittest

from src.models.competition import HssZone, QrCoordinate
from src.models.geometry import Point
from src.models.kamikaze_approach import KamikazeApproachConfig
from src.models.telemetry import TelemetrySnapshot
from src.services.kamikaze_approach import KamikazeApproachService


class _Competition:
    def __init__(self, qr: QrCoordinate, zones: tuple[HssZone, ...] = ()) -> None:
        self.qr_coordinate = qr
        self.hss_zones = zones


class _Telemetry:
    def __init__(self, snapshot: TelemetrySnapshot) -> None:
        self.current = snapshot


class KamikazeApproachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.qr = QrCoordinate(39.925533, 32.866287)
        snapshot = TelemetrySnapshot(
            team_id=1,
            latitude=39.926,
            longitude=32.866,
            altitude_agl=110.0,
            altitude_msl=110.0,
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
            wind_speed=5.0,
            wind_heading=0.0,
            cpu_load=10,
            packets_in=1,
            packets_out=1,
            bandwidth_kbps=1.0,
            position_valid=True,
        )
        self.competition = _Competition(self.qr)
        self.telemetry = _Telemetry(snapshot)

    def _service(self, config: KamikazeApproachConfig | None = None) -> KamikazeApproachService:
        return KamikazeApproachService(
            self.competition,  # type: ignore[arg-type]
            self.telemetry,  # type: ignore[arg-type]
            config=config or KamikazeApproachConfig(),
        )

    def _point_on_angle(self, service: KamikazeApproachService, angle_deg: float, radius_m: float) -> Point:
        theta = math.radians(angle_deg)
        qr = Point(self.qr.latitude, self.qr.longitude)
        return service._point_from_local(
            math.cos(theta) * radius_m,
            math.sin(theta) * radius_m,
            qr,
            0.0,
        )

    def test_default_geometry_is_110_70_50_and_is_fully_derived(self) -> None:
        cfg = KamikazeApproachConfig()
        self.assertEqual(cfg.start_altitude_m, 110.0)
        self.assertEqual(cfg.pullup_altitude_m, 70.0)
        self.assertEqual(cfg.dive_angle_deg, 50.0)
        self.assertAlmostEqual(cfg.dive_start_radius_m, 110.0 / math.tan(math.radians(50.0)), places=9)
        self.assertAlmostEqual(cfg.pullup_radius_m, 70.0 / math.tan(math.radians(50.0)), places=9)
        self.assertAlmostEqual(cfg.descent_horizontal_distance_m, 40.0 / math.tan(math.radians(50.0)), places=9)
        self.assertAlmostEqual(cfg.dive_start_radius_m, 92.3009594295, places=6)
        self.assertAlmostEqual(cfg.pullup_radius_m, 58.7369741824, places=6)
        self.assertAlmostEqual(cfg.descent_horizontal_distance_m, 33.5639852471, places=6)

    def test_p1_p2_and_pullup_are_collinear_and_handoff_returns_two_navigation_points(self) -> None:
        service = self._service()
        plan = service.build_plan()
        corridor = plan.selected_corridor
        self.assertIsNotNone(corridor)
        assert corridor is not None
        qr = Point(self.qr.latitude, self.qr.longitude)
        self.assertAlmostEqual(qr.distance_to(corridor.dive_start), plan.config.dive_start_radius_m, delta=0.05)
        self.assertAlmostEqual(qr.distance_to(corridor.pullup), plan.config.pullup_radius_m, delta=0.05)
        self.assertAlmostEqual(corridor.dive_start.distance_to(corridor.lineup), 50.0, delta=0.05)
        self.assertAlmostEqual(qr.distance_to(corridor.lineup), plan.config.dive_start_radius_m + 50.0, delta=0.05)

        handoff = plan.navigation_handoff()
        self.assertTrue(handoff["ready"])
        self.assertEqual([item["role"] for item in handoff["waypoints"]], ["alignment", "dive_entry"])
        self.assertEqual(handoff["alignment_goto_parameters"]["altitude"], 110.0)
        self.assertEqual(handoff["dive_entry_goto_parameters"]["altitude"], 110.0)
        self.assertEqual(handoff["pullup_point"]["altitude"], 70.0)

    def test_changing_altitude_and_angle_updates_every_dependent_distance(self) -> None:
        service = self._service()
        service.update_config(
            start_altitude_m=120.0,
            pullup_altitude_m=80.0,
            dive_angle_deg=45.0,
            alignment_nominal_m=60.0,
        )
        plan = service.build_plan()
        cfg = plan.config
        self.assertAlmostEqual(cfg.dive_start_radius_m, 120.0, places=6)
        self.assertAlmostEqual(cfg.pullup_radius_m, 80.0, places=6)
        self.assertAlmostEqual(cfg.descent_horizontal_distance_m, 40.0, places=6)
        corridor = plan.recommended_corridor
        qr = Point(self.qr.latitude, self.qr.longitude)
        self.assertAlmostEqual(qr.distance_to(corridor.dive_start), 120.0, delta=0.05)
        self.assertAlmostEqual(qr.distance_to(corridor.pullup), 80.0, delta=0.05)
        self.assertAlmostEqual(corridor.dive_start.distance_to(corridor.lineup), 60.0, delta=0.05)
        self.assertAlmostEqual(qr.distance_to(corridor.lineup), 180.0, delta=0.05)

    def test_alignment_search_prefers_moving_p2_farther_before_closer(self) -> None:
        service = self._service()
        p1_radius = service.config.dive_start_radius_m
        nominal_p2 = self._point_on_angle(service, 180.0, p1_radius + 50.0)
        self.competition.hss_zones = (
            HssZone(1, nominal_p2.latitude, nominal_p2.longitude, 2.0),
        )
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=self.competition.hss_zones,
            wind_from_deg=0.0,  # angle 180 -> inbound 0, therefore preferred
        )
        corridor = next(item for item in plan.corridors if item.angle_deg == 180)
        self.assertTrue(corridor.alignment_point_valid)
        self.assertTrue(corridor.alignment_fallback_used)
        # radius=2, exact boundary is invalid (<=0), so first valid farther point is 53 m.
        self.assertAlmostEqual(corridor.alignment_distance_m, 53.0, places=6)

    def test_hss_between_p2_and_p1_does_not_reject_or_realign_corridor(self) -> None:
        service = self._service()
        p1_radius = service.config.dive_start_radius_m
        between = self._point_on_angle(service, 180.0, p1_radius + 25.0)
        self.competition.hss_zones = (
            HssZone(2, between.latitude, between.longitude, 2.0),
        )
        plan = service.build_plan_from_values(
            qr=self.qr,
            zones=self.competition.hss_zones,
            wind_from_deg=0.0,
        )
        corridor = next(item for item in plan.corridors if item.angle_deg == 180)
        self.assertAlmostEqual(corridor.alignment_distance_m, 50.0, places=6)
        self.assertTrue(corridor.alignment_point_valid)
        self.assertFalse(corridor.alignment_fallback_used)

    def test_no_safe_p2_invalidates_by_default_but_can_be_disabled(self) -> None:
        base = KamikazeApproachConfig(max_candidates=20, preferred_spacing_deg=0.0)
        service = self._service(base)
        p1_radius = service.config.dive_start_radius_m
        blocker = self._point_on_angle(service, 180.0, p1_radius + 70.0)
        zones = (HssZone(3, blocker.latitude, blocker.longitude, 35.0),)
        plan = service.build_plan_from_values(qr=self.qr, zones=zones, wind_from_deg=0.0)
        self.assertGreater(plan.hss_alignment_point_rejected_count, 0)
        self.assertNotIn(180, {item.angle_deg for item in plan.corridors})

        permissive = self._service(
            KamikazeApproachConfig(
                max_candidates=20,
                preferred_spacing_deg=0.0,
                invalidate_if_no_alignment_point=False,
            )
        )
        plan2 = permissive.build_plan_from_values(qr=self.qr, zones=zones, wind_from_deg=0.0)
        corridor = next(item for item in plan2.corridors if item.angle_deg == 180)
        self.assertFalse(corridor.alignment_point_valid)
        self.assertAlmostEqual(corridor.alignment_distance_m, 50.0, places=6)

    def test_manual_selection_uses_same_precomputed_p1_p2_geometry(self) -> None:
        service = self._service(KamikazeApproachConfig(auto_select_mode=0))
        plan = service.build_plan()
        self.assertIsNone(plan.selected_angle_deg)
        chosen = plan.corridors[-1]
        selected = service.select(chosen.angle_deg, plan_revision=plan.revision)
        self.assertEqual(selected.selection_source, "operator")
        handoff = selected.navigation_handoff()
        self.assertTrue(handoff["ready"])
        self.assertEqual(handoff["alignment_point"], chosen.lineup.to_dict())
        self.assertEqual(handoff["dive_entry_point"], chosen.dive_start.to_dict())


if __name__ == "__main__":
    unittest.main()
