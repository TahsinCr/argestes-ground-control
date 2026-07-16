from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.models.mission import MapOverlay, MissionState
from src.services.storage import ensure_directory, load_json_file, save_json_file


class MissionArchiveService:
    def __init__(self, base_dir: Path) -> None:
        self._directory = ensure_directory(base_dir / "data" / "missions")

    @property
    def directory(self) -> Path:
        return self._directory

    def list_plans(self) -> list[Path]:
        return sorted(
            self._directory.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _resolve_path(self, source: str | Path) -> Path:
        path = Path(source)
        if not path.is_absolute():
            path = self._directory / path
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        return path

    def save_plan(self, target: str | Path, state: MissionState) -> Path:
        path = self._resolve_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": datetime.utcnow().isoformat(),
            "base_layer": state.base_layer,
            "active_tool": state.active_tool,
            "selected_overlay_id": state.selected_overlay_id,
            "overlays": [overlay.to_dict() for overlay in state.overlays],
            "contacts": [],
        }
        save_json_file(path, payload)
        return path

    def load_plan(self, source: str | Path) -> MissionState:
        payload = load_json_file(self._resolve_path(source))
        overlays = [MapOverlay(**item) for item in payload.get("overlays", [])]
        return MissionState(
            overlays=overlays,
            contacts=[],
            active_tool=payload.get("active_tool", "pan"),
            base_layer=payload.get("base_layer", "vector"),
            selected_overlay_id=payload.get("selected_overlay_id"),
        )

    def delete_plan(self, source: str | Path) -> None:
        self._resolve_path(source).unlink(missing_ok=True)
