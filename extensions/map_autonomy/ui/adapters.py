from PyQt6.QtCore import QObject, pyqtSignal
from src.ui.adapters.services import _QtAdapter
from extensions.map_autonomy.services.planner import PlannerService
from extensions.map_autonomy.services.tile_cache import TileCacheServer
from src.models.mission import MissionState, MapOverlay, MapContact


class QtPlannerService(_QtAdapter):
    planner_changed = pyqtSignal(object)

    def __init__(self, backend: PlannerService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.changed, self.planner_changed.emit)

    @property
    def state(self) -> MissionState:
        return self.backend.state

    @property
    def api(self):
        return getattr(self.backend, "api", None)

    def start(self) -> None:
        self.backend.start()

    def stop(self) -> None:
        self.backend.stop()

    def set_default_radii(self, values: dict) -> None:
        self.backend.set_default_radii(values)

    def set_base_layer(self, layer: str) -> None:
        self.backend.set_base_layer(layer)

    def set_active_tool(self, tool: str) -> None:
        self.backend.set_active_tool(tool)

    def set_mission_loop(self, enabled: bool) -> None:
        self.backend.set_mission_loop(enabled)

    def set_overlays(self, overlays) -> None:
        self.backend.set_overlays(overlays)

    def upsert_overlay(self, overlay: MapOverlay) -> None:
        self.backend.upsert_overlay(overlay)

    def remove_overlay(self, overlay_id: str) -> None:
        self.backend.remove_overlay(overlay_id)

    def clear_dynamic_overlays(self) -> None:
        self.backend.clear_dynamic_overlays()

    def clear_all(self) -> None:
        self.backend.clear_all()

    def select_overlay(self, overlay_id: str | None) -> None:
        self.backend.select_overlay(overlay_id)

    def load_state(self, state: MissionState) -> None:
        self.backend.load_state(state)

    def request_vehicle_sync(self) -> bool:
        return self.backend.request_vehicle_sync()

    def replace_local_plan(self, overlays, *, mission_loop: bool) -> None:
        self.backend.replace_local_plan(overlays, mission_loop=mission_loop)

    def upsert_contact(self, contact: MapContact) -> None:
        self.backend.upsert_contact(contact)

    def remove_contact(self, contact_id: str) -> None:
        self.backend.remove_contact(contact_id)

    def clear_contacts(self) -> None:
        self.backend.clear_contacts()


class QtTileCacheService(_QtAdapter):
    cache_changed = pyqtSignal(int)

    def __init__(self, backend: TileCacheServer, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._subscribe(backend.cache_changed, self.cache_changed.emit)

    @property
    def base_url(self) -> str:
        return self.backend.base_url

    @property
    def index_path(self):
        return self.backend.index_path

    def configure(self, **kwargs) -> None:
        self.backend.configure(**kwargs)

    def start(self) -> None:
        self.backend.start()

    def stop(self) -> None:
        self.backend.stop()

    def clear(self) -> None:
        self.backend.clear()

    def count_tiles(self) -> int:
        return self.backend.count_tiles()

    def entries(self):
        return self.backend.entries()
