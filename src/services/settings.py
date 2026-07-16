from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from src.models.settings import SettingsModel
from src.services.storage import clear_directory_contents, ensure_directory, load_json_file, save_json_file


class SettingsService(QObject):
    settings_changed = pyqtSignal(object)
    cache_cleared = pyqtSignal()

    def __init__(self, base_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._base_dir = base_dir
        self._data_dir = base_dir / "data"
        self._settings_path = self._data_dir / "settings.json"
        ensure_directory(self._data_dir)
        self._settings = self._load()

    @property
    def current(self) -> SettingsModel:
        return self._settings

    def _load(self) -> SettingsModel:
        if not self._settings_path.exists():
            settings = SettingsModel.default(self._base_dir)
            self.save(settings)
            return settings
        payload = load_json_file(self._settings_path)
        settings = SettingsModel.from_dict(payload)
        ensure_directory(settings.cache_path)
        return settings

    def save(self, settings: SettingsModel) -> None:
        ensure_directory(settings.cache_path)
        save_json_file(self._settings_path, settings.to_dict())
        self._settings = settings
        self.settings_changed.emit(settings)

    def patch(self, **changes: object) -> None:
        self.save(replace(self._settings, **changes))

    def clear_cache(self) -> None:
        clear_directory_contents(self._settings.cache_path)
        self.cache_cleared.emit()
