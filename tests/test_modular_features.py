from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from src.container import create_container
from src.extension.host import ExtensionHost
from src.services.settings import SettingsService
from src.services.abstracts.vehicle import VehicleService
from src.extension.api.vehicle import RcControl, MissionControl
from src.services.connections import ConnectionHub
from src.core.events import EventBus
from src.models.connections import ConnectionStatus


class ModularFeatureTests(unittest.TestCase):
    def test_map_cache_stop_cancels_waiting_clients_without_stopping_shared_runtime(
        self,
    ):
        import asyncio
        from src.services.async_runtime import AsyncRuntime
        from extensions.map_autonomy.services.tile_cache import TileCacheServer

        with TemporaryDirectory() as folder:
            runtime = AsyncRuntime()
            cache = TileCacheServer(
                cache_path=folder, enabled=True, limit_mb=1, async_runtime=runtime
            )
            try:
                cache.start()
                host, port = cache._server_address

                async def connect():
                    reader, writer = await asyncio.open_connection(host, port)
                    writer.write(b"GET /incomplete")
                    await writer.drain()
                    await asyncio.sleep(0.02)
                    return writer

                writer = runtime.submit(connect()).result(1)
                self.assertTrue(cache._clients)
                cache.stop()
                self.assertFalse(cache._clients)
                self.assertFalse(cache.running)

                async def probe():
                    writer.close()
                    await writer.wait_closed()
                    return 42

                self.assertEqual(runtime.submit(probe()).result(1), 42)
            finally:
                cache.stop()
                runtime.stop()

    def test_bad_processor_does_not_break_feed_and_removal_restores_raw_values(self):
        from src.services.telemetry import MutableTelemetryService
        from src.models.telemetry import TelemetrySnapshot

        hub = MutableTelemetryService()
        hub.add_processor(
            "battery.estimate", lambda snapshot: snapshot.copy(battery_percent=10)
        )
        with self.assertLogs("extensions.broken", level="ERROR"):
            hub.add_processor("broken.processor", lambda snapshot: None)
            hub.push(TelemetrySnapshot.zero().copy(battery_percent=90))
        self.assertEqual(hub.current.battery_percent, 10)
        hub.remove_processor("broken.processor")
        hub.remove_processor("battery.estimate")
        self.assertEqual(hub.current.battery_percent, 90)

    def test_mavlink_has_no_battery_implementation_import(self):
        import ast

        root = Path(__file__).resolve().parents[1]
        for file in (root / "extensions" / "mavlink").rglob("*.py"):
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(
                        (node.module or "").startswith("extensions.battery"), str(file)
                    )

    def test_telemetry_visible_fields_apply_live_and_persist(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        from src.ui.themes.palette import DARK

        self.app = QApplication.instance() or QApplication(["yki-fields-test"])
        with TemporaryDirectory() as folder:
            container = create_container(Path(folder))
            panel = None
            try:
                settings = container.resolve(SettingsService)
                view = next(
                    view
                    for view in container.resolve(ExtensionHost).visual_views()
                    if view.identifier == "telemetry_display.view"
                )
                panel = view.factory(None, DARK, "tr")
                settings.update_extension(
                    "telemetry_display", config={"fields": ["yaw"]}
                )
                self.app.processEvents()
                self.assertFalse(panel.readouts["yaw"].isHidden())
                self.assertTrue(panel.readouts["roll"].isHidden())
                self.assertTrue(panel.section_titles["flight"].isHidden())
                self.assertEqual(
                    SettingsService(Path(folder)).extension_config("telemetry_display")[
                        "fields"
                    ],
                    ["yaw"],
                )
                from extensions.mavlink.ui.status import ProtocolStatusChip
                from src.models.telemetry import TelemetrySnapshot
                from types import SimpleNamespace

                gps = ProtocolStatusChip(
                    SimpleNamespace(services=container), None, DARK, "tr", "gps"
                )
                self.assertEqual(gps.state, "danger")
                gps._snapshot(
                    TelemetrySnapshot.zero().copy(autopilot_link=True, gps_fix="NO FIX")
                )
                self.assertEqual(gps.state, "warning")
                gps._snapshot(
                    TelemetrySnapshot.zero().copy(autopilot_link=True, gps_fix="3D FIX")
                )
                self.assertEqual(gps.state, "success")
                gps.prepare_shutdown()
                gps.deleteLater()
            finally:
                if panel is not None:
                    panel.prepare_shutdown()
                    panel.deleteLater()
                container.shutdown()

    def test_vehicle_minimum_contract_does_not_force_rc_or_mission(self):
        self.assertNotIn("send_rc_override", VehicleService.__abstractmethods__)
        self.assertNotIn("upload_mission", VehicleService.__abstractmethods__)
        self.assertFalse(isinstance(object(), RcControl))
        self.assertFalse(isinstance(object(), MissionControl))
        from src.models.vehicle import VehicleConnectionState

        self.assertEqual(
            VehicleConnectionState(
                True, "/robot", protocol="ros2", identity="robot:rover"
            ).device_id,
            "robot:rover",
        )
        self.assertEqual(
            VehicleConnectionState(True, "/robot", protocol="ros2").device_id,
            "ros2:/robot",
        )
        self.assertEqual(
            VehicleConnectionState(True, "udp", 1, 1, protocol="mavlink").device_id,
            "mavlink:1:1",
        )

    def test_renamed_extension_configs_keep_disabled_state_and_unknown_fields(self):
        with TemporaryDirectory() as folder:
            settings = SettingsService(Path(folder))
            settings.update_extension("map", config={"custom": 123}, enabled=False)
            settings.update_extension(
                "teknofest", config={"servers": []}, enabled=False
            )
            updated = SettingsService(Path(folder))
            self.assertEqual(updated.extension_config("map_autonomy")["custom"], 123)
            self.assertFalse(updated.current.extensions["map_autonomy"]["enabled"])
            self.assertFalse(
                updated.current.extensions["siha_teknofest_server"]["enabled"]
            )
            self.assertNotIn("map", updated.current.extensions)

    def test_optional_feature_services_are_absent_when_packages_are_disabled(self):
        from extensions.map_autonomy.services.planner import PlannerService
        from extensions.map_autonomy.services.tile_cache import TileCacheServer
        from src.extension.api.parameters import ParameterService

        with TemporaryDirectory() as folder:
            settings = SettingsService(Path(folder))
            for key in (
                "map_autonomy",
                "battery",
                "parameters",
                "flight_dynamics",
                "telemetry_display",
                "quick_commands",
            ):
                settings.update_extension(key, enabled=False)
            container = create_container(Path(folder))
            try:
                self.assertFalse(container.container.has(PlannerService))
                self.assertFalse(container.container.has(TileCacheServer))
                self.assertFalse(container.container.has(ParameterService))
                host = container.resolve(ExtensionHost)
                self.assertFalse(host.visual_views())
                self.assertFalse((Path(folder) / "data" / "map-cache").exists())
            finally:
                container.shutdown()

    def test_connection_hub_accepts_arbitrary_channels_and_rejects_detached_events(
        self,
    ):
        class Controller:
            def __init__(self):
                self.changed = EventBus()

        hub = ConnectionHub()
        old, new = Controller(), Controller()
        received = []
        subscription = hub.changed.subscribe(received.append)
        hub.bind(old)
        old.changed.publish(ConnectionStatus("ros2", True, "ready"))
        hub.bind(new)
        old.changed.publish(ConnectionStatus("ros2", False, "stale"))
        new.changed.publish(ConnectionStatus("custom", True, "ready"))
        self.assertEqual([event.channel for event in received], ["ros2", "custom"])
        hub.stop()
        subscription.cancel()
