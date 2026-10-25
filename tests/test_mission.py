from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from src.core.events import EventBus
from src.models.geometry import Point
from src.models.mission import MapOverlay, MissionState
from src.models.vehicle import VehicleConnectionState
from src.services.abstracts.vehicle import MissionTransferInterrupted
from extensions.map_autonomy.services.planner import PlannerService


class MissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PlannerService(initial_state=MissionState())

    def test_map_api_supports_points_routes_and_areas(self) -> None:
        waypoint_id = self.service.api.set_point(
            "waypoint",
            latitude=39.9,
            longitude=32.8,
            altitude=120,
        )
        rtl_id = self.service.api.set_point("rtl", latitude=39.9, longitude=32.8)
        safe_id = self.service.api.set_area(
            "safe_zone",
            points=[[39.9, 32.8], [39.91, 32.8], [39.91, 32.81]],
        )
        route_id = self.service.api.set_route([[39.9, 32.8], [39.92, 32.82]])
        state = self.service.state
        self.assertEqual(
            {item.overlay_id for item in state.overlays},
            {waypoint_id, rtl_id, safe_id, route_id},
        )
        self.assertEqual(state.revision, 4)

    def test_point_rejects_non_finite_altitude_but_allows_negative_altitude(self) -> None:
        for altitude in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(altitude=altitude), self.assertRaises(ValueError):
                Point(39.9, 32.8, altitude)

        self.assertEqual(Point(39.9, 32.8, -25.0).altitude, -25.0)

    def test_snapshots_do_not_allow_external_mutation(self) -> None:
        overlay = MapOverlay("wp-1", "waypoint", "WP", [[39.9, 32.8]], "#fff", 35)
        self.service.upsert_overlay(overlay)
        snapshot = self.service.state
        snapshot.overlays[0].label = "CHANGED"
        overlay.label = "ALSO CHANGED"
        self.assertEqual(self.service.state.overlays[0].label, "WP")

    def test_parallel_ui_and_background_updates_do_not_lose_items(self) -> None:
        def add(index: int) -> None:
            self.service.upsert_overlay(
                MapOverlay(
                    f"wp-{index}",
                    "waypoint",
                    f"WP {index}",
                    [[39.8 + index / 100_000, 32.7]],
                    "#fff",
                    35,
                )
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(add, range(100)))
        self.assertEqual(len(self.service.state.overlays), 100)
        self.assertEqual(self.service.state.revision, 100)

    def test_mission_loop_is_central_planner_state(self) -> None:
        revisions: list[int] = []
        self.service.changed.subscribe(lambda state: revisions.append(state.revision))
        self.service.api.set_mission_loop(True)
        self.assertTrue(self.service.state.mission_loop)
        self.assertEqual(MissionState.from_dict(self.service.state.to_dict()).mission_loop, True)

        self.service.api.set_mission_loop(True)
        self.assertEqual(len(revisions), 1)
        self.service.clear_dynamic_overlays()
        self.assertFalse(self.service.state.mission_loop)

    def test_vehicle_mission_sync_ignores_a_download_preempted_by_operator_upload(self) -> None:
        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def download_mission(self):
                raise MissionTransferInterrupted("preempted")

        service = PlannerService(
            initial_state=MissionState(
                overlays=[MapOverlay("local-wp", "waypoint", "WP", [[39.7, 32.6]], "#fff", 35)]
            ),
            vehicle=Vehicle(),  # type: ignore[arg-type]
        )
        service._sync_from_vehicle()
        self.assertEqual([item.overlay_id for item in service.state.overlays], ["local-wp"])

    def test_manual_vehicle_sync_requires_connection_and_imports_the_plan(self) -> None:
        downloaded = MapOverlay(
            "vehicle-waypoint-manual",
            "waypoint",
            "WP 1",
            [[39.91, 32.81, 120]],
            "#fff",
            35,
            {"source": "flight-controller", "route_order": 1},
        )

        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def download_mission(self):
                return [downloaded]

        service = PlannerService(vehicle=Vehicle())  # type: ignore[arg-type]
        self.assertTrue(service.request_vehicle_sync())
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not service.state.overlays:
            time.sleep(0.01)
        self.assertEqual(
            [item.overlay_id for item in service.state.overlays],
            ["vehicle-waypoint-manual"],
        )
        service.stop()

        class OfflineVehicle:
            connected = False
            state = VehicleConnectionState(False, "test", 1, 1)

        offline = PlannerService(vehicle=OfflineVehicle())  # type: ignore[arg-type]
        self.assertFalse(offline.request_vehicle_sync())

    def test_vehicle_download_does_not_overwrite_edits_made_while_waiting(self) -> None:
        downloaded = MapOverlay(
            "vehicle-wp",
            "waypoint",
            "Vehicle WP",
            [[39.91, 32.81, 120]],
            "#fff",
            35,
        )
        local = MapOverlay(
            "operator-wp",
            "waypoint",
            "Operator WP",
            [[39.92, 32.82, 130]],
            "#fff",
            35,
        )

        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)
            planner: PlannerService

            def download_mission(self):
                self.planner.upsert_overlay(local)
                return [downloaded]

        vehicle = Vehicle()
        service = PlannerService(vehicle=vehicle)  # type: ignore[arg-type]
        vehicle.planner = service

        service._sync_from_vehicle()

        self.assertEqual(
            [item.overlay_id for item in service.state.overlays], ["operator-wp"]
        )

    def test_old_vehicle_download_is_discarded_after_stop_and_restart(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        applied = threading.Event()
        stale = MapOverlay(
            "stale-wp", "waypoint", "Stale", [[39.91, 32.81, 120]], "#fff", 35
        )
        current = MapOverlay(
            "current-wp", "waypoint", "Current", [[39.92, 32.82, 130]], "#fff", 35
        )

        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)
            connection_changed = EventBus()

            def __init__(self) -> None:
                self.download_count = 0

            def download_mission(self):
                self.download_count += 1
                if self.download_count == 1:
                    entered.set()
                    if not release.wait(1.0):
                        raise TimeoutError("test did not release the stale download")
                    return [stale]
                return [current]

        vehicle = Vehicle()
        service = PlannerService(vehicle=vehicle)  # type: ignore[arg-type]
        subscription = service._changed.subscribe(
            lambda state: applied.set()
            if any(item.overlay_id == "current-wp" for item in state.overlays)
            else None
        )
        try:
            service.start()
            self.assertTrue(entered.wait(1.0))

            stopping = threading.Thread(target=service.stop)
            stopping.start()
            self.assertTrue(service._stop_event.wait(1.0))
            service.start()
            release.set()

            self.assertTrue(applied.wait(1.0))
            stopping.join(timeout=1.0)
            self.assertFalse(stopping.is_alive())
            self.assertEqual(
                [item.overlay_id for item in service.state.overlays], ["current-wp"]
            )
        finally:
            release.set()
            service.stop()
            subscription.cancel()


if __name__ == "__main__":
    unittest.main()
