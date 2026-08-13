from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from src.core.events import EventBus
from src.models.settings import SettingsModel
from src.services.abstracts.settings import SettingsRepository
from src.services.logging_service import ServiceLogger
from src.services.storage import clear_directory_contents, ensure_directory, load_json_file, save_json_file


class SettingsService(SettingsRepository):
    """UI-independent settings service preserving backend and UI ownership."""

    def __init__(self, base_dir: Path, logger: ServiceLogger | None = None) -> None:
        self._changed = EventBus[SettingsModel]()
        self.cache_cleared = EventBus[None]()
        self._base_dir = base_dir
        self._logger = logger
        self._data_dir = ensure_directory(base_dir / "data")
        self._settings_path = self._data_dir / "settings.json"
        self._lock = threading.RLock()
        self._settings = self._load()

    @property
    def changed(self) -> EventBus[SettingsModel]:
        return self._changed

    @property
    def current(self) -> SettingsModel:
        with self._lock:
            return SettingsModel.from_dict(self._settings.to_dict())

    def _load(self) -> SettingsModel:
        if self._settings_path.exists():
            try:
                payload = load_json_file(self._settings_path)
                settings = SettingsModel.from_dict(payload)
                self._prepare_runtime_paths(settings)
                return settings
            except (OSError, TypeError, ValueError) as exc:
                if self._logger:
                    self._logger.event(
                        "settings.load_failed",
                        f"Ayar dosyası okunamadı; güvenli varsayılanlar kullanılacak: {exc}",
                        severity="warning",
                        details={
                            "path": str(self._settings_path),
                            "error_type": exc.__class__.__name__,
                        },
                    )
                return self._restore_defaults(reason="unreadable")
        return self._restore_defaults(reason="missing")

    def _restore_defaults(self, *, reason: str) -> SettingsModel:
        settings = SettingsModel.default(self._base_dir)
        self._prepare_runtime_paths(settings)
        try:
            self.save(settings, notify=False)
        except OSError as exc:
            if self._logger:
                self._logger.event(
                    "settings.defaults_persist_failed",
                    f"Varsayılan ayarlar dosyaya yazılamadı; bellekte kullanılacak: {exc}",
                    severity="danger",
                    details={
                        "path": str(self._settings_path),
                        "error_type": exc.__class__.__name__,
                    },
                )
        else:
            if self._logger:
                self._logger.event(
                    "settings.defaults_created",
                    "Varsayılan ayar dosyası oluşturuldu",
                    severity="success",
                    details={
                        "path": str(self._settings_path),
                        "reason": reason,
                    },
                )
        return settings

    def _prepare_runtime_paths(self, settings: SettingsModel) -> None:
        if not settings.backend.cache.path.strip():
            settings.backend.cache.path = str(
                self._base_dir / "data" / "map-cache"
            )
        ensure_directory(settings.backend.cache.path)

    def save(self, settings: SettingsModel, *, notify: bool = True) -> None:
        snapshot = SettingsModel.from_dict(settings.to_dict())
        self._prepare_runtime_paths(snapshot)
        with self._lock:
            committed = self._commit_locked(snapshot)
        self._notify_commit(committed, notify=notify)

    def set_value(self, path: str, value: object) -> None:
        self.update(lambda settings: settings.with_value(path, value))

    def patch(self, **changes: object) -> None:
        self.update(lambda settings: settings.patch(**changes))

    def update(
        self,
        mutator: Callable[[SettingsModel], SettingsModel],
        *,
        notify: bool = True,
    ) -> SettingsModel:
        """Apply a read-modify-write operation as one settings transaction."""

        with self._lock:
            current = SettingsModel.from_dict(self._settings.to_dict())
            updated = mutator(current)
            if not isinstance(updated, SettingsModel):
                raise TypeError("Ayar güncelleme işlevi SettingsModel döndürmeli")
            snapshot = SettingsModel.from_dict(updated.to_dict())
            self._prepare_runtime_paths(snapshot)
            committed = self._commit_locked(snapshot)
            result = SettingsModel.from_dict(self._settings.to_dict())
        self._notify_commit(committed, notify=notify)
        return result

    def _commit_locked(
        self,
        snapshot: SettingsModel,
    ) -> tuple[SettingsModel | None, SettingsModel] | None:
        previous = (
            SettingsModel.from_dict(self._settings.to_dict())
            if hasattr(self, "_settings")
            else None
        )
        if previous is not None and previous.to_dict() == snapshot.to_dict():
            return None
        save_json_file(self._settings_path, snapshot.to_dict())
        self._settings = snapshot
        return previous, SettingsModel.from_dict(snapshot.to_dict())

    def _notify_commit(
        self,
        committed: tuple[SettingsModel | None, SettingsModel] | None,
        *,
        notify: bool,
    ) -> None:
        if committed is None or not notify:
            return
        previous, published = committed
        self._changed.publish(published)
        changed_paths = self._changed_paths(
            previous.to_dict() if previous is not None else {},
            published.to_dict(),
        )
        if changed_paths and self._logger:
            self._logger.event(
                "settings.saved",
                f"{len(changed_paths)} ayar kalıcı olarak güncellendi",
                severity="success",
                details={"changed_paths": changed_paths},
            )

    def clear_cache(self) -> None:
        cache_path = Path(self.current.backend.cache.path)
        removed = sum(1 for _ in cache_path.rglob("*")) if cache_path.exists() else 0
        clear_directory_contents(cache_path)
        self.cache_cleared.publish(None)
        if removed and self._logger:
            self._logger.event(
                "map_cache.cleared",
                f"Harita önbelleğinden {removed} öğe silindi",
                severity="success",
                details={"removed_entries": removed},
            )

    @classmethod
    def _changed_paths(
        cls,
        previous: dict[str, object],
        current: dict[str, object],
        prefix: str = "",
    ) -> list[str]:
        paths: list[str] = []
        for key in sorted(set(previous) | set(current)):
            path = f"{prefix}.{key}" if prefix else key
            before = previous.get(key)
            after = current.get(key)
            if isinstance(before, dict) and isinstance(after, dict):
                paths.extend(cls._changed_paths(before, after, path))
            elif before != after:
                paths.append(path)
        return paths
