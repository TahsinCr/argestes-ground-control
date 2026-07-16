from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from src.config.i18n import t
from src.models.mission import MapOverlay, MissionState
from src.models.telemetry import TelemetrySnapshot
from src.services.base import MissionService
from src.services.settings import SettingsService
from src.services.tile_cache import TileCacheServer
from src.themes.style import ThemePalette
from src.widgets.common import PanelFrame

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


TOOL_KEYS = {
    "pan": "map.tool.pan",
    "takeoff": "map.tool.takeoff",
    "waypoint": "map.tool.waypoint",
    "loiter": "map.tool.loiter",
    "no_fly": "map.tool.no_fly",
    "safe_zone": "map.tool.safe_zone",
    "hss": "map.tool.hss",
    "land": "map.tool.land",
    "mission_list": "map.tool.mission_list",
    "archive": "map.tool.archive",
    "send": "map.tool.send",
    "clear": "map.tool.clear",
}


class MapRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def interceptRequest(self, info) -> None:  # pragma: no cover - Qt callback
        if info.requestUrl().scheme() in {"http", "https"}:
            info.setHttpHeader(
                b"User-Agent",
                b"KuzeyGCS/1.0 (PyQt6 Ground Control Station)",
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
        self._tile_cache = TileCacheServer(
            cache_path=settings_service.current.cache_path,
            enabled=settings_service.current.cache_enabled,
            limit_mb=settings_service.current.cache_limit_mb,
            parent=self,
        )
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
        payload = {
            "active": bool(snapshot.telemetry_link and snapshot.autopilot_link),
            "lat": snapshot.latitude,
            "lon": snapshot.longitude,
            "heading": snapshot.heading,
            "mode": snapshot.flight_mode,
            "alt": snapshot.altitude_agl,
            "speed": snapshot.ground_speed,
            "armed": snapshot.armed,
        }
        self._run_script(f"window.kuzeyMap && window.kuzeyMap.pushTelemetry({json.dumps(payload)});")

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

        self._profile = QWebEngineProfile("kuzey-map", self)
        self._interceptor = MapRequestInterceptor(self)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._profile.setHttpUserAgent("KuzeyGCS/1.0 (PyQt6 Ground Control Station)")

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
        html_path = self._base_dir / "src" / "web" / "map.html"
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
        self._run_script(f"window.kuzeyMap && window.kuzeyMap.setUiConfig({json.dumps(config)});")

    def _ui_config(self) -> dict[str, Any]:
        settings = self._settings_service.current
        state = getattr(self._mission_service, "state", MissionState())
        current_layer = state.base_layer or settings.map_layer
        return {
            "layers": {
                "vector": t("map.layer.vector", self._language),
                "satellite": t("map.layer.satellite", self._language),
            },
            "tools": {key: t(label_key, self._language) for key, label_key in TOOL_KEYS.items()},
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
                "radii": {
                    "takeoff": settings.default_radius_takeoff,
                    "waypoint": settings.default_radius_waypoint,
                    "loiter": settings.default_radius_loiter,
                    "land": settings.default_radius_land,
                    "hss": settings.default_radius_hss,
                },
                "centerMinZoom": settings.map_center_min_zoom,
                "vehicleTrailLimit": settings.vehicle_trail_limit,
                "contactTrailLimit": settings.contact_trail_limit,
            },
        }

    def _sync_mission_state(self, state: MissionState) -> None:
        overlays = [overlay.to_dict() for overlay in state.overlays]
        contacts = [contact.to_dict() for contact in getattr(state, "contacts", [])]
        overlay_lookup = {item["overlay_id"]: item for item in overlays}
        contact_lookup = {item["contact_id"]: item for item in contacts}

        removed_overlays = set(self._overlay_cache) - set(overlay_lookup)
        for overlay_id in removed_overlays:
            self._run_script(
                f"window.kuzeyMap && window.kuzeyMap.removeOverlayById({json.dumps(overlay_id)}, false);"
            )

        for payload in overlays:
            overlay_id = payload["overlay_id"]
            if self._overlay_cache.get(overlay_id) != payload:
                self._run_script(f"window.kuzeyMap && window.kuzeyMap.upsertOverlay({json.dumps(payload)});")

        removed_contacts = set(self._contact_cache) - set(contact_lookup)
        for contact_id in removed_contacts:
            self._run_script(
                f"window.kuzeyMap && window.kuzeyMap.removeContact({json.dumps(contact_id)});"
            )

        for payload in contacts:
            contact_id = payload["contact_id"]
            if self._contact_cache.get(contact_id) != payload:
                self._run_script(f"window.kuzeyMap && window.kuzeyMap.upsertContact({json.dumps(payload)});")

        overlay_order = [overlay["overlay_id"] for overlay in overlays]
        self._run_script(f"window.kuzeyMap && window.kuzeyMap.setOverlayOrder({json.dumps(overlay_order)});")

        if state.selected_overlay_id != self._selected_overlay_id:
            self._run_script(
                f"window.kuzeyMap && window.kuzeyMap.setSelectedOverlay({json.dumps(state.selected_overlay_id)});"
            )
            self._selected_overlay_id = state.selected_overlay_id

        if state.active_tool != self._active_tool:
            self._run_script(f"window.kuzeyMap && window.kuzeyMap.setActiveTool({json.dumps(state.active_tool)});")
            self._active_tool = state.active_tool

        if state.base_layer != self._base_layer:
            self._run_script(f"window.kuzeyMap && window.kuzeyMap.setBaseLayer({json.dumps(state.base_layer)});")
            self._base_layer = state.base_layer

        self._overlay_cache = overlay_lookup
        self._contact_cache = contact_lookup
        self._update_summary(state)

    def _update_summary(self, state: MissionState) -> None:
        del state
        self._cache_entries = self._tile_cache.entries()
        self._run_script(
            f"window.kuzeyMap && window.kuzeyMap.setCacheCount({json.dumps(self._tile_cache.count_tiles())});"
        )

    def _on_tile_cache_changed(self, count: int) -> None:
        self._pending_cache_count = count
        if not self._cache_count_timer.isActive():
            self._cache_count_timer.start(250)

    def _flush_cache_count(self) -> None:
        self._cache_entries = self._tile_cache.entries()
        self._run_script(
            f"window.kuzeyMap && window.kuzeyMap.setCacheCount({json.dumps(self._pending_cache_count)});"
        )

    def _run_script(self, script: str) -> None:
        if not self._view:
            return
        if not self._js_ready:
            self._script_queue.append(script)
            return
        self._view.page().runJavaScript(script)

    def _on_overlay_changed(self, payload: dict[str, Any]) -> None:
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
                self.info_generated.emit("warning", "Mission plan temizlendi")
                return
            self._active_tool = tool
            if hasattr(self._mission_service, "set_active_tool"):
                self._mission_service.set_active_tool(tool)
        elif action == "storage_request":
            request = payload.get("action")
            if request in {"archive", "save", "load"}:
                self.storage_requested.emit(request)
        elif action == "mission_send_request":
            state = getattr(self._mission_service, "state", MissionState())
            self.command_requested.emit(
                "MISSION_UPLOAD",
                {
                    "overlays": [overlay.to_dict() for overlay in state.overlays],
                    "selected_overlay_id": state.selected_overlay_id,
                },
            )
            self.info_generated.emit("success", "Mission plan SIHA aktarimi icin hazirlandi")
        elif action == "layer_changed":
            layer = payload.get("layer", "vector")
            self._base_layer = layer
            if hasattr(self._mission_service, "set_base_layer"):
                self._mission_service.set_base_layer(layer)
            self._settings_service.patch(map_layer=layer)
        elif action == "mission_item_created":
            self.command_requested.emit(
                "MISSION_ITEM_ADD",
                {
                    "item_type": payload.get("item_type"),
                    "latitude": payload.get("lat"),
                    "longitude": payload.get("lon"),
                    "radius_m": payload.get("radius_m", 0.0),
                },
            )
        elif action == "goto_request":
            self.command_requested.emit("GOTO", {"latitude": payload["lat"], "longitude": payload["lon"]})
        elif action == "land_request":
            self.command_requested.emit("LAND", {"latitude": payload["lat"], "longitude": payload["lon"]})
        elif action == "takeoff_request":
            self.command_requested.emit("TAKEOFF_POINT", {"latitude": payload["lat"], "longitude": payload["lon"]})
        elif action == "center_vehicle":
            self.info_generated.emit("info", "Harita arac konumuna odaklandi")
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
