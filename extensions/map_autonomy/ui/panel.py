from __future__ import annotations

from collections import deque
import json
import math
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QElapsedTimer, QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from src.ui.i18n import t
from src.models.commands import CommandResult
from src.models.geometry import Point
from .tools import TOOL_LABEL_KEYS, map_tool_config
from src.models.mission import MapContact, MapOverlay, MissionState
from src.models.telemetry import TelemetrySnapshot
from extensions.map_autonomy.services.planner import PlannerService
from src.services.settings import SettingsService
from src.ui.themes.style import ThemePalette
from src.ui.widgets.common import PanelFrame

try:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineCore import (
        QWebEnginePage,
        QWebEngineProfile,
        QWebEngineSettings,
        QWebEngineUrlRequestInterceptor,
    )
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    WEBENGINE_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - runtime fallback
    WEBENGINE_ERROR = str(exc)
    WEBENGINE_AVAILABLE = False
    QWebChannel = object  # type: ignore[assignment]
    QWebEnginePage = object  # type: ignore[assignment]
    QWebEngineProfile = object  # type: ignore[assignment]
    QWebEngineSettings = object  # type: ignore[assignment]
    QWebEngineUrlRequestInterceptor = object  # type: ignore[assignment]
    QWebEngineView = QLabel  # type: ignore[assignment]


class MapRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def interceptRequest(self, info) -> None:  # pragma: no cover - Qt callback
        if info.requestUrl().scheme() in {"http", "https"}:
            info.setHttpHeader(
                b"User-Agent",
                b"YKI/1.0 (PyQt6 Ground Control Station)",
            )
            info.setHttpHeader(b"Accept-Language", b"tr-TR,tr;q=0.9,en;q=0.8")


class MapWebBridge(QObject):
    _MAX_PAYLOAD_BYTES = 256 * 1024
    _MAX_OVERLAY_ID_LENGTH = 128
    _MAX_OVERLAY_POINTS = 4096
    _MAP_ACTION_TYPES = frozenset(
        {
            "tool_changed",
            "mission_send_request",
            "mission_download_request",
            "mission_loop_changed",
            "history_batch_begin",
            "history_batch_end",
            "history_undo",
            "history_redo",
            "history_home_command",
            "layer_changed",
            "mission_item_created",
            "goto_request",
            "set_home_reference",
            "land_request",
            "takeoff_request",
            "center_vehicle",
            "tile_notice",
        }
    )

    js_ready = pyqtSignal()
    telemetry_pushed = pyqtSignal(str)
    state_delta_applied = pyqtSignal(str)
    overlay_changed = pyqtSignal(dict)
    overlay_removed = pyqtSignal(str)
    overlay_selected = pyqtSignal(object)
    map_action = pyqtSignal(dict)
    bounds_changed = pyqtSignal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._initial_config = "{}"

    def set_initial_config(self, config: dict[str, Any]) -> None:
        self._initial_config = json.dumps(config)

    @pyqtSlot(result=str)
    def initialConfig(self) -> str:
        return self._initial_config

    @pyqtSlot()
    def jsReady(self) -> None:
        self.js_ready.emit()

    @pyqtSlot(str)
    def overlayChanged(self, payload: str) -> None:
        parsed = self._parse_object(payload)
        if parsed is not None and self._valid_overlay(parsed):
            self.overlay_changed.emit(parsed)

    @pyqtSlot(str)
    def overlayRemoved(self, overlay_id: str) -> None:
        if self._valid_overlay_id(overlay_id):
            self.overlay_removed.emit(overlay_id)

    @pyqtSlot(str)
    def overlaySelected(self, overlay_id: str) -> None:
        if not overlay_id:
            self.overlay_selected.emit(None)
        elif self._valid_overlay_id(overlay_id):
            self.overlay_selected.emit(overlay_id)

    @pyqtSlot(str)
    def mapAction(self, payload: str) -> None:
        parsed = self._parse_object(payload)
        if parsed is not None and self._valid_map_action(parsed):
            self.map_action.emit(parsed)

    @pyqtSlot(str)
    def boundsChanged(self, payload: str) -> None:
        parsed = self._parse_object(payload)
        if parsed is not None:
            self.bounds_changed.emit(parsed)

    @staticmethod
    def _parse_object(payload: str) -> dict[str, Any] | None:
        if not isinstance(payload, str):
            return None
        if len(payload) > MapWebBridge._MAX_PAYLOAD_BYTES:
            return None
        if len(payload.encode("utf-8", errors="replace")) > MapWebBridge._MAX_PAYLOAD_BYTES:
            return None
        try:
            value = json.loads(payload)
        except (TypeError, ValueError, RecursionError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _valid_overlay_id(value: object) -> bool:
        return (
            isinstance(value, str)
            and bool(value)
            and len(value) <= MapWebBridge._MAX_OVERLAY_ID_LENGTH
            and value.isprintable()
        )

    @classmethod
    def _valid_overlay(cls, payload: dict[str, Any]) -> bool:
        overlay_type = payload.get("overlay_type")
        label = payload.get("label", "")
        color = payload.get("color", "#61A8FF")
        points = payload.get("points", [])
        metadata = payload.get("metadata", {})
        radius = payload.get("radius_m")
        if not cls._valid_overlay_id(payload.get("overlay_id")):
            return False
        if (
            not isinstance(overlay_type, str)
            or not overlay_type
            or len(overlay_type) > 64
            or not overlay_type.isprintable()
        ):
            return False
        if not isinstance(label, str) or len(label) > 256 or not label.isprintable():
            return False
        if (
            not isinstance(color, str)
            or not color
            or len(color) > 64
            or not color.isprintable()
        ):
            return False
        if not isinstance(points, list) or len(points) > cls._MAX_OVERLAY_POINTS:
            return False
        if not isinstance(metadata, dict):
            return False
        if radius is not None and not cls._valid_number(radius, minimum=0.0, exclusive=True):
            return False
        for point in points:
            if not isinstance(point, list) or len(point) not in {2, 3}:
                return False
            if not cls._valid_number(point[0], -90.0, 90.0):
                return False
            if not cls._valid_number(point[1], -180.0, 180.0):
                return False
            if len(point) == 3 and not cls._valid_number(point[2]):
                return False
        return True

    @classmethod
    def _valid_map_action(cls, payload: dict[str, Any]) -> bool:
        action = payload.get("type")
        if not isinstance(action, str) or action not in cls._MAP_ACTION_TYPES:
            return False
        if action in {"goto_request", "history_home_command"}:
            return cls._valid_coordinates(payload)
        if action == "set_home_reference":
            if not cls._valid_coordinates(payload):
                return False
            previous = {
                "lat": payload.get("previous_lat", payload["lat"]),
                "lon": payload.get("previous_lon", payload["lon"]),
            }
            return cls._valid_coordinates(previous)
        if action in {"mission_send_request", "mission_loop_changed"}:
            return "loop" not in payload or isinstance(payload["loop"], bool)
        if action in {"tool_changed", "layer_changed"}:
            key = "tool" if action == "tool_changed" else "layer"
            value = payload.get(key, "pan" if key == "tool" else "vector")
            return isinstance(value, str) and 0 < len(value) <= 64
        if action == "history_batch_begin":
            label = payload.get("label", "map-edit")
            return isinstance(label, str) and len(label) <= 128
        if action == "tile_notice":
            message = payload.get("message", "Harita katmani gecici olarak ulasilamiyor")
            return isinstance(message, str) and len(message) <= 256
        return True

    @classmethod
    def _valid_coordinates(cls, payload: dict[str, Any]) -> bool:
        return cls._valid_number(payload.get("lat"), -90.0, 90.0) and cls._valid_number(
            payload.get("lon"), -180.0, 180.0
        )

    @staticmethod
    def _valid_number(
        value: object,
        minimum: float | None = None,
        maximum: float | None = None,
        *,
        exclusive: bool = False,
    ) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            number = float(value)
        except OverflowError:
            return False
        if not math.isfinite(number):
            return False
        if minimum is not None and (number <= minimum if exclusive else number < minimum):
            return False
        if maximum is not None and number > maximum:
            return False
        return True


class MapPanel(PanelFrame):
    command_requested = pyqtSignal(str, dict)
    info_generated = pyqtSignal(str, str)
    _VEHICLE_MISSION_TYPES = {"takeoff", "waypoint", "loiter", "rtl", "land", "route"}
    _TELEMETRY_RENDER_INTERVAL_MS = 33
    _MAX_STARTUP_SCRIPTS = 256
    _MAX_HISTORY_ENTRIES = 100
    _GUIDED_GOTO_MIN_ARRIVAL_RADIUS_M = 5.0

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        theme: ThemePalette,
        language: str,
        base_dir: Path,
        planner_service: PlannerService,
        settings_service: SettingsService,
        tile_cache_service,
        parent=None,
    ) -> None:
        super().__init__(title, subtitle, parent)
        self._theme = theme
        self._language = language
        self._base_dir = base_dir
        self._planner_service = planner_service
        self._settings_service = settings_service
        self._js_ready = False
        self._script_queue: deque[str] = deque(maxlen=self._MAX_STARTUP_SCRIPTS)
        self._base_layer = settings_service.current.map_layer
        self._align_initial_base_layer()
        self._cache_index_path = self._cache_index_file()
        self._tile_cache = tile_cache_service
        self._tile_cache.cache_changed.connect(self._on_tile_cache_changed)
        self._pending_cache_count = self._tile_cache.count_tiles()
        self._last_cache_count: int | None = None
        self._cache_count_timer = QTimer(self)
        self._cache_count_timer.setSingleShot(True)
        self._cache_count_timer.timeout.connect(self._flush_cache_count)
        self._overlay_cache: dict[str, dict[str, Any]] = {}
        self._contact_cache: dict[str, dict[str, Any]] = {}
        self._selected_overlay_id: str | None = None
        self._active_tool = "pan"
        self._mission_loop = bool(
            getattr(
                getattr(self._planner_service, "state", MissionState()),
                "mission_loop",
                False,
            )
        )
        self._mission_upload_pending = False
        self._pending_mission_signature: str | None = None
        self._last_uploaded_mission_signature: str | None = None
        self._mission_transfer_state = "idle"
        self._history_undo: deque[dict[str, Any]] = deque(
            maxlen=self._MAX_HISTORY_ENTRIES
        )
        self._history_redo: deque[dict[str, Any]] = deque(
            maxlen=self._MAX_HISTORY_ENTRIES
        )
        self._history_batch_before: dict[str, Any] | None = None
        self._history_batch_label = ""
        self._history_mutation_in_progress = False
        # Planner updates cross the Qt adapter queue. Keep the snapshots caused
        # by this panel's own mutations so their delayed delivery is not
        # mistaken for an external plan replacement and allowed to erase undo.
        self._pending_local_plan_syncs: deque[dict[str, Any]] = deque(
            maxlen=self._MAX_HISTORY_ENTRIES * 2
        )
        self._pending_home_record: dict[str, Any] | None = None
        self._pending_home_navigation: dict[str, Any] | None = None
        initial_planner_state = getattr(self._planner_service, "state", MissionState())
        self._history_known_plan = self._capture_local_plan(initial_planner_state)
        self._history_known_revision = int(
            getattr(initial_planner_state, "revision", 0)
        )
        self._last_vehicle_point: Point | None = None
        self._last_vehicle_timestamp = None
        self._guided_goto_target: Point | None = None
        self._guided_goto_arrival_radius_m = 0.0
        self._navigation_mode = ""
        self._flight_mode = ""
        self._last_telemetry_payload: dict[str, Any] | None = None
        self._pending_telemetry_payload: dict[str, Any] | None = None
        self._telemetry_push_clock = QElapsedTimer()
        self._telemetry_push_clock.start()
        self._last_telemetry_push_ms = -self._TELEMETRY_RENDER_INTERVAL_MS
        self._telemetry_push_timer = QTimer(self)
        self._telemetry_push_timer.setSingleShot(True)
        self._telemetry_push_timer.timeout.connect(self._flush_telemetry_payload)
        self._profile = None
        self._page = None
        self._view = None
        # Keep the Python wrapper alive for the complete lifetime of the page.
        # The page owns the C++ object, but losing the wrapper can detach the
        # WebChannel bridge before JavaScript has finished its startup handshake.
        self._channel = None
        self._bridge = None
        self._interceptor = None
        self._shutting_down = False
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._web_container = QWidget(self)
        self._web_layout = QVBoxLayout(self._web_container)
        self._web_layout.setContentsMargins(0, 0, 0, 0)
        self._web_layout.setSpacing(0)
        self.body_layout.addWidget(self._web_container, 1)

        self._build_webview()
        self.retranslate(language)

        planner_service.planner_changed.connect(self._sync_mission_state)
        settings_service.cache_cleared.connect(self._on_cache_cleared)

    def shutdown(self) -> None:
        """Stop WebEngine activity before Qt tears down the application."""

        if self._shutting_down:
            return
        self._shutting_down = True
        self._js_ready = False
        self._script_queue.clear()
        self._pending_telemetry_payload = None
        self._cache_count_timer.stop()
        self._telemetry_push_timer.stop()
        bridge = self._bridge
        self._bridge = None
        if bridge is not None:
            bridge.blockSignals(True)
        self._channel = None
        page = self._page
        self._page = None
        if page is not None:
            page.setWebChannel(None)
        view = self._view
        self._view = None
        if view is not None:
            view.stop()
            view.hide()
            self._web_layout.removeWidget(view)
            view.setParent(None)
            view.deleteLater()
        if page is not None:
            page.deleteLater()
        self._profile = None
        self._interceptor = None

    def _align_initial_base_layer(self) -> None:
        state = getattr(self._planner_service, "state", MissionState())
        preferred_layer = self._settings_service.current.map_layer
        if (
            preferred_layer in {"vector", "satellite"}
            and state.base_layer != preferred_layer
        ):
            if hasattr(self._planner_service, "set_base_layer"):
                self._planner_service.set_base_layer(preferred_layer)

    def retranslate(self, language: str) -> None:
        self._language = language
        self._send_ui_config()
        state = getattr(self._planner_service, "state", MissionState())
        self._update_summary(state)
        message_key = {
            "pending": "map.transfer.sending",
            "idle": "map.transfer.empty",
            "success": "map.transfer.synced",
            "dirty": "map.transfer.dirty",
        }.get(self._mission_transfer_state)
        if message_key:
            self._set_mission_transfer_state(
                self._mission_transfer_state,
                t(message_key, self._language),
            )

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self._send_ui_config()

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._flight_mode = snapshot.flight_mode.strip().upper()
        active = bool(
            snapshot.telemetry_link
            and snapshot.autopilot_link
            and snapshot.has_valid_position
        )
        reset_track = False
        if active:
            current_point = Point(
                snapshot.latitude,
                snapshot.longitude,
                snapshot.altitude_msl,
            )
            if (
                self._last_vehicle_point is not None
                and self._last_vehicle_timestamp is not None
            ):
                elapsed_s = max(
                    0.0,
                    (snapshot.timestamp - self._last_vehicle_timestamp).total_seconds(),
                )
                distance_m = self._last_vehicle_point.distance_to(current_point)
                plausible_distance_m = max(
                    2_000.0,
                    max(0.0, snapshot.ground_speed) * max(0.1, elapsed_s) * 4.0 + 500.0,
                )
                reset_track = distance_m > plausible_distance_m
                if reset_track:
                    self.info_generated.emit(
                        "warning",
                        f"Araç izi {distance_m / 1000.0:.1f} km konum sıçraması nedeniyle sıfırlandı",
                    )
            self._last_vehicle_point = current_point
            self._last_vehicle_timestamp = snapshot.timestamp
        else:
            self._last_vehicle_point = None
            self._last_vehicle_timestamp = None

        guided_goto: dict[str, object] | None = None
        guided_mode_active = self._flight_mode == "GUIDED"
        if active and guided_mode_active and self._guided_goto_target is not None:
            remaining_distance_m = current_point.distance_to(self._guided_goto_target)
            if remaining_distance_m <= self._guided_goto_arrival_radius_m:
                self.info_generated.emit(
                    "success",
                    "GUIDED GOTO hedefine varış mesafesine ulaşıldı",
                )
                self._guided_goto_target = None
                self._navigation_mode = ""
            else:
                guided_goto = {
                    "active": True,
                    "lat": self._guided_goto_target.latitude,
                    "lon": self._guided_goto_target.longitude,
                    "remainingDistanceM": remaining_distance_m,
                }

        home_latitude = snapshot.home_latitude
        home_longitude = snapshot.home_longitude
        home_position_available = bool(
            home_latitude is not None
            and home_longitude is not None
            and math.isfinite(float(home_latitude))
            and math.isfinite(float(home_longitude))
            and -90.0 <= float(home_latitude) <= 90.0
            and -180.0 <= float(home_longitude) <= 180.0
            and (abs(float(home_latitude)) > 1e-9 or abs(float(home_longitude)) > 1e-9)
        )
        payload = {
            "active": active,
            "commandAvailable": bool(snapshot.autopilot_link),
            "positionValid": snapshot.has_valid_position,
            "resetTrack": reset_track,
            "lat": snapshot.latitude,
            "lon": snapshot.longitude,
            "heading": snapshot.heading,
            "mode": snapshot.flight_mode,
            "alt": snapshot.altitude_agl,
            "speed": snapshot.ground_speed,
            "armed": snapshot.armed,
            "gpsReady": bool(
                snapshot.autopilot_link
                and snapshot.has_valid_position
                and snapshot.has_fresh_gps(3.0)
            ),
            "gpsFix": snapshot.gps_fix,
            "gpsSatellites": snapshot.gps_satellites,
            "gpsHdop": snapshot.gps_hdop,
            "localPositionAvailable": bool(
                snapshot.telemetry_link
                and snapshot.autopilot_link
                and snapshot.has_local_position
            ),
            "localNorth": snapshot.local_north_m,
            "localEast": snapshot.local_east_m,
            "localDown": snapshot.local_down_m,
            "homePositionAvailable": home_position_available,
            "homeLat": home_latitude if home_position_available else None,
            "homeLon": home_longitude if home_position_available else None,
            "guidedGoto": guided_goto,
        }
        if payload == self._last_telemetry_payload:
            return
        self._last_telemetry_payload = payload
        self._pending_telemetry_payload = payload
        if not self._js_ready:
            return
        self._schedule_telemetry_payload()

    def _schedule_telemetry_payload(self) -> None:
        if self._shutting_down or self._pending_telemetry_payload is None:
            return
        elapsed_ms = self._telemetry_push_clock.elapsed() - self._last_telemetry_push_ms
        remaining_ms = self._TELEMETRY_RENDER_INTERVAL_MS - elapsed_ms
        if remaining_ms <= 0:
            self._flush_telemetry_payload()
        elif not self._telemetry_push_timer.isActive():
            self._telemetry_push_timer.start(max(1, remaining_ms))

    def _flush_telemetry_payload(self) -> None:
        if self._shutting_down or not self._js_ready:
            return
        payload = self._pending_telemetry_payload
        self._pending_telemetry_payload = None
        if payload is None:
            return
        self._last_telemetry_push_ms = self._telemetry_push_clock.elapsed()
        self._push_telemetry_payload(payload)

    def _push_telemetry_payload(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if self._bridge is not None:
            self._bridge.telemetry_pushed.emit(encoded)
            return
        self._run_script(f"window.ykiMap && window.ykiMap.pushTelemetry({encoded});")

    def command_started(self, code: str, parameters: dict[str, Any]) -> None:
        normalized = code.strip().upper()
        if normalized != "MISSION_UPLOAD":
            return
        self._mission_upload_pending = True
        overlays = parameters.get("overlays")
        if overlays is None:
            state = getattr(self._planner_service, "state", MissionState())
            overlays = state.overlays
        loop = bool(parameters.get("loop", False))
        self._pending_mission_signature = self._vehicle_mission_signature(
            overlays,
            loop=loop,
        )
        self._set_mission_transfer_state(
            "pending",
            t("map.transfer.sending", self._language),
        )

    @staticmethod
    def _request_target(result: CommandResult) -> Point | None:
        try:
            return Point(
                float(result.request.parameters["latitude"]),
                float(result.request.parameters["longitude"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def command_completed(self, result: CommandResult) -> None:
        if result.request.code == "GOTO":
            target = self._request_target(result)
            if (
                not result.accepted
                and target is not None
                and self._guided_goto_target is not None
                and target.distance_to(self._guided_goto_target) <= 1.0
            ):
                self._guided_goto_target = None
                self._navigation_mode = ""
            return
        if result.request.code == "SET_HOME":
            self._complete_home_history(result.accepted)
            fallback_latitude = None
            fallback_longitude = None
            if self._last_telemetry_payload:
                fallback_latitude = self._last_telemetry_payload.get("homeLat")
                fallback_longitude = self._last_telemetry_payload.get("homeLon")
            self._run_script(
                "window.ykiMap && window.ykiMap.resolveHomeReference("
                f"{json.dumps(result.accepted)}, "
                f"{json.dumps(fallback_latitude)}, "
                f"{json.dumps(fallback_longitude)});"
            )
            return
        if result.request.code != "MISSION_UPLOAD":
            return
        self._mission_upload_pending = False
        state = "success" if result.accepted else "danger"
        if result.accepted:
            self._mission_loop = bool(result.request.parameters.get("loop", False))
            if hasattr(self._planner_service, "set_mission_loop"):
                self._planner_service.set_mission_loop(self._mission_loop)
            self._run_script(
                "window.ykiMap && window.ykiMap.setMissionLoop("
                f"{json.dumps(self._mission_loop)});"
            )
            self._last_uploaded_mission_signature = (
                self._pending_mission_signature
                or self._vehicle_mission_signature(
                    result.request.parameters.get("overlays", []),
                    loop=bool(result.request.parameters.get("loop", False)),
                )
            )
        self._pending_mission_signature = None
        self._set_mission_transfer_state(
            state,
            t(
                "map.transfer.uploaded" if result.accepted else "map.transfer.failed",
                self._language,
            ),
        )

    def _set_mission_transfer_state(self, state: str, message: str) -> None:
        self._mission_transfer_state = state
        self._run_script(
            "window.ykiMap && window.ykiMap.setMissionTransferState("
            f"{json.dumps(state)}, {json.dumps(message)});"
        )

    def _build_webview(self) -> None:
        self._cache_index_path = self._cache_index_file()
        self._migrate_legacy_cache_index()
        settings = self._settings_service.current
        # Runtime resources are owned by MapExtension, not by a widget refresh.
        self._cache_base_url = self._tile_cache.base_url
        if self._view:
            self._view.deleteLater()
            self._view = None
        if self._page:
            self._page.deleteLater()
            self._page = None
        self._channel = None
        self._profile = None
        self._interceptor = None

        while self._web_layout.count():
            item = self._web_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not WEBENGINE_AVAILABLE:
            placeholder = QLabel(
                t("map.webengine.unavailable", self._language)
                + "\n"
                + WEBENGINE_ERROR
                + "\npython -m pip install -r extensions/map_autonomy/requirements.txt",
                self,
            )
            placeholder.setWordWrap(True)
            self._web_layout.addWidget(placeholder)
            self._view = None
            return

        # Tile persistence belongs to TileCacheServer, not a second Chromium
        # disk cache. Off-the-record profiles also close without background
        # storage writes after an extension unload.
        self._profile = QWebEngineProfile(self)
        self._interceptor = MapRequestInterceptor(self)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._profile.setHttpUserAgent("YKI/1.0 (PyQt6 Ground Control Station)")

        cache_type = getattr(
            QWebEngineProfile.HttpCacheType,
            "NoCache",
            QWebEngineProfile.HttpCacheType.MemoryHttpCache,
        )
        self._profile.setHttpCacheType(cache_type)
        cache_size_bytes = min(settings.cache_limit_mb * 1024 * 1024, 2_147_000_000)
        self._profile.setHttpCacheMaximumSize(cache_size_bytes)

        self._page = QWebEnginePage(self._profile, self)
        self._profile.setParent(self._page)
        web_settings = self._page.settings()
        web_settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        web_settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        web_settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalStorageEnabled, True
        )
        web_settings.setAttribute(
            QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False
        )

        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.setMinimumHeight(360)
        self._view.setMinimumWidth(0)
        self._view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        channel = QWebChannel(self._page)
        self._channel = channel
        self._bridge = MapWebBridge(self)
        self._bridge.set_initial_config(self._ui_config())
        channel.registerObject("qtBridge", self._bridge)
        self._page.setWebChannel(channel)
        self._bridge.js_ready.connect(self._on_js_ready)
        self._bridge.overlay_changed.connect(self._on_overlay_changed)
        self._bridge.overlay_removed.connect(self._remove_overlay)
        self._bridge.overlay_selected.connect(self._select_overlay)
        self._bridge.map_action.connect(self._on_map_action)
        self._bridge.bounds_changed.connect(self._on_bounds_changed)

        self._web_layout.addWidget(self._view)
        self._js_ready = False
        self._reset_map_view_state()
        self._last_telemetry_payload = None
        self._pending_telemetry_payload = None
        self._last_cache_count = None
        html_path = Path(__file__).resolve().parents[1] / "web" / "map.html"
        self._view.load(QUrl.fromLocalFile(str(html_path)))

    def _reset_map_view_state(self) -> None:
        """Discard browser-side caches before rebuilding an authoritative map."""

        self._script_queue.clear()
        self._overlay_cache.clear()
        self._contact_cache.clear()
        self._selected_overlay_id = None
        self._active_tool = ""
        self._base_layer = ""

    def rebuild_cache_profile(self) -> None:
        self._build_webview()
        state = getattr(self._planner_service, "state", MissionState())
        self._sync_mission_state(state)

    def _on_js_ready(self) -> None:
        self._js_ready = True
        # Rebuild one authoritative map snapshot instead of replaying every
        # startup mutation. This keeps a slow WebEngine boot from accumulating
        # stale scripts and guarantees that no bounded-queue eviction can omit
        # an overlay.
        self._reset_map_view_state()
        self._send_ui_config()
        state = getattr(self._planner_service, "state", MissionState())
        self._sync_mission_state(state)
        message_key = {
            "pending": "map.transfer.sending",
            "idle": "map.transfer.empty",
            "success": "map.transfer.synced",
            "dirty": "map.transfer.dirty",
        }.get(self._mission_transfer_state)
        if message_key:
            self._set_mission_transfer_state(
                self._mission_transfer_state,
                t(message_key, self._language),
            )
        self._schedule_telemetry_payload()

    def _send_ui_config(self) -> None:
        config = self._ui_config()
        if self._bridge:
            self._bridge.set_initial_config(config)
        self._run_script(
            f"window.ykiMap && window.ykiMap.setUiConfig({json.dumps(config)});"
        )

    def _ui_config(self) -> dict[str, Any]:
        settings = self._settings_service.current
        state = getattr(self._planner_service, "state", MissionState())
        current_layer = state.base_layer or settings.map_layer
        tool_config = map_tool_config()
        return {
            "layers": {
                "vector": t("map.layer.vector", self._language),
                "satellite": t("map.layer.satellite", self._language),
            },
            "missionMode": {
                "once": t("map.mission.once", self._language),
                "loop": t("map.mission.loop", self._language),
                "onceHint": t("map.mission.once_hint", self._language),
                "loopHint": t("map.mission.loop_hint", self._language),
            },
            "pointList": {
                "dragHint": t("map.point_list.drag_hint", self._language),
            },
            "missionLoop": self._mission_loop,
            "tools": {
                key: t(label_key, self._language)
                for key, label_key in TOOL_LABEL_KEYS.items()
            },
            "toolOrder": {
                "primary": tool_config["primary"],
                "transfer": tool_config["transfer"],
            },
            "missionTypes": tool_config["missionTypes"],
            "context": {
                "goto": t("map.context.goto", self._language),
                "center": t("map.context.center", self._language),
                "set_home": t("map.context.set_home", self._language),
                "delete": t("map.context.delete", self._language),
            },
            "theme": {
                "name": self._theme.name,
                "background": self._theme.background,
                "surface": self._theme.surface,
                "surfaceAlt": self._theme.surface_alt,
                "surfaceHover": self._theme.surface_hover,
                "border": self._theme.border,
                "borderStrong": self._theme.border_strong,
                "text": self._theme.text,
                "muted": self._theme.muted_text,
                "accent": self._theme.accent,
                "accentDim": self._theme.accent_dim,
                "success": self._theme.success,
                "warning": self._theme.warning,
                "danger": self._theme.danger,
                "info": self._theme.info,
            },
            "tileCache": {
                "enabled": settings.cache_enabled,
                "baseUrl": self._tile_cache.base_url if settings.cache_enabled else "",
            },
            "currentLayer": current_layer,
            "missionDefaults": {
                "defaultAltitude": settings.backend.mission.default_altitude_m,
                "radii": {
                    "takeoff": settings.default_radius_takeoff,
                    "waypoint": settings.default_radius_waypoint,
                    "loiter": settings.default_radius_loiter,
                    "rtl": settings.default_radius_rtl,
                    "land": settings.default_radius_land,
                },
                "centerMinZoom": settings.map_center_min_zoom,
                "vehicleTrailLimit": settings.vehicle_trail_limit,
                "contactTrailLimit": settings.contact_trail_limit,
            },
        }

    def _sync_mission_state(self, state: MissionState) -> None:
        incoming_revision = int(getattr(state, "revision", 0))
        if incoming_revision < self._history_known_revision:
            # Qt delivers planner snapshots asynchronously. A selection signal
            # emitted just before an add/delete can arrive after the newer plan
            # mutation; rendering it would erase both the map change and its
            # undo record.
            return
        self._history_known_revision = incoming_revision
        incoming_local_plan = self._capture_local_plan(state)
        expected_local_sync = (
            bool(self._pending_local_plan_syncs)
            and incoming_local_plan == self._pending_local_plan_syncs[0]
        )
        if expected_local_sync:
            self._pending_local_plan_syncs.popleft()
        elif (
            incoming_local_plan != self._history_known_plan
            and not self._history_mutation_in_progress
        ):
            self._discard_plan_history()
        self._history_known_plan = incoming_local_plan
        planned_loop = bool(getattr(state, "mission_loop", False))
        loop_changed = planned_loop != self._mission_loop
        if loop_changed:
            self._mission_loop = planned_loop
            self._run_script(
                "window.ykiMap && window.ykiMap.setMissionLoop("
                f"{json.dumps(planned_loop)});"
            )
        overlays = [overlay.to_dict() for overlay in state.overlays]
        contact_trail_limit = max(
            1,
            int(self._settings_service.current.contact_trail_limit),
        )
        contacts = []
        for contact in getattr(state, "contacts", []):
            contacts.append(self._contact_payload(contact, contact_trail_limit))
        overlay_lookup = {item["overlay_id"]: item for item in overlays}
        contact_lookup = {item["contact_id"]: item for item in contacts}

        removed_overlays = sorted(set(self._overlay_cache) - set(overlay_lookup))
        changed_overlays = [
            payload
            for payload in overlays
            if self._overlay_cache.get(payload["overlay_id"]) != payload
        ]
        removed_contacts = sorted(set(self._contact_cache) - set(contact_lookup))
        changed_contacts = [
            payload
            for payload in contacts
            if self._contact_cache.get(payload["contact_id"]) != payload
        ]
        compact_contacts: list[dict[str, Any]] = []
        for payload in changed_contacts:
            previous = self._contact_cache.get(payload["contact_id"])
            previous_trail = list((previous or {}).get("trail_points", []))
            current_trail = list(payload.get("trail_points", []))
            if previous is not None and (
                (
                    len(current_trail) == len(previous_trail) + 1
                    and current_trail[:-1] == previous_trail
                )
                or (
                    len(current_trail) == len(previous_trail)
                    and len(current_trail) > 1
                    and current_trail[:-1] == previous_trail[1:]
                )
            ):
                compact = dict(payload)
                compact["trail_points"] = current_trail[-1:]
                compact["trail_incremental"] = True
                compact_contacts.append(compact)
            else:
                compact_contacts.append(payload)
        overlay_order = [overlay["overlay_id"] for overlay in overlays]
        overlay_order_changed = list(self._overlay_cache) != overlay_order
        if (
            removed_overlays
            or changed_overlays
            or removed_contacts
            or changed_contacts
            or overlay_order_changed
        ):
            delta = {
                "removeOverlays": removed_overlays,
                "upsertOverlays": changed_overlays,
                "removeContacts": removed_contacts,
                "upsertContacts": compact_contacts,
                "overlayOrder": overlay_order if overlay_order_changed else None,
            }
            self._push_state_delta(delta)

        if state.selected_overlay_id != self._selected_overlay_id:
            self._run_script(
                f"window.ykiMap && window.ykiMap.setSelectedOverlay({json.dumps(state.selected_overlay_id)});"
            )
            self._selected_overlay_id = state.selected_overlay_id

        if state.active_tool != self._active_tool:
            self._run_script(
                f"window.ykiMap && window.ykiMap.setActiveTool({json.dumps(state.active_tool)});"
            )
            self._active_tool = state.active_tool

        if state.base_layer != self._base_layer:
            self._run_script(
                f"window.ykiMap && window.ykiMap.setBaseLayer({json.dumps(state.base_layer)});"
            )
            self._base_layer = state.base_layer

        self._overlay_cache = overlay_lookup
        self._contact_cache = contact_lookup
        if (
            removed_overlays
            or changed_overlays
            or overlay_order_changed
            or loop_changed
        ):
            self._sync_mission_transfer_freshness(state.overlays)
        self._update_summary(state)

    def _push_state_delta(self, delta: dict[str, Any]) -> None:
        encoded = json.dumps(delta, ensure_ascii=False, separators=(",", ":"))
        if self._js_ready and self._bridge is not None:
            self._bridge.state_delta_applied.emit(encoded)
            return
        self._run_script(f"window.ykiMap && window.ykiMap.applyStateDelta({encoded});")

    @staticmethod
    def _contact_payload(contact: MapContact, trail_limit: int) -> dict[str, Any]:
        return {
            "contact_id": contact.contact_id,
            "label": contact.label,
            "latitude": contact.latitude,
            "longitude": contact.longitude,
            "color": contact.color,
            "heading": contact.heading,
            "speed": contact.speed,
            "trail_points": [
                list(point) for point in contact.trail_points[-max(1, trail_limit) :]
            ],
            "source": contact.source,
            "metadata": dict(contact.metadata),
        }

    def _sync_mission_transfer_freshness(self, overlays) -> None:
        if self._mission_upload_pending:
            return
        relevant = [
            overlay
            for overlay in overlays
            if overlay.overlay_type in self._VEHICLE_MISSION_TYPES
        ]
        if not relevant:
            self._last_uploaded_mission_signature = None
            if self._mission_transfer_state not in {"idle", "danger"}:
                self._set_mission_transfer_state(
                    "idle",
                    t("map.transfer.empty", self._language),
                )
            return

        signature = self._vehicle_mission_signature(relevant, loop=self._mission_loop)
        if self._last_uploaded_mission_signature is None and all(
            str(overlay.metadata.get("source", "")).lower() == "flight-controller"
            for overlay in relevant
        ):
            self._last_uploaded_mission_signature = signature
            self._set_mission_transfer_state(
                "success",
                t("map.transfer.synced", self._language),
            )
            return
        if signature != self._last_uploaded_mission_signature:
            self._set_mission_transfer_state(
                "dirty",
                t("map.transfer.dirty", self._language),
            )

    @classmethod
    def _vehicle_mission_signature(cls, overlays, *, loop: bool = False) -> str:
        payloads: list[dict[str, Any]] = []
        for overlay in overlays:
            payload = (
                overlay.to_dict() if isinstance(overlay, MapOverlay) else dict(overlay)
            )
            if (
                str(payload.get("overlay_type", "")).lower()
                in cls._VEHICLE_MISSION_TYPES
            ):
                payloads.append(payload)
        return json.dumps(
            {"loop": bool(loop), "overlays": payloads},
            sort_keys=True,
            separators=(",", ":"),
        )

    def _update_summary(self, state: MissionState) -> None:
        del state
        count = self._tile_cache.count_tiles()
        if count == self._last_cache_count:
            return
        self._last_cache_count = count
        self._run_script(
            f"window.ykiMap && window.ykiMap.setCacheCount({json.dumps(count)});"
        )

    def _on_tile_cache_changed(self, count: int) -> None:
        base_url = self._tile_cache.base_url
        if base_url != getattr(self, "_cache_base_url", ""):
            self._cache_base_url = base_url
            self._send_ui_config()
        self._pending_cache_count = count
        if not self._cache_count_timer.isActive():
            self._cache_count_timer.start(500)

    def _flush_cache_count(self) -> None:
        if self._pending_cache_count == self._last_cache_count:
            return
        self._last_cache_count = self._pending_cache_count
        self._run_script(
            f"window.ykiMap && window.ykiMap.setCacheCount({json.dumps(self._pending_cache_count)});"
        )

    def _run_script(self, script: str) -> None:
        if self._shutting_down or not self._view:
            return
        if not self._js_ready:
            self._script_queue.append(script)
            return
        self._view.page().runJavaScript(script)

    def _on_overlay_changed(self, payload: dict[str, Any]) -> None:
        existing_payload = self._overlay_cache.get(str(payload.get("overlay_id", "")))
        if (
            existing_payload
            and MapOverlay.from_dict(existing_payload).is_source_managed
        ):
            return
        overlay = MapOverlay(
            overlay_id=payload["overlay_id"],
            overlay_type=payload["overlay_type"],
            label=payload.get("label") or payload["overlay_type"].upper(),
            points=payload.get("points", []),
            radius_m=payload.get("radius_m"),
            color=payload.get("color", "#61A8FF"),
            metadata=payload.get("metadata", {}),
        )
        self._overlay_cache[overlay.overlay_id] = overlay.to_dict()
        if hasattr(self._planner_service, "upsert_overlay"):
            self._mutate_local_plan(
                lambda: self._planner_service.upsert_overlay(overlay),
                label="overlay",
            )

    def _remove_overlay(self, overlay_id: str) -> None:
        existing_payload = self._overlay_cache.get(overlay_id)
        if (
            existing_payload
            and MapOverlay.from_dict(existing_payload).is_source_managed
        ):
            return
        self._overlay_cache.pop(overlay_id, None)
        if self._selected_overlay_id == overlay_id:
            self._selected_overlay_id = None
        if hasattr(self._planner_service, "remove_overlay"):
            self._mutate_local_plan(
                lambda: self._planner_service.remove_overlay(overlay_id),
                label="delete",
            )

    def _select_overlay(self, overlay_id: str | None) -> None:
        self._selected_overlay_id = overlay_id
        if hasattr(self._planner_service, "select_overlay"):
            self._planner_service.select_overlay(overlay_id)

    def _on_map_action(self, payload: dict[str, Any]) -> None:
        action = payload.get("type")
        if action == "tool_changed":
            tool = payload.get("tool", "pan")
            if tool == "clear":
                if hasattr(self._planner_service, "clear_dynamic_overlays"):
                    self._mutate_local_plan(
                        self._planner_service.clear_dynamic_overlays,
                        label="clear",
                    )
                return
            self._active_tool = tool
            if hasattr(self._planner_service, "set_active_tool"):
                self._planner_service.set_active_tool(tool)
        elif action == "mission_send_request":
            self._mission_loop = bool(payload.get("loop", self._mission_loop))
            state = getattr(self._planner_service, "state", MissionState())
            overlays = [
                overlay for overlay in state.overlays if overlay.is_vehicle_mission
            ]
            self.command_requested.emit(
                "MISSION_UPLOAD",
                {
                    "overlays": [overlay.to_dict() for overlay in overlays],
                    "selected_overlay_id": state.selected_overlay_id,
                    "loop": self._mission_loop,
                },
            )
        elif action == "mission_download_request":
            request_sync = getattr(self._planner_service, "request_vehicle_sync", None)
            if not callable(request_sync) or not request_sync():
                self.info_generated.emit(
                    "warning",
                    t("map.transfer.download_unavailable", self._language),
                )
            else:
                self.info_generated.emit(
                    "info",
                    t("map.transfer.downloading", self._language),
                )
        elif action == "mission_loop_changed":
            self._mission_loop = bool(payload.get("loop", False))
            if hasattr(self._planner_service, "set_mission_loop"):
                self._mutate_local_plan(
                    lambda: self._planner_service.set_mission_loop(self._mission_loop),
                    label="mission-loop",
                )
            state = getattr(self._planner_service, "state", MissionState())
            self._sync_mission_transfer_freshness(state.overlays)
        elif action == "history_batch_begin":
            self._begin_history_batch(str(payload.get("label", "map-edit")))
        elif action == "history_batch_end":
            self._end_history_batch()
        elif action == "history_undo":
            self._navigate_history("undo")
        elif action == "history_redo":
            self._navigate_history("redo")
        elif action == "history_home_command":
            if self._pending_home_navigation is not None:
                parameters = {
                    "latitude": float(payload["lat"]),
                    "longitude": float(payload["lon"]),
                }
                if self._last_vehicle_point is not None:
                    parameters["altitude"] = self._last_vehicle_point.altitude
                self.command_requested.emit("SET_HOME", parameters)
        elif action == "layer_changed":
            layer = payload.get("layer", "vector")
            self._base_layer = layer
            if hasattr(self._planner_service, "set_base_layer"):
                self._planner_service.set_base_layer(layer)
            self._settings_service.patch(map_layer=layer)
        elif action == "mission_item_created":
            return
        elif action == "goto_request":
            latitude = float(payload["lat"])
            longitude = float(payload["lon"])
            if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
                self.info_generated.emit("warning", "GUIDED GOTO koordinatı geçersiz")
                return
            if self._flight_mode != "GUIDED":
                self.info_generated.emit(
                    "warning", "GOTO yalnızca GUIDED modunda kullanılabilir"
                )
                return
            settings = self._settings_service.current
            self._guided_goto_target = Point(latitude, longitude)
            self._navigation_mode = "goto"
            self._guided_goto_arrival_radius_m = max(
                self._GUIDED_GOTO_MIN_ARRIVAL_RADIUS_M,
                float(settings.backend.mission.radius_for("waypoint")),
            )
            self.command_requested.emit(
                "GOTO", {"latitude": latitude, "longitude": longitude}
            )
        elif action == "set_home_reference":
            previous = {
                "latitude": float(payload.get("previous_lat", payload["lat"])),
                "longitude": float(payload.get("previous_lon", payload["lon"])),
            }
            current = {
                "latitude": float(payload["lat"]),
                "longitude": float(payload["lon"]),
            }
            self._pending_home_record = {
                "kind": "home",
                "label": "home",
                "before": previous,
                "after": current,
            }
            parameters = {
                "latitude": payload["lat"],
                "longitude": payload["lon"],
            }
            if self._last_vehicle_point is not None:
                parameters["altitude"] = self._last_vehicle_point.altitude
            self.command_requested.emit("SET_HOME", parameters)
        elif action in {"land_request", "takeoff_request"}:
            # JavaScript creates the MapOverlay at the same time. The point
            # remains in the central plan and produces no immediate vehicle
            # command until the operator explicitly sends the plan.
            return
        elif action == "center_vehicle":
            return
        elif action == "tile_notice":
            message = payload.get(
                "message", "Harita katmani gecici olarak ulasilamiyor"
            )
            self.info_generated.emit("warning", message)

    def _capture_local_plan(
        self,
        state: MissionState | None = None,
    ) -> dict[str, Any]:
        snapshot = state or getattr(self._planner_service, "state", MissionState())
        return {
            "overlays": [
                overlay.to_dict()
                for overlay in snapshot.overlays
                if not overlay.is_source_managed
            ],
            "mission_loop": bool(getattr(snapshot, "mission_loop", False)),
        }

    def _mutate_local_plan(self, mutation, *, label: str) -> None:
        before = self._capture_local_plan()
        self._history_mutation_in_progress = True
        try:
            mutation()
        finally:
            self._history_mutation_in_progress = False
        after = self._capture_local_plan()
        self._history_known_revision = int(
            getattr(
                self._planner_service.state, "revision", self._history_known_revision
            )
        )
        self._history_known_plan = after
        self._expect_local_plan_sync(after)
        if self._history_batch_before is None:
            self._record_plan_history(before, after, label=label)

    def _expect_local_plan_sync(self, snapshot: dict[str, Any]) -> None:
        """Preserve history until the queued Qt planner signal arrives."""

        self._pending_local_plan_syncs.append(snapshot)

    def _record_plan_history(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        label: str,
    ) -> None:
        if before == after:
            return
        self._push_history(
            {
                "kind": "plan",
                "label": label,
                "before": before,
                "after": after,
            }
        )

    def _push_history(self, entry: dict[str, Any]) -> None:
        if entry.get("before") == entry.get("after"):
            return
        self._history_undo.append(entry)
        self._history_redo.clear()

    def _begin_history_batch(self, label: str) -> None:
        if self._history_batch_before is not None:
            return
        self._history_batch_before = self._capture_local_plan()
        self._history_batch_label = label

    def _end_history_batch(self) -> None:
        before = self._history_batch_before
        if before is None:
            return
        label = self._history_batch_label or "map-edit"
        self._history_batch_before = None
        self._history_batch_label = ""
        after = self._capture_local_plan()
        self._history_known_revision = int(
            getattr(
                self._planner_service.state, "revision", self._history_known_revision
            )
        )
        self._history_known_plan = after
        self._expect_local_plan_sync(after)
        self._record_plan_history(before, after, label=label)

    def _discard_plan_history(self) -> None:
        self._history_undo = deque(
            (entry for entry in self._history_undo if entry.get("kind") == "home"),
            maxlen=self._MAX_HISTORY_ENTRIES,
        )
        self._history_redo = deque(
            (entry for entry in self._history_redo if entry.get("kind") == "home"),
            maxlen=self._MAX_HISTORY_ENTRIES,
        )
        self._history_batch_before = None
        self._history_batch_label = ""
        self._pending_local_plan_syncs.clear()

    def _restore_local_plan(self, snapshot: dict[str, Any]) -> None:
        overlays = [
            MapOverlay.from_dict(payload) for payload in snapshot.get("overlays", [])
        ]
        self._history_mutation_in_progress = True
        try:
            if hasattr(self._planner_service, "replace_local_plan"):
                self._planner_service.replace_local_plan(
                    overlays,
                    mission_loop=bool(snapshot.get("mission_loop", False)),
                )
            else:
                self._planner_service.load_state(
                    MissionState(
                        overlays=overlays,
                        mission_loop=bool(snapshot.get("mission_loop", False)),
                    )
                )
        finally:
            self._history_mutation_in_progress = False
        self._history_known_plan = self._capture_local_plan()
        self._history_known_revision = int(
            getattr(
                self._planner_service.state, "revision", self._history_known_revision
            )
        )
        self._expect_local_plan_sync(self._history_known_plan)

    def _navigate_history(self, direction: str) -> None:
        if (
            self._pending_home_navigation is not None
            or self._pending_home_record is not None
        ):
            return
        source = self._history_undo if direction == "undo" else self._history_redo
        if not source:
            return
        entry = source[-1]
        if entry.get("kind") == "home":
            target_key = "before" if direction == "undo" else "after"
            target = entry[target_key]
            self._pending_home_navigation = {
                "direction": direction,
                "entry": entry,
            }
            self._run_script(
                "window.ykiMap && window.ykiMap.prepareHomeHistoryReference("
                f"{json.dumps(target['latitude'])}, "
                f"{json.dumps(target['longitude'])});"
            )
            return

        target_key = "before" if direction == "undo" else "after"
        try:
            self._restore_local_plan(entry[target_key])
        except Exception as exc:
            self.info_generated.emit("danger", str(exc))
            return
        source.pop()
        destination = self._history_redo if direction == "undo" else self._history_undo
        destination.append(entry)
        self.info_generated.emit(
            "info",
            t(f"map.history.{direction}", self._language),
        )

    def _complete_home_history(self, accepted: bool) -> None:
        navigation = self._pending_home_navigation
        record = self._pending_home_record
        self._pending_home_navigation = None
        self._pending_home_record = None
        if not accepted:
            return
        if navigation is not None:
            direction = str(navigation["direction"])
            entry = navigation["entry"]
            source = self._history_undo if direction == "undo" else self._history_redo
            if source and source[-1] == entry:
                source.pop()
                destination = (
                    self._history_redo if direction == "undo" else self._history_undo
                )
                destination.append(entry)
                self.info_generated.emit(
                    "info",
                    t(f"map.history.{direction}", self._language),
                )
            return
        if record is not None:
            self._push_history(record)

    def _cache_index_file(self) -> Path:
        return Path(self._settings_service.current.cache_path) / "cache-index.json"

    def _migrate_legacy_cache_index(self) -> None:
        legacy_path = self._base_dir / "data" / "cache-index.json"
        if legacy_path == self._cache_index_path or not legacy_path.exists():
            return
        try:
            archive_path = self._cache_index_path.parent / "coverage-index-legacy.json"
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.replace(archive_path)
        except OSError:
            return

    def _on_bounds_changed(self, payload: dict[str, Any]) -> None:
        del payload

    def _on_cache_cleared(self) -> None:
        self._cache_index_path = self._cache_index_file()
        self._tile_cache.clear()
        if self._profile:
            self._profile.clearHttpCache()
            self._profile.clearAllVisitedLinks()
        state = getattr(self._planner_service, "state", MissionState())
        self._update_summary(state)
