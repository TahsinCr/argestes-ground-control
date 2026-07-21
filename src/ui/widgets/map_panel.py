from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from src.ui.config.i18n import t
from src.models.commands import CommandResult
from src.models.geometry import Point
from src.ui.config.map import TOOL_LABEL_KEYS, map_tool_config
from src.models.mission import MapOverlay, MissionState
from src.models.telemetry import TelemetrySnapshot
from src.services.contracts import MissionService
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
except ImportError:  # pragma: no cover - runtime fallback
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
    js_ready = pyqtSignal()
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
        self.overlay_changed.emit(json.loads(payload))

    @pyqtSlot(str)
    def overlayRemoved(self, overlay_id: str) -> None:
        self.overlay_removed.emit(overlay_id)

    @pyqtSlot(str)
    def overlaySelected(self, overlay_id: str) -> None:
        self.overlay_selected.emit(overlay_id or None)

    @pyqtSlot(str)
    def mapAction(self, payload: str) -> None:
        self.map_action.emit(json.loads(payload))

    @pyqtSlot(str)
    def boundsChanged(self, payload: str) -> None:
        self.bounds_changed.emit(json.loads(payload))


class MapPanel(PanelFrame):
    command_requested = pyqtSignal(str, dict)
    info_generated = pyqtSignal(str, str)
    storage_requested = pyqtSignal(str)
    _VEHICLE_MISSION_TYPES = {"takeoff", "waypoint", "loiter", "rtl", "land", "route"}

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        theme: ThemePalette,
        language: str,
        base_dir: Path,
        mission_service: MissionService,
        settings_service: SettingsService,
        tile_cache_service,
        parent=None,
    ) -> None:
        super().__init__(title, subtitle, parent)
        self._theme = theme
        self._language = language
        self._base_dir = base_dir
        self._mission_service = mission_service
        self._settings_service = settings_service
        self._js_ready = False
        self._script_queue: list[str] = []
        self._base_layer = settings_service.current.map_layer
        self._align_initial_base_layer()
        self._cache_index_path = self._cache_index_file()
        self._tile_cache = tile_cache_service
        self._tile_cache.cache_changed.connect(self._on_tile_cache_changed)
        self.destroyed.connect(lambda: self._tile_cache.stop())
        self._migrate_legacy_cache_index()
        self._cache_entries = self._tile_cache.entries()
        self._cache_keys = {entry["key"] for entry in self._cache_entries if "key" in entry}
        self._pending_cache_count = self._tile_cache.count_tiles()
        self._cache_count_timer = QTimer(self)
        self._cache_count_timer.setSingleShot(True)
        self._cache_count_timer.timeout.connect(self._flush_cache_count)
        self._overlay_cache: dict[str, dict[str, Any]] = {}
        self._contact_cache: dict[str, dict[str, Any]] = {}
        self._selected_overlay_id: str | None = None
        self._active_tool = "pan"
        self._mission_loop = False
        self._mission_upload_pending = False
        self._pending_mission_signature: str | None = None
        self._last_uploaded_mission_signature: str | None = None
        self._mission_transfer_state = "idle"
        self._last_vehicle_point: Point | None = None
        self._last_vehicle_timestamp = None
        self._profile = None
        self._page = None
        self._view = None
        self._bridge = None
        self._interceptor = None
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._web_container = QWidget(self)
        self._web_layout = QVBoxLayout(self._web_container)
        self._web_layout.setContentsMargins(0, 0, 0, 0)
        self._web_layout.setSpacing(0)
        self.body_layout.addWidget(self._web_container, 1)

        self._build_webview()
        self.retranslate(language)

        mission_service.mission_state_changed.connect(self._sync_mission_state)
        settings_service.cache_cleared.connect(self._on_cache_cleared)

    def _align_initial_base_layer(self) -> None:
        state = getattr(self._mission_service, "state", MissionState())
        preferred_layer = self._settings_service.current.map_layer
        if preferred_layer in {"vector", "satellite"} and state.base_layer != preferred_layer:
            if hasattr(self._mission_service, "set_base_layer"):
                self._mission_service.set_base_layer(preferred_layer)

    def retranslate(self, language: str) -> None:
        self._language = language
        self._send_ui_config()
        state = getattr(self._mission_service, "state", MissionState())
        self._update_summary(state)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self._send_ui_config()

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
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
            if self._last_vehicle_point is not None and self._last_vehicle_timestamp is not None:
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
                and snapshot.gps_satellites > 0
                and snapshot.gps_fix not in {"NO GPS", "NO FIX"}
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
        }
        self._run_script(f"window.ykiMap && window.ykiMap.pushTelemetry({json.dumps(payload)});")

    def command_started(self, code: str, parameters: dict[str, Any]) -> None:
        normalized = code.strip().upper()
        if normalized != "MISSION_UPLOAD":
            return
        self._mission_upload_pending = True
        overlays = parameters.get("overlays")
        if overlays is None:
            state = getattr(self._mission_service, "state", MissionState())
            overlays = state.overlays
        loop = bool(parameters.get("loop", False))
        self._pending_mission_signature = self._vehicle_mission_signature(
            overlays,
            loop=loop,
        )
        self._set_mission_transfer_state(
            "pending",
            "Görev planı uçuş cihazına gönderiliyor…",
        )

    def command_completed(self, result: CommandResult) -> None:
        if result.request.code != "MISSION_UPLOAD":
            return
        self._mission_upload_pending = False
        state = "success" if result.accepted else "danger"
        if result.accepted:
            self._mission_loop = bool(result.request.parameters.get("loop", False))
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
        self._set_mission_transfer_state(state, result.message)

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
        self._tile_cache.configure(
            cache_path=settings.cache_path,
            enabled=settings.cache_enabled,
            limit_mb=settings.cache_limit_mb,
        )
        self._tile_cache.start()
        self._cache_entries = self._tile_cache.entries()
        self._cache_keys = {entry["key"] for entry in self._cache_entries if "key" in entry}
        if self._view:
            self._view.deleteLater()
            self._view = None
        if self._page:
            self._page.deleteLater()
            self._page = None
        self._profile = None
        self._interceptor = None

        while self._web_layout.count():
            item = self._web_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not WEBENGINE_AVAILABLE:
            placeholder = QLabel("PyQt6-WebEngine bulunamadi. Harita baslatilamadi.", self)
            placeholder.setWordWrap(True)
            self._web_layout.addWidget(placeholder)
            self._view = None
            return

        self._profile = QWebEngineProfile("yki-map", self)
        self._interceptor = MapRequestInterceptor(self)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._profile.setHttpUserAgent("YKI/1.0 (PyQt6 Ground Control Station)")

        cache_type = getattr(
            QWebEngineProfile.HttpCacheType,
            "NoCache",
            QWebEngineProfile.HttpCacheType.MemoryHttpCache,
        )
        self._profile.setHttpCacheType(cache_type)
        cache_root = Path(settings.cache_path)
        self._profile.setCachePath(str(cache_root / "webengine-http"))
        self._profile.setPersistentStoragePath(str(cache_root / "web-storage"))
        cache_size_bytes = min(settings.cache_limit_mb * 1024 * 1024, 2_147_000_000)
        self._profile.setHttpCacheMaximumSize(cache_size_bytes)

        self._page = QWebEnginePage(self._profile, self)
        self._profile.setParent(self._page)
        web_settings = self._page.settings()
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)

        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.setMinimumHeight(360)
        self._view.setMinimumWidth(0)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        channel = QWebChannel(self._page)
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
        self._script_queue.clear()
        self._overlay_cache = {}
        self._contact_cache = {}
        self._selected_overlay_id = None
        self._active_tool = ""
        self._base_layer = ""
        html_path = self._base_dir / "src" / "ui" / "web" / "map.html"
        self._view.load(QUrl.fromLocalFile(str(html_path)))

    def rebuild_cache_profile(self) -> None:
        self._cache_index_path = self._cache_index_file()
        self._migrate_legacy_cache_index()
        settings = self._settings_service.current
        self._tile_cache.configure(
            cache_path=settings.cache_path,
            enabled=settings.cache_enabled,
            limit_mb=settings.cache_limit_mb,
        )
        self._cache_entries = self._tile_cache.entries()
        self._cache_keys = {entry["key"] for entry in self._cache_entries if "key" in entry}
        self._build_webview()
        state = getattr(self._mission_service, "state", MissionState())
        self._sync_mission_state(state)

    def _on_js_ready(self) -> None:
        self._js_ready = True
        self._send_ui_config()
        state = getattr(self._mission_service, "state", MissionState())
        self._sync_mission_state(state)
        while self._script_queue:
            self._run_script(self._script_queue.pop(0))

    def _send_ui_config(self) -> None:
        config = self._ui_config()
        if self._bridge:
            self._bridge.set_initial_config(config)
        self._run_script(f"window.ykiMap && window.ykiMap.setUiConfig({json.dumps(config)});")

    def _ui_config(self) -> dict[str, Any]:
        settings = self._settings_service.current
        state = getattr(self._mission_service, "state", MissionState())
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
            "missionLoop": self._mission_loop,
            "tools": {key: t(label_key, self._language) for key, label_key in TOOL_LABEL_KEYS.items()},
            "toolOrder": {
                "primary": tool_config["primary"],
                "transfer": tool_config["transfer"],
            },
            "missionTypes": tool_config["missionTypes"],
            "context": {
                "goto": t("map.context.goto", self._language),
                "takeoff": t("map.context.takeoff", self._language),
                "waypoint": t("map.context.waypoint", self._language),
                "loiter": t("map.context.loiter", self._language),
                "land": t("map.context.land", self._language),
                "center": t("map.context.center", self._language),
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
                    "hss": settings.default_radius_hss,
                },
                "centerMinZoom": settings.map_center_min_zoom,
                "vehicleTrailLimit": settings.vehicle_trail_limit,
                "contactTrailLimit": settings.contact_trail_limit,
            },
        }

    def _sync_mission_state(self, state: MissionState) -> None:
        vehicle_items = [
            overlay
            for overlay in state.overlays
            if overlay.is_vehicle_mission
            and overlay.source == "flight-controller"
        ]
        if vehicle_items and len(vehicle_items) == len(
            [overlay for overlay in state.overlays if overlay.is_vehicle_mission]
        ):
            downloaded_loop = any(
                bool(overlay.metadata.get("mission_loop", False))
                for overlay in vehicle_items
            )
            if downloaded_loop != self._mission_loop:
                self._mission_loop = downloaded_loop
                self._run_script(
                    "window.ykiMap && window.ykiMap.setMissionLoop("
                    f"{json.dumps(downloaded_loop)});"
                )
        overlays = [overlay.to_dict() for overlay in state.overlays]
        contacts = [contact.to_dict() for contact in getattr(state, "contacts", [])]
        overlay_lookup = {item["overlay_id"]: item for item in overlays}
        contact_lookup = {item["contact_id"]: item for item in contacts}

        removed_overlays = set(self._overlay_cache) - set(overlay_lookup)
        for overlay_id in removed_overlays:
            self._run_script(
                f"window.ykiMap && window.ykiMap.removeOverlayById({json.dumps(overlay_id)}, false);"
            )

        for payload in overlays:
            overlay_id = payload["overlay_id"]
            if self._overlay_cache.get(overlay_id) != payload:
                self._run_script(f"window.ykiMap && window.ykiMap.upsertOverlay({json.dumps(payload)});")

        removed_contacts = set(self._contact_cache) - set(contact_lookup)
        for contact_id in removed_contacts:
            self._run_script(
                f"window.ykiMap && window.ykiMap.removeContact({json.dumps(contact_id)});"
            )

        for payload in contacts:
            contact_id = payload["contact_id"]
            if self._contact_cache.get(contact_id) != payload:
                self._run_script(f"window.ykiMap && window.ykiMap.upsertContact({json.dumps(payload)});")

        overlay_order = [overlay["overlay_id"] for overlay in overlays]
        self._run_script(f"window.ykiMap && window.ykiMap.setOverlayOrder({json.dumps(overlay_order)});")

        if state.selected_overlay_id != self._selected_overlay_id:
            self._run_script(
                f"window.ykiMap && window.ykiMap.setSelectedOverlay({json.dumps(state.selected_overlay_id)});"
            )
            self._selected_overlay_id = state.selected_overlay_id

        if state.active_tool != self._active_tool:
            self._run_script(f"window.ykiMap && window.ykiMap.setActiveTool({json.dumps(state.active_tool)});")
            self._active_tool = state.active_tool

        if state.base_layer != self._base_layer:
            self._run_script(f"window.ykiMap && window.ykiMap.setBaseLayer({json.dumps(state.base_layer)});")
            self._base_layer = state.base_layer

        self._overlay_cache = overlay_lookup
        self._contact_cache = contact_lookup
        self._sync_mission_transfer_freshness(state.overlays)
        self._update_summary(state)

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
                self._set_mission_transfer_state("idle", "Görev planı boş")
            return

        signature = self._vehicle_mission_signature(relevant, loop=self._mission_loop)
        if self._last_uploaded_mission_signature is None and all(
            str(overlay.metadata.get("source", "")).lower() == "flight-controller"
            for overlay in relevant
        ):
            self._last_uploaded_mission_signature = signature
            self._set_mission_transfer_state(
                "success",
                "Görev planı uçuş cihazından senkronize edildi",
            )
            return
        if signature != self._last_uploaded_mission_signature:
            self._set_mission_transfer_state(
                "dirty",
                "Haritadaki görev planı değişti veya henüz uçuş cihazına gönderilmedi",
            )

    @classmethod
    def _vehicle_mission_signature(cls, overlays, *, loop: bool = False) -> str:
        payloads: list[dict[str, Any]] = []
        for overlay in overlays:
            payload = overlay.to_dict() if isinstance(overlay, MapOverlay) else dict(overlay)
            if str(payload.get("overlay_type", "")).lower() in cls._VEHICLE_MISSION_TYPES:
                payloads.append(payload)
        return json.dumps(
            {"loop": bool(loop), "overlays": payloads},
            sort_keys=True,
            separators=(",", ":"),
        )

    def _update_summary(self, state: MissionState) -> None:
        del state
        self._cache_entries = self._tile_cache.entries()
        self._run_script(
            f"window.ykiMap && window.ykiMap.setCacheCount({json.dumps(self._tile_cache.count_tiles())});"
        )

    def _on_tile_cache_changed(self, count: int) -> None:
        self._pending_cache_count = count
        if not self._cache_count_timer.isActive():
            self._cache_count_timer.start(250)

    def _flush_cache_count(self) -> None:
        self._cache_entries = self._tile_cache.entries()
        self._run_script(
            f"window.ykiMap && window.ykiMap.setCacheCount({json.dumps(self._pending_cache_count)});"
        )

    def _run_script(self, script: str) -> None:
        if not self._view:
            return
        if not self._js_ready:
            self._script_queue.append(script)
            return
        self._view.page().runJavaScript(script)

    def _on_overlay_changed(self, payload: dict[str, Any]) -> None:
        existing_payload = self._overlay_cache.get(str(payload.get("overlay_id", "")))
        if existing_payload and MapOverlay.from_dict(existing_payload).is_source_managed:
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
        if hasattr(self._mission_service, "upsert_overlay"):
            self._mission_service.upsert_overlay(overlay)

    def _remove_overlay(self, overlay_id: str) -> None:
        existing_payload = self._overlay_cache.get(overlay_id)
        if existing_payload and MapOverlay.from_dict(existing_payload).is_source_managed:
            return
        self._overlay_cache.pop(overlay_id, None)
        if self._selected_overlay_id == overlay_id:
            self._selected_overlay_id = None
        if hasattr(self._mission_service, "remove_overlay"):
            self._mission_service.remove_overlay(overlay_id)

    def _select_overlay(self, overlay_id: str | None) -> None:
        self._selected_overlay_id = overlay_id
        if hasattr(self._mission_service, "select_overlay"):
            self._mission_service.select_overlay(overlay_id)

    def _on_map_action(self, payload: dict[str, Any]) -> None:
        action = payload.get("type")
        if action == "tool_changed":
            tool = payload.get("tool", "pan")
            if tool == "clear":
                if hasattr(self._mission_service, "clear_dynamic_overlays"):
                    self._mission_service.clear_dynamic_overlays()
                return
            self._active_tool = tool
            if hasattr(self._mission_service, "set_active_tool"):
                self._mission_service.set_active_tool(tool)
        elif action == "storage_request":
            request = payload.get("action")
            if request in {"archive", "save", "load"}:
                self.storage_requested.emit(request)
        elif action == "mission_send_request":
            self._mission_loop = bool(payload.get("loop", self._mission_loop))
            state = getattr(self._mission_service, "state", MissionState())
            overlays = [overlay for overlay in state.overlays if overlay.is_vehicle_mission]
            self.command_requested.emit(
                "MISSION_UPLOAD",
                {
                    "overlays": [overlay.to_dict() for overlay in overlays],
                    "selected_overlay_id": state.selected_overlay_id,
                    "loop": self._mission_loop,
                },
            )
        elif action == "mission_loop_changed":
            self._mission_loop = bool(payload.get("loop", False))
            state = getattr(self._mission_service, "state", MissionState())
            self._sync_mission_transfer_freshness(state.overlays)
        elif action == "layer_changed":
            layer = payload.get("layer", "vector")
            self._base_layer = layer
            if hasattr(self._mission_service, "set_base_layer"):
                self._mission_service.set_base_layer(layer)
            self._settings_service.patch(map_layer=layer)
        elif action == "mission_item_created":
            return
        elif action == "goto_request":
            self.command_requested.emit("GOTO", {"latitude": payload["lat"], "longitude": payload["lon"]})
        elif action in {"land_request", "takeoff_request"}:
            # JS aynı anda MapOverlay oluşturur; nokta, açıkça görev gönderilene kadar
            # yalnızca merkezi görev planında kalır ve ani uçuş komutu üretmez.
            return
        elif action == "center_vehicle":
            return
        elif action == "tile_notice":
            message = payload.get("message", "Harita katmani gecici olarak ulasilamiyor")
            self.info_generated.emit("warning", message)

    def _load_cache_entries(self) -> list[dict[str, Any]]:
        return self._tile_cache.entries()

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

    def _save_cache_entries(self) -> None:
        self._cache_entries = self._tile_cache.entries()
        self._cache_keys = {entry["key"] for entry in self._cache_entries if "key" in entry}

    def _on_bounds_changed(self, payload: dict[str, Any]) -> None:
        del payload
        state = getattr(self._mission_service, "state", MissionState())
        self._update_summary(state)

    def _on_cache_cleared(self) -> None:
        self._cache_index_path = self._cache_index_file()
        self._cache_entries = []
        self._cache_keys = set()
        self._tile_cache.clear()
        if self._profile:
            self._profile.clearHttpCache()
            self._profile.clearAllVisitedLinks()
        state = getattr(self._mission_service, "state", MissionState())
        self._update_summary(state)
