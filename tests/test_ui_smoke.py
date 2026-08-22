from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.container import create_container
from src.services.runtime import RuntimeServices
from src.ui.adapters.runtime import QtRuntimeServices
from src.ui.container import UiContainer
from src.models.commands import CommandRequest, CommandResult
from src.models.connections import ConnectionStatus
from src.models.mission import MapOverlay, MissionState
from src.models.telemetry import TelemetrySnapshot
from src.models.video import VideoStreamStats
from src.ui.i18n import t
from src.ui.themes.manager import apply_theme
from src.ui.views.main_window import MainWindow


ROOT = Path(__file__).resolve().parents[1]


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["yki-smoke-tests"])

    def test_main_window_starts_with_the_new_backend_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            backend_container = create_container(base_dir)
            backend = backend_container.resolve(RuntimeServices)
            theme = apply_theme(self.app, backend.settings_service.current.theme)
            ui_container = UiContainer(
                backend_container.container,
                base_dir=ROOT,
                theme=theme,
            )
            runtime = ui_container.resolve(QtRuntimeServices)
            window = ui_container.resolve(MainWindow)
            window.show()
            runtime.start()
            for _attempt in range(40):
                if window.map_panel._js_ready and backend._started:
                    break
                QTest.qWait(100)
            # Drain the startup telemetry snapshot before asserting manual map
            # updates so a queued disconnected state cannot win the test race.
            QTest.qWait(runtime.telemetry_service._UI_FRAME_INTERVAL_MS + 10)
            self.assertTrue(window.isVisible())
            self.assertFalse(window.windowIcon().isNull())
            self.assertEqual(
                window.workspace_splitter.orientation(),
                Qt.Orientation.Vertical,
            )
            for splitter in (
                window.main_splitter,
                window.workspace_splitter,
                window.visuals_splitter,
                window.bottom_splitter,
            ):
                self.assertFalse(splitter.opaqueResize())
                self.assertEqual(splitter.handleWidth(), 5)
            self.assertTrue(window.map_panel._js_ready)
            self.assertIsNotNone(window.map_panel)
            self.assertEqual(window.parameters_panel.table.rowCount(), 0)
            self.assertEqual(window.bottom_status["video"].state, "danger")
            self.assertIn(theme.danger, window.bottom_status["video"].text_label.styleSheet())
            self.assertEqual(window.bottom_status["orin"].state, "danger")

            with patch.object(
                QMessageBox,
                "exec",
                side_effect=(
                    QMessageBox.StandardButton.Yes,
                    QMessageBox.StandardButton.Yes,
                ),
            ) as confirmation:
                self.assertTrue(window._confirm_actuator_test())
                self.assertEqual(confirmation.call_count, 2)

            with patch.object(
                QMessageBox,
                "exec",
                return_value=QMessageBox.StandardButton.No,
            ) as confirmation:
                self.assertFalse(window._confirm_actuator_test())
                confirmation.assert_called_once()

            window._last_snapshot = TelemetrySnapshot.zero().copy(
                telemetry_link=True,
                autopilot_link=True,
                armed=True,
            )
            with (
                patch.object(QMessageBox, "warning") as warning,
                patch.object(window.command_service, "issue_command") as issue,
            ):
                window._request_command(
                    "SET_MISSION_TASK",
                    {"task": "ACTUATOR_TEST"},
                )
                warning.assert_called_once()
                issue.assert_not_called()

            QTest.mouseClick(window.battery_meter, Qt.MouseButton.LeftButton)
            QTest.qWait(25)
            self.assertTrue(window.battery_popup.isVisible())
            self.assertEqual(window.battery_popup.full_voltage.value(), 16.8)
            self.assertEqual(window.battery_popup.minimum_voltage.value(), 14.8)
            window.battery_popup.close()

            home_reference_result: list[object] = []
            window.map_panel._view.page().runJavaScript(
                "setHomeReference(L.latLng(39.8672, 32.7481), false);"
                "state.homeReferenceRollbackLatLng = L.latLng(39.8672, 32.7481);"
                "homeReferenceMarker.setLatLng(L.latLng(39.8710, 32.7520));"
                "map.removeLayer(homeReferenceMarker);"
                "window.ykiMap.resolveHomeReference(false, null, null);"
                "(() => {"
                "  const point = homeReferenceMarker.getLatLng();"
                "  return {"
                "    lat: point.lat,"
                "    lon: point.lng,"
                "    visible: map.hasLayer(homeReferenceMarker)"
                "  };"
                "})()",
                home_reference_result.append,
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if home_reference_result:
                    break
            self.assertEqual(
                home_reference_result,
                [{"lat": 39.8672, "lon": 32.7481, "visible": True}],
            )

            drag_telemetry_result: list[object] = []
            window.map_panel._view.page().runJavaScript(
                "setHomeReference(L.latLng(39.8672, 32.7481), false);"
                "state.homeReferenceDragging = true;"
                "homeReferenceMarker.setLatLng(L.latLng(39.8750, 32.7600));"
                "updateHomeReference({"
                "  homePositionAvailable: true,"
                "  homeLat: 39.8672,"
                "  homeLon: 32.7481"
                "});"
                "(() => {"
                "  const point = homeReferenceMarker.getLatLng();"
                "  state.homeReferenceDragging = false;"
                "  return {lat: point.lat, lon: point.lng};"
                "})()",
                drag_telemetry_result.append,
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if drag_telemetry_result:
                    break
            self.assertEqual(
                drag_telemetry_result,
                [{"lat": 39.875, "lon": 32.76}],
            )

            map_interaction_result: list[object] = []
            window.map_panel._view.page().runJavaScript(
                "setHomeReference(L.latLng(39.8672, 32.7481), false);"
                "window.ykiMap.setMissionLoop(false);"
                "window.ykiMap.setOverlays(["
                "{overlay_id:'route-takeoff',overlay_type:'takeoff',label:'TKF 1',"
                "points:[[39.8660,32.7460,80]],radius_m:35,color:'#78b7ff',"
                "metadata:{route_order:1}},"
                "{overlay_id:'route-waypoint',overlay_type:'waypoint',label:'WP 2',"
                "points:[[39.8680,32.7500,100]],radius_m:35,color:'#74d99f',"
                "metadata:{route_order:2}},"
                "{overlay_id:'route-rtl',overlay_type:'rtl',label:'RTL 3',"
                "points:[[40.0000,33.0000]],radius_m:75,color:'#f4b942',"
                "metadata:{route_order:3}},"
                "{overlay_id:'route-after-rtl',overlay_type:'waypoint',label:'WP 4',"
                "points:[[39.9000,32.9000,100]],radius_m:35,color:'#74d99f',"
                "metadata:{route_order:4}}"
                "]);"
                "(() => {"
                " const solid = missionRouteLine.getLatLngs();"
                " const returned = rtlReturnLine.getLatLngs();"
                " const rtl = state.layers.get('route-rtl').getLatLng();"
                " const wp = state.layers.get('route-waypoint').getLatLng();"
                " const cp = map.latLngToContainerPoint(wp);"
                " showContextMenu(cp, wp, 'route-waypoint');"
                " const pointMenu = Object.fromEntries("
                "   [...document.querySelectorAll('#context-menu button')].map("
                "     (button) => [button.dataset.action, button.style.display]"
                "   )"
                " );"
                " const editorElement = document.getElementById('context-point-editor');"
                " const editorVisible = editorElement.style.display;"
                " const editorRect = editorElement.getBoundingClientRect();"
                " const altitudeRect = document.getElementById('context-altitude-input').getBoundingClientRect();"
                " const editorFits = altitudeRect.right <= editorRect.right + 0.5"
                "   && editorElement.scrollWidth <= editorElement.clientWidth;"
                " document.getElementById('context-radius-input').value = '52';"
                " document.getElementById('context-altitude-input').value = '135';"
                " document.getElementById('context-radius-input').dispatchEvent("
                "   new Event('input', {bubbles:true})"
                " );"
                " document.getElementById('context-altitude-input').dispatchEvent("
                "   new Event('input', {bubbles:true})"
                " );"
                " const draftRadius = state.layers.get('route-waypoint').getRadius();"
                " const draftAltitude = altitudeForLayer(state.layers.get('route-waypoint'));"
                " const savedBridge = bridge; bridge = null;"
                " hideContextMenu(true); bridge = savedBridge;"
                " const editedRadius = state.layers.get('route-waypoint').getRadius();"
                " const editedAltitude = altitudeForLayer(state.layers.get('route-waypoint'));"
                " showContextMenu(cp, wp, null);"
                " const mapMenu = Object.fromEntries("
                "   [...document.querySelectorAll('#context-menu button')].map("
                "     (button) => [button.dataset.action, button.style.display]"
                "   )"
                " );"
                " return {"
                "   solidCount: solid.length,"
                "   solidEndsAtWaypoint: solid.length === 2"
                "     && Math.abs(solid[1].lat - wp.lat) < 0.000001"
                "     && Math.abs(solid[1].lng - wp.lng) < 0.000001,"
                "   returnCount: returned.length,"
                "   returnStartsAtWaypoint: returned.length === 2"
                "     && Math.abs(returned[0].lat - wp.lat) < 0.000001"
                "     && Math.abs(returned[0].lng - wp.lng) < 0.000001,"
                "   returnEndsAtHome: returned.length === 2"
                "     && Math.abs(returned[1].lat - 39.8672) < 0.000001"
                "     && Math.abs(returned[1].lng - 32.7481) < 0.000001,"
                "   rtlAtHome: Math.abs(rtl.lat - 39.8672) < 0.000001"
                "     && Math.abs(rtl.lng - 32.7481) < 0.000001,"
                "   distanceLabelCount: missionDistanceLabelGroup.getLayers().length,"
                "   downloadButton: Boolean(document.querySelector('[data-tool=\"download\"]')),"
                "   pointMenu,"
                "   editorVisible,"
                "   editorFits,"
                "   draftRadius,"
                "   draftAltitude,"
                "   editedRadius,"
                "   editedAltitude,"
                "   centerWheelZoom: map.options.scrollWheelZoom === 'center',"
                "   centerTouchZoom: map.options.touchZoom === 'center',"
                "   mapMenu"
                " };"
                "})()",
                map_interaction_result.append,
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if map_interaction_result:
                    break
            self.assertEqual(
                map_interaction_result,
                [{
                    "solidCount": 2,
                    "solidEndsAtWaypoint": True,
                    "returnCount": 2,
                    "returnStartsAtWaypoint": True,
                    "returnEndsAtHome": True,
                    "rtlAtHome": True,
                    "distanceLabelCount": 2,
                    "downloadButton": True,
                    "pointMenu": {
                        "goto": "none",
                        "center": "none",
                        "set_home": "none",
                        "delete": "flex",
                    },
                    "editorVisible": "grid",
                    "editorFits": True,
                    "draftRadius": 35,
                    "draftAltitude": 100,
                    "editedRadius": 52,
                    "editedAltitude": 135,
                    "centerWheelZoom": True,
                    "centerTouchZoom": True,
                    "mapMenu": {
                        "goto": "flex",
                        "center": "flex",
                        "set_home": "flex",
                        "delete": "none",
                    },
                }],
            )

            requested_vehicle_syncs: list[bool] = []
            original_vehicle_sync = window.map_panel._planner_service.request_vehicle_sync
            window.map_panel._planner_service.request_vehicle_sync = (  # type: ignore[method-assign]
                lambda: requested_vehicle_syncs.append(True) or True
            )
            window.map_panel._view.page().runJavaScript(
                "window.ykiMap.setCommandAvailable(true);"
                "document.querySelector('[data-tool=\"download\"]').click();"
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if requested_vehicle_syncs:
                    break
            window.map_panel._planner_service.request_vehicle_sync = original_vehicle_sync  # type: ignore[method-assign]
            self.assertEqual(requested_vehicle_syncs, [True])

            point_drag_result: list[object] = []
            window.map_panel._view.page().runJavaScript(
                "window.ykiMap.setOverlays([{"
                "overlay_id:'drag-waypoint',overlay_type:'waypoint',label:'WP 1',"
                "points:[[39.8680,32.7500,100]],radius_m:35,color:'#74d99f',"
                "metadata:{route_order:1}"
                "}]);"
                "(() => {"
                " const layer = state.layers.get('drag-waypoint');"
                " const before = layer.getLatLng();"
                " const point = map.latLngToContainerPoint(before);"
                " const rect = map.getContainer().getBoundingClientRect();"
                " const savedBridge = bridge;"
                " bridge = null;"
                " beginPointDrag(layer, {"
                "   containerPoint: point,"
                "   latlng: before,"
                "   originalEvent: new MouseEvent('mousedown', {"
                "     bubbles:true,button:0,buttons:1,"
                "     clientX:rect.left+point.x,clientY:rect.top+point.y"
                "   })"
                " });"
                " document.dispatchEvent(new MouseEvent('mousemove', {"
                "   bubbles:true,button:0,buttons:1,"
                "   clientX:rect.left+point.x+36,clientY:rect.top+point.y+18"
                " }));"
                " document.dispatchEvent(new MouseEvent('mouseup', {"
                "   bubbles:true,button:0,clientX:rect.left+point.x+36,"
                "   clientY:rect.top+point.y+18"
                " }));"
                " bridge = savedBridge;"
                " const after = layer.getLatLng();"
                " const result = {"
                "   moved: before.distanceTo(after) > 1,"
                "   finished: state.draggingPoint === null,"
                "   mapDragRestored: map.dragging.enabled(),"
                "   draggingClassCleared: !map.getContainer().classList.contains('is-point-dragging')"
                " };"
                " window.ykiMap.setOverlays([]);"
                " result.editHandlesCleared = document.querySelectorAll('.leaflet-editing-icon').length === 0;"
                " return result;"
                "})()",
                point_drag_result.append,
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if point_drag_result:
                    break
            self.assertEqual(
                point_drag_result,
                [{
                    "moved": True,
                    "finished": True,
                    "mapDragRestored": True,
                    "draggingClassCleared": True,
                    "editHandlesCleared": True,
                }],
            )

            loop_action_result: list[object] = []
            window.map_panel._view.page().runJavaScript(
                "window.ykiMap.setMissionLoop(true);"
                "emitAction({type:'mission_loop_changed',loop:true});"
                "document.querySelector('.mission-mode-button').dataset.mode;",
                loop_action_result.append,
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if (
                    loop_action_result
                    and runtime.facade.mission_snapshot().mission_loop
                ):
                    break
            self.assertEqual(loop_action_result, ["loop"])
            self.assertTrue(runtime.facade.mission_snapshot().mission_loop)
            runtime.planner_service.set_active_tool("waypoint")
            QTest.qWait(50)
            loop_after_sync: list[object] = []
            window.map_panel._view.page().runJavaScript(
                "document.querySelector('.mission-mode-button').dataset.mode;",
                loop_after_sync.append,
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if loop_after_sync:
                    break
            self.assertEqual(loop_after_sync, ["loop"])
            runtime.planner_service.set_active_tool("pan")

            telemetry_payloads: list[dict] = []
            window.map_panel._bridge.telemetry_pushed.connect(
                lambda payload: telemetry_payloads.append(json.loads(payload))
            )
            waiting_position = TelemetrySnapshot.zero().copy(
                telemetry_link=True,
                autopilot_link=True,
            )
            window.map_panel.update_snapshot(waiting_position)
            self.assertFalse(telemetry_payloads[-1]["active"])
            first_position = waiting_position.copy(
                latitude=39.9,
                longitude=32.8,
                position_valid=True,
                gps_satellites=14,
                gps_fix="3D FIX",
                gps_hdop=0.82,
                local_north_m=145.25,
                local_east_m=-32.5,
                local_down_m=-114.6,
            )
            window.map_panel.update_snapshot(first_position)
            for _attempt in range(10):
                QTest.qWait(window.map_panel._TELEMETRY_RENDER_INTERVAL_MS)
                if telemetry_payloads[-1].get("active"):
                    break
            latest_payload = telemetry_payloads[-1]
            self.assertTrue(latest_payload["active"])
            self.assertEqual(latest_payload["gpsSatellites"], 14)
            self.assertEqual(latest_payload["gpsHdop"], 0.82)
            self.assertTrue(latest_payload["localPositionAvailable"])
            self.assertEqual(latest_payload["localNorth"], 145.25)
            self.assertEqual(latest_payload["localEast"], -32.5)
            self.assertEqual(latest_payload["localDown"], -114.6)
            self.assertFalse(latest_payload["homePositionAvailable"])
            vehicle_readout: list[object] = []
            for _attempt in range(40):
                current_readout: list[object] = []
                window.map_panel._view.page().runJavaScript(
                    "document.querySelector('#vehicle-readout').textContent;",
                    current_readout.append,
                )
                for _callback_attempt in range(10):
                    QTest.qWait(10)
                    if current_readout:
                        break
                if current_readout:
                    vehicle_readout[:] = current_readout
                    if "39.90000" in str(current_readout[-1]):
                        break
                QTest.qWait(25)
            self.assertIn("39.90000", str(vehicle_readout[-1]))
            self.assertIn("32.80000", str(vehicle_readout[-1]))
            map_update_count = len(telemetry_payloads)
            window.map_panel.update_snapshot(first_position)
            self.assertEqual(len(telemetry_payloads), map_update_count)
            jumped_position = first_position.copy(latitude=-35.36, longitude=149.16)
            window.map_panel.update_snapshot(jumped_position)
            for _attempt in range(10):
                QTest.qWait(window.map_panel._TELEMETRY_RENDER_INTERVAL_MS)
                if telemetry_payloads[-1].get("resetTrack"):
                    break
            self.assertTrue(telemetry_payloads[-1]["resetTrack"])

            telemetry_chip = window.bottom_status["telemetry"]
            window._on_connection_status(
                ConnectionStatus(
                    "telemetry",
                    True,
                    "Canlı MAVLink telemetrisi alınıyor",
                    options={
                        "link_quality": 91,
                        "link_quality_available": True,
                    },
                )
            )
            self.assertEqual(
                telemetry_chip.text_label.text(),
                t("status.telemetry", window.language),
            )
            self.assertNotIn("%", telemetry_chip.text_label.text())
            packet_snapshot = TelemetrySnapshot.zero()
            packet_snapshot = packet_snapshot.copy(
                telemetry_link=True,
                autopilot_link=True,
                link_quality=93,
                link_quality_source="mavlink_packets",
                link_quality_received_at=packet_snapshot.timestamp,
                packets_in=391,
                packets_out=8,
            )
            window._on_telemetry(packet_snapshot)
            self.assertEqual(window.link_meters["command"].value.text(), "93%")
            self.assertEqual(
                window.link_meters["command"].detail.text(),
                "↓391 ↑8 · ML93",
            )
            rf_snapshot = packet_snapshot.copy(
                link_quality=61,
                link_quality_source="rf",
                radio_local_rssi=150,
                radio_local_noise=100,
                radio_remote_rssi=135,
                radio_remote_noise=100,
                radio_tx_buffer=91,
                radio_rx_errors=7,
                radio_fixed_packets=3,
                packet_delivery_percent=88,
                estimated_dropped_packets=12,
            )
            window._on_telemetry(rf_snapshot)
            self.assertEqual(window.link_meters["command"].value.text(), "61%")
            self.assertEqual(
                window.link_meters["command"].detail.text(),
                "↓391 ↑8 · ML88",
            )
            command_meter = window.link_meters["command"]
            self.assertIn("Yerel RFD", command_meter.label.toolTip())
            self.assertIn("tahmini kayıp 12", command_meter.label.toolTip())
            self.assertEqual(command_meter.toolTip(), "")
            self.assertEqual(command_meter.value.toolTip(), "")
            self.assertEqual(command_meter.detail.toolTip(), "")
            self.assertEqual(window.battery_meter.value.text(), "--%")
            battery_snapshot = packet_snapshot.copy(
                battery_percent=76,
                battery_voltage=15.8,
                battery_current_amps=7.4,
                battery_consumed_mah=940,
            )
            window._on_telemetry(battery_snapshot)
            self.assertEqual(window.battery_meter.value.text(), "76%")
            self.assertEqual(window.battery_meter.detail.text(), "15.8 V · 7.4 A")
            self.assertIn("940 mAh", window.battery_meter.toolTip())
            self.assertEqual(
                window.link_meters["bulk"].label.text(),
                "Video Aktarımı",
            )
            window._on_video_stats(
                VideoStreamStats(
                    connected=True,
                    quality_percent=92,
                    frames_received=120,
                    fps=29.7,
                    bitrate_kbps=2420.0,
                    width=1280,
                    height=720,
                    frame_age_s=0.0,
                )
            )
            self.assertEqual(window.link_meters["bulk"].value.text(), "92%")
            self.assertEqual(
                window.link_meters["bulk"].detail.text(),
                "↓2.4M · 29.7F · 720p",
            )
            video_meter = window.link_meters["bulk"]
            self.assertIn("1280×720", video_meter.label.toolTip())
            self.assertIn("120", video_meter.label.toolTip())
            self.assertEqual(video_meter.toolTip(), "")
            self.assertEqual(video_meter.value.toolTip(), "")
            self.assertEqual(video_meter.detail.toolTip(), "")
            telemetry_chip.set_state("success")
            self.assertIn(theme.success, telemetry_chip.text_label.styleSheet())
            telemetry_chip.set_state("danger")
            self.assertIn(theme.danger, telemetry_chip.text_label.styleSheet())

            window._open_connection_popup("telemetry", telemetry_chip)
            self.assertTrue(window.connection_popup.isVisible())
            self.assertEqual(window.connection_popup.channel, "telemetry")
            window.connection_popup.close()

            orin_chip = window.bottom_status["orin"]
            window._open_connection_popup("orin", orin_chip)
            self.assertTrue(window.connection_popup.isVisible())
            self.assertEqual(window.connection_popup.channel, "orin")
            self.assertIn("ORIN", window.connection_popup.title_label.text())
            self.assertTrue(window.connection_popup.baud_input.isVisible())
            self.assertEqual(
                window.connection_popup.endpoint_input.text(),
                runtime.settings_service.current.backend.mavlink.endpoint,
            )
            window.connection_popup.close()

            runtime.facade.upsert_mission_item(
                MapOverlay(
                    "external-rtl",
                    "rtl",
                    "External RTL",
                    [[39.9, 32.8]],
                    "#f4b942",
                    75,
                )
            )
            QTest.qWait(100)
            self.assertIn("external-rtl", window.map_panel._overlay_cache)

            window.map_panel._on_overlay_changed(
                {
                    "overlay_id": "ui-land",
                    "overlay_type": "land",
                    "label": "UI Land",
                    "points": [[39.91, 32.81]],
                    "radius_m": 35,
                    "color": "#2f7ed8",
                }
            )
            self.assertIn(
                "ui-land",
                {item.overlay_id for item in runtime.facade.mission_snapshot().overlays},
            )

            altitude_applied: list[object] = []
            window.map_panel._view.page().runJavaScript(
                "window.ykiMap.upsertOverlay({"
                "overlay_id:'altitude-takeoff',overlay_type:'takeoff',label:'TKF 1',"
                "points:[[39.915,32.815,85]],radius_m:35,color:'#78b7ff',"
                "metadata:{route_order:1}});"
                "openPointDialog('altitude-takeoff');"
                "document.getElementById('point-altitude-input').value='125';"
                "applyPointDialog();"
                "[...document.querySelectorAll('.overlay-label')]"
                ".some((label) => label.textContent.includes('125 m'));",
                altitude_applied.append,
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if altitude_applied:
                    break
            self.assertEqual(altitude_applied, [True])
            altitude_overlay = next(
                item
                for item in runtime.facade.mission_snapshot().overlays
                if item.overlay_id == "altitude-takeoff"
            )
            self.assertEqual(altitude_overlay.points[0][2], 125.0)

            transfer_scripts: list[str] = []
            original_run_script = window.map_panel._run_script
            window.map_panel._run_script = transfer_scripts.append
            current_overlays = [
                overlay.to_dict()
                for overlay in runtime.facade.mission_snapshot().overlays
            ]
            window.map_panel.command_started("MISSION_UPLOAD", {"overlays": current_overlays})
            window.map_panel.command_completed(
                CommandResult(
                    CommandRequest("MISSION_UPLOAD", {"overlays": current_overlays}),
                    True,
                    "2 görev öğesi uçuş cihazı tarafından kabul edildi",
                )
            )
            self.assertTrue(any('"success"' in script for script in transfer_scripts))
            runtime.facade.upsert_mission_item(
                MapOverlay(
                    "new-waypoint",
                    "waypoint",
                    "WP 2",
                    [[39.92, 32.82]],
                    "#74d99f",
                    35,
                )
            )
            QTest.qWait(50)
            self.assertTrue(any('"dirty"' in script for script in transfer_scripts))
            window.map_panel._run_script = original_run_script

            history_commands: list[tuple[str, dict]] = []
            window.map_panel.command_requested.connect(
                lambda code, parameters: history_commands.append(
                    (code, parameters)
                )
            )
            window.map_panel._history_undo.clear()
            window.map_panel._history_redo.clear()

            # Exercise the real WebChannel path. Point creation first emits a
            # selection snapshot and then the plan mutation; a delayed older
            # snapshot must not erase the add/delete undo record.
            before_web_ids = {
                item.overlay_id for item in runtime.planner_service.state.overlays
            }
            window.map_panel._view.page().runJavaScript(
                "addPointOverlay('waypoint', L.latLng(39.929, 32.829));"
            )
            web_added_id = ""
            for _attempt in range(40):
                QTest.qWait(25)
                added_ids = {
                    item.overlay_id for item in runtime.planner_service.state.overlays
                } - before_web_ids
                if added_ids:
                    web_added_id = next(iter(added_ids))
                    break
            self.assertTrue(web_added_id)
            self.assertEqual(len(window.map_panel._history_undo), 1)

            window.map_panel._view.page().runJavaScript(
                "document.dispatchEvent(new KeyboardEvent('keydown', {"
                "key:'z', ctrlKey:true, bubbles:true, cancelable:true}));"
            )
            for _attempt in range(40):
                QTest.qWait(25)
                if web_added_id not in {
                    item.overlay_id for item in runtime.planner_service.state.overlays
                }:
                    break
            self.assertNotIn(
                web_added_id,
                {item.overlay_id for item in runtime.planner_service.state.overlays},
            )

            window.map_panel._view.page().runJavaScript(
                "document.dispatchEvent(new KeyboardEvent('keydown', {"
                "key:'z', ctrlKey:true, shiftKey:true, bubbles:true, cancelable:true}));"
            )
            for _attempt in range(40):
                QTest.qWait(25)
                if web_added_id in {
                    item.overlay_id for item in runtime.planner_service.state.overlays
                }:
                    break
            self.assertIn(
                web_added_id,
                {item.overlay_id for item in runtime.planner_service.state.overlays},
            )

            window.map_panel._view.page().runJavaScript(
                f"window.ykiMap.removeOverlayById({json.dumps(web_added_id)}, true);"
            )
            for _attempt in range(40):
                QTest.qWait(25)
                if web_added_id not in {
                    item.overlay_id for item in runtime.planner_service.state.overlays
                }:
                    break
            self.assertNotIn(
                web_added_id,
                {item.overlay_id for item in runtime.planner_service.state.overlays},
            )
            window.map_panel._navigate_history("undo")
            QTest.qWait(100)
            self.assertIn(
                web_added_id,
                {item.overlay_id for item in runtime.planner_service.state.overlays},
            )
            window.map_panel._navigate_history("redo")
            QTest.qWait(100)
            self.assertNotIn(
                web_added_id,
                {item.overlay_id for item in runtime.planner_service.state.overlays},
            )

            window.map_panel._history_undo.clear()
            window.map_panel._history_redo.clear()
            history_overlay = MapOverlay(
                "history-waypoint",
                "waypoint",
                "HISTORY WP",
                [[39.93, 32.83, 130]],
                "#74d99f",
                35,
                {"route_order": 99},
            )
            window.map_panel._on_overlay_changed(history_overlay.to_dict())
            self.assertTrue(
                any(
                    item.overlay_id == history_overlay.overlay_id
                    for item in runtime.planner_service.state.overlays
                )
            )
            # The planner update returns through a queued Qt adapter. Undo must
            # still retain this local operation after that delayed sync.
            QTest.qWait(100)
            self.assertEqual(len(window.map_panel._history_undo), 1)

            window.map_panel._view.page().runJavaScript(
                "document.dispatchEvent(new KeyboardEvent('keydown', {"
                "key: 'z', ctrlKey: true, bubbles: true, cancelable: true"
                "}));"
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if not any(
                    item.overlay_id == history_overlay.overlay_id
                    for item in runtime.planner_service.state.overlays
                ):
                    break
            self.assertFalse(
                any(
                    item.overlay_id == history_overlay.overlay_id
                    for item in runtime.planner_service.state.overlays
                )
            )
            self.assertEqual(history_commands, [])

            window.map_panel._view.page().runJavaScript(
                "document.dispatchEvent(new KeyboardEvent('keydown', {"
                "key: 'z', ctrlKey: true, shiftKey: true,"
                "bubbles: true, cancelable: true"
                "}));"
            )
            for _attempt in range(20):
                QTest.qWait(25)
                if any(
                    item.overlay_id == history_overlay.overlay_id
                    for item in runtime.planner_service.state.overlays
                ):
                    break
            self.assertTrue(
                any(
                    item.overlay_id == history_overlay.overlay_id
                    for item in runtime.planner_service.state.overlays
                )
            )
            self.assertEqual(history_commands, [])

            window.map_panel._remove_overlay(history_overlay.overlay_id)
            QTest.qWait(50)
            self.assertFalse(
                any(
                    item.overlay_id == history_overlay.overlay_id
                    for item in runtime.planner_service.state.overlays
                )
            )
            window.map_panel._navigate_history("undo")
            QTest.qWait(50)
            self.assertTrue(
                any(
                    item.overlay_id == history_overlay.overlay_id
                    for item in runtime.planner_service.state.overlays
                )
            )

            archived_overlay = MapOverlay(
                "archive-waypoint",
                "waypoint",
                "ARCHIVE WP",
                [[39.94, 32.84, 140]],
                "#74d99f",
                35,
                {"route_order": 1},
            )
            window.map_panel.load_archived_plan(
                MissionState(overlays=[archived_overlay])
            )
            QTest.qWait(50)
            self.assertEqual(
                [item.overlay_id for item in runtime.planner_service.state.overlays
                 if not item.is_source_managed],
                [archived_overlay.overlay_id],
            )
            window.map_panel._navigate_history("undo")
            QTest.qWait(50)
            self.assertTrue(
                any(
                    item.overlay_id == history_overlay.overlay_id
                    for item in runtime.planner_service.state.overlays
                )
            )
            window.map_panel._navigate_history("redo")
            QTest.qWait(50)
            self.assertEqual(
                [item.overlay_id for item in runtime.planner_service.state.overlays
                 if not item.is_source_managed],
                [archived_overlay.overlay_id],
            )

            force_requests: list[tuple[str, dict]] = []
            window._last_snapshot = TelemetrySnapshot.zero().copy(
                telemetry_link=True,
                autopilot_link=True,
                armed=False,
            )
            window._confirm_force_arm = lambda _result: True  # type: ignore[method-assign]
            window.command_service.issue_command = (  # type: ignore[method-assign]
                lambda code, params=None: force_requests.append((code, params or {}))
            )
            failed_arm = CommandResult(
                CommandRequest("ARM"),
                False,
                "Pre-arm kontrolü reddetti",
            )
            window._on_command_completed(failed_arm)
            self.assertEqual(force_requests[0][0], "FORCE_ARM")
            self.assertTrue(force_requests[0][1]["operator_confirmation"])
            self.assertEqual(
                force_requests[0][1]["normal_arm_error"],
                "Pre-arm kontrolü reddetti",
            )

            force_requests.clear()
            window._confirm_force_arm = lambda _result: False  # type: ignore[method-assign]
            window._on_command_completed(failed_arm)
            self.assertEqual(force_requests, [])

            runtime.settings_service.set_value("ui.language", "en")
            QTest.qWait(50)
            self.assertEqual(window.language, "en")
            self.assertEqual(window.telemetry_panel.title_label.text(), "Flight Telemetry")
            self.assertEqual(window.video_panel.title_label.text(), "Camera")
            self.assertEqual(
                window.video_panel.subtitle_label.text(),
                "Live stream panel",
            )
            self.assertEqual(window.parameters_panel.title_label.text(), "Vehicle Management")
            self.assertEqual(
                window.parameters_panel.tabs.tabText(1),
                "Task Actions",
            )
            self.assertFalse(window.command_panel.onboard_status.isHidden())
            self.assertTrue(
                window.command_panel.task_tab.isAncestorOf(
                    window.command_panel.onboard_status
                )
            )
            self.assertIs(
                window.command_panel.task_progress.parentWidget(),
                window.parameters_panel.task_feedback_strip,
            )
            self.assertIs(
                window.command_panel.kamikaze_card.parentWidget(),
                window.parameters_panel.task_feedback_strip,
            )
            self.assertIs(
                window.command_panel.actuator_card.parentWidget(),
                window.parameters_panel.task_feedback_strip,
            )
            self.assertFalse(
                window.command_panel.task_tab.isAncestorOf(
                    window.command_panel.kamikaze_card
                )
            )
            window.parameters_panel.tabs.setCurrentIndex(1)
            for row in range(window.command_panel.task_list.count()):
                item = window.command_panel.task_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == "KAMIKAZE":
                    window.command_panel.task_list.setCurrentRow(row)
                    break
            QTest.qWait(50)
            self.assertLessEqual(
                abs(
                    window.parameters_panel.task_feedback_strip.width()
                    - window.parameters_panel.mission_commands_tab.width()
                ),
                2,
            )
            self.assertEqual(
                window.command_panel.kamikaze_card.width(),
                window.parameters_panel.task_feedback_strip.width(),
            )
            self.assertGreater(
                window.command_panel.kamikaze_status.width(),
                window.command_panel.kamikaze_title.width(),
            )
            self.assertLessEqual(
                abs(
                    window.command_panel.kamikaze_phase.width()
                    - window.command_panel.kamikaze_qr.width()
                ),
                6,
            )
            self.assertEqual(window.command_panel.kamikaze_card.height(), 48)
            self.assertTrue(
                window.command_panel.kamikaze_title.alignment()
                & Qt.AlignmentFlag.AlignLeft
            )
            self.assertTrue(window.command_panel.kamikaze_detail.isHidden())
            self.assertTrue(
                window.command_panel.kamikaze_phase.alignment()
                & Qt.AlignmentFlag.AlignLeft
            )
            self.assertTrue(
                window.command_panel.kamikaze_qr.alignment()
                & Qt.AlignmentFlag.AlignRight
            )
            self.assertEqual(
                window.command_panel.task_progress.width(),
                window.parameters_panel.task_feedback_strip.width(),
            )
            self.assertLessEqual(
                window.parameters_panel.task_feedback_strip.height(),
                57,
            )
            self.assertEqual(
                window.connection_popup.settings_button.text(),
                "All Settings",
            )
            self.assertEqual(
                window.log_panel.table.horizontalHeaderItem(3).text(),
                "MESSAGE",
            )

            window.close()
            runtime.stop()
            window.deleteLater()
            QTest.qWait(100)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
