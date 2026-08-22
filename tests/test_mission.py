from __future__ import annotations

import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.models.mission import MapContact, MapOverlay, MissionState
from src.models.vehicle import VehicleConnectionState
from src.services.abstracts.vehicle import MissionTransferInterrupted
from src.services.mission_archive import MissionArchiveService
from src.services.planner import PlannerService


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

    def test_contact_updates_build_a_trail(self) -> None:
        self.service.upsert_contact(MapContact("target", "Target", 39.9, 32.8))
        self.service.upsert_contact(MapContact("target", "Target", 39.91, 32.81))
        contact = self.service.state.contacts[0]
        self.assertEqual(contact.trail_points, [[39.9, 32.8], [39.91, 32.81]])

    def test_archive_roundtrip_does_not_archive_live_contacts(self) -> None:
        self.service.api.set_point("land", latitude=39.9, longitude=32.8)
        self.service.set_mission_loop(True)
        with tempfile.TemporaryDirectory() as directory:
            archive = MissionArchiveService(Path(directory))
            path = archive.save_plan("flight-one", self.service.state)
            restored = archive.load_plan(path)
            self.assertEqual(len(restored.overlays), 1)
            self.assertEqual(restored.overlays[0].overlay_type, "land")
            self.assertEqual(restored.contacts, [])
            self.assertTrue(restored.mission_loop)

    def test_archive_rejects_paths_outside_the_mission_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = MissionArchiveService(root)
            outside = root / "data" / "settings.json"

            with self.assertRaises(ValueError):
                archive.save_plan("../settings", MissionState())
            with self.assertRaises(ValueError):
                archive.load_plan(outside)
            with self.assertRaises(ValueError):
                archive.delete_plan("../../outside")

            self.assertFalse(outside.exists())

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

    def test_teknofest_reference_layers_are_read_only_and_not_archived(self) -> None:
        hss = MapOverlay(
            "teknofest-hss-1",
            "hss",
            "HSS 1",
            [[39.9, 32.8]],
            "#f00",
            100,
            {"source": "teknofest-hss", "read_only": True, "persistent": False},
        )
        waypoint = MapOverlay(
            "wp-1",
            "waypoint",
            "WP 1",
            [[39.91, 32.81, 120]],
            "#fff",
            35,
        )
        self.service.replace_source_overlays("teknofest-hss", [hss])
        self.service.upsert_overlay(waypoint)

        self.service.remove_overlay(hss.overlay_id)
        self.service.upsert_overlay(hss.with_changes(label="DEGISTI"))
        self.assertEqual(self.service.state.overlays[0].label, "HSS 1")

        self.service.clear_dynamic_overlays()
        self.assertEqual([item.overlay_id for item in self.service.state.overlays], [hss.overlay_id])

        with tempfile.TemporaryDirectory() as directory:
            archive = MissionArchiveService(Path(directory))
            path = archive.save_plan("references", MissionState(overlays=[hss, waypoint]))
            restored = archive.load_plan(path)
            self.assertEqual([item.overlay_id for item in restored.overlays], [waypoint.overlay_id])

    def test_unchanged_source_refresh_preserves_order_without_revision_churn(self) -> None:
        hss = MapOverlay(
            "teknofest-hss-1",
            "hss",
            "HSS 1",
            [[39.9, 32.8]],
            "#f00",
            100,
            {"source": "teknofest-hss", "read_only": True, "persistent": False},
        )
        qr = MapOverlay(
            "teknofest-qr",
            "target",
            "QR",
            [[39.91, 32.81]],
            "#ff0",
            20,
            {"source": "teknofest-qr", "read_only": True, "persistent": False},
        )
        self.service.replace_source_overlays("teknofest-hss", [hss])
        self.service.replace_source_overlays("teknofest-qr", [qr])
        revision = self.service.state.revision

        self.service.replace_source_overlays("teknofest-hss", [hss])

        state = self.service.state
        self.assertEqual(state.revision, revision)
        self.assertEqual(
            [item.overlay_id for item in state.overlays],
            ["teknofest-hss-1", "teknofest-qr"],
        )

    def test_loading_plan_preserves_live_reference_layers(self) -> None:
        hss = MapOverlay(
            "teknofest-hss-2",
            "hss",
            "HSS 2",
            [[39.9, 32.8]],
            "#f00",
            100,
            {"source": "teknofest-hss", "read_only": True, "persistent": False},
        )
        self.service.replace_source_overlays("teknofest-hss", [hss])
        self.service.load_state(
            MissionState(
                overlays=[MapOverlay("land-1", "land", "LAND", [[39.8, 32.7, 0]], "#fff", 35)]
            )
        )
        self.assertEqual(
            {item.overlay_id for item in self.service.state.overlays},
            {"teknofest-hss-2", "land-1"},
        )

    def test_local_plan_restore_preserves_live_reference_layers(self) -> None:
        hss = MapOverlay(
            "teknofest-hss-history",
            "hss",
            "HSS",
            [[39.9, 32.8]],
            "#f00",
            100,
            {"source": "teknofest-hss", "read_only": True},
        )
        self.service.replace_source_overlays("teknofest-hss", [hss])
        self.service.upsert_overlay(
            MapOverlay(
                "waypoint-current",
                "waypoint",
                "CURRENT",
                [[39.91, 32.81, 100]],
                "#fff",
                35,
            )
        )

        restored = MapOverlay(
            "waypoint-restored",
            "waypoint",
            "RESTORED",
            [[39.92, 32.82, 120]],
            "#fff",
            35,
        )
        self.service.replace_local_plan([restored], mission_loop=True)

        state = self.service.state
        self.assertEqual(
            {item.overlay_id for item in state.overlays},
            {"teknofest-hss-history", "waypoint-restored"},
        )
        self.assertTrue(state.mission_loop)

    def test_vehicle_mission_sync_preserves_reference_and_local_area_layers(self) -> None:
        downloaded = MapOverlay(
            "vehicle-waypoint-1",
            "waypoint",
            "WP 1",
            [[39.91, 32.81, 120]],
            "#fff",
            35,
            {
                "source": "flight-controller",
                "route_order": 1,
                "mission_loop": True,
            },
        )

        class Vehicle:
            connected = True
            state = VehicleConnectionState(True, "test", 1, 1)

            def download_mission(self):
                return [downloaded]

        service = PlannerService(
            initial_state=MissionState(
                overlays=[
                    MapOverlay(
                        "teknofest-hss-3",
                        "hss",
                        "HSS 3",
                        [[39.9, 32.8]],
                        "#f00",
                        100,
                        {"source": "teknofest-hss", "read_only": True},
                    ),
                    MapOverlay(
                        "safe-1",
                        "safe_zone",
                        "SAFE",
                        [[39.8, 32.7], [39.81, 32.7], [39.81, 32.71]],
                        "#0f0",
                    ),
                    MapOverlay("local-wp", "waypoint", "OLD WP", [[39.7, 32.6]], "#fff", 35),
                ]
            ),
            vehicle=Vehicle(),  # type: ignore[arg-type]
        )
        service._sync_from_vehicle()
        self.assertEqual(
            {item.overlay_id for item in service.state.overlays},
            {"teknofest-hss-3", "safe-1", "vehicle-waypoint-1"},
        )
        self.assertTrue(service.state.mission_loop)

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


if __name__ == "__main__":
    unittest.main()
