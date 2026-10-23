from __future__ import annotations

import os
import stat
import threading
from copy import deepcopy
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from src.core.events import EventBus
from src.models.settings import SettingsModel
from src.services.abstracts.settings import SettingsRepository
from src.services.logging_service import ServiceLogger
from src.services.storage import ensure_directory, load_json_file, save_json_file


class SettingsService(SettingsRepository):
    """UI-independent settings service preserving backend and UI ownership."""

    def __init__(self, base_dir: Path, logger: ServiceLogger | None = None) -> None:
        self._changed = EventBus[SettingsModel]()
        self.extensions_changed = EventBus[str]()
        self._base_dir = base_dir
        self._logger = logger
        self._data_dir = ensure_directory(base_dir / "data")
        self._settings_path = self._data_dir / "settings.json"
        self._lock = threading.RLock()
        self._extension_projections = {}
        self._extension_restorers = {}
        self._legacy_backend = {}
        self._preservation_failed = False
        self._settings = self._load()
        self._migrate_extensions()

    def _migrate_extensions(self) -> None:
        if self._preservation_failed:
            return
        directory = self._data_dir / "extensions"
        states = (
            load_json_file(directory / "state.json")
            if (directory / "state.json").exists()
            else {}
        )
        configs = {
            path.parent.name: load_json_file(path)
            for path in directory.glob("*/settings.json")
        }
        backend = self._legacy_backend
        if "video_sources" in backend:
            configs.setdefault(
                "camera",
                {
                    key: backend[key]
                    for key in (
                        "video_sources",
                        "video_shares",
                        "active_video_source_id",
                    )
                    if key in backend
                },
            )
        if "joystick" in backend:
            configs.setdefault("joystick", backend["joystick"])
        if "competition_servers" in backend:
            configs.setdefault("teknofest", {"servers": backend["competition_servers"]})
        configs.setdefault(
            "map",
            {
                key: getattr(self._settings.ui, key)
                for key in (
                    "map_layer",
                    "map_center_min_zoom",
                    "vehicle_trail_limit",
                    "contact_trail_limit",
                )
            },
        )

        def migrate(settings):
            for identifier in set(states) | set(configs):
                entry = settings.extensions.setdefault(identifier, {})
                if identifier in states:
                    entry.setdefault("enabled", states[identifier])
                if identifier in configs:
                    entry.setdefault("config", configs[identifier])
            for old, new in (
                ("map", "map_autonomy"),
                ("teknofest", "siha_teknofest_server"),
            ):
                if old in settings.extensions:
                    source = settings.extensions.pop(old)
                    target = settings.extensions.setdefault(new, {})
                    for key, value in source.items():
                        if key == "config" and isinstance(value, dict):
                            destination = target.setdefault("config", {})
                            for setting, setting_value in value.items():
                                destination.setdefault(setting, setting_value)
                        else:
                            target.setdefault(key, value)
            return settings

        self.update(migrate, notify=False)
        if not directory.exists():
            return
        # Remove only successfully imported configuration; never unrelated data.
        (directory / "state.json").unlink(missing_ok=True)
        for path in directory.glob("*/settings.json"):
            path.unlink()
        for child in directory.iterdir():
            if child.is_dir() and not any(child.iterdir()):
                child.rmdir()
        if not any(directory.iterdir()):
            directory.rmdir()

    def extension_config(self, identifier: str) -> dict:
        with self._lock:
            return deepcopy(
                self._settings.extensions.get(identifier, {}).get("config", {})
            )

    def bind_extension_settings(self, identifier, project, restore) -> None:
        """Bridge legacy typed fields without teaching the host a protocol schema."""
        with self._lock:
            snapshot = self.current
            config = self.extension_config(identifier)
            if config:
                snapshot = restore(snapshot, config)
            self._extension_projections[identifier] = project
            self._extension_restorers[identifier] = restore
            self._commit_locked(snapshot)

    def update_extension(self, identifier: str, **changes) -> None:
        def mutate(settings):
            entry = settings.extensions.setdefault(identifier, {})
            if "config" in changes:
                entry.setdefault("config", {}).update(deepcopy(changes["config"]))
            if "enabled" in changes:
                entry["enabled"] = changes["enabled"]
            restore = self._extension_restorers.get(identifier)
            if restore is not None and "config" in changes:
                settings = restore(settings, entry["config"])
            return settings

        self.update(mutate, notify=False)
        self.extensions_changed.publish(identifier)

    @property
    def changed(self) -> EventBus[SettingsModel]:
        return self._changed

    @property
    def current(self) -> SettingsModel:
        with self._lock:
            return SettingsModel.from_dict(self._settings.to_dict())

    def _load(self) -> SettingsModel:
        if self._settings_path.exists():
            self._secure_settings_permissions()
            try:
                payload = load_json_file(self._settings_path)
                self._legacy_backend = dict(payload.get("backend", {}))
                settings = SettingsModel.from_dict(payload)
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
                preserved_path = self._preserve_unreadable_settings()
                if preserved_path is None:
                    self._preservation_failed = True
                    if self._logger:
                        self._logger.event(
                            "settings.preserve_failed",
                            "Bozuk ayar dosyası korunamadı; varsayılanlar yalnız bellekte kullanılacak",
                            severity="danger",
                            details={"path": str(self._settings_path)},
                        )
                    return SettingsModel.default(self._base_dir)
                return self._restore_defaults(
                    reason=f"unreadable; preserved as {preserved_path.name}"
                )
        return self._restore_defaults(reason="missing")

    def _secure_settings_permissions(self) -> None:
        """Restrict the local settings file, which may contain credentials, to its owner."""

        if os.name != "posix":
            return
        try:
            metadata = self._settings_path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                return
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                os.chmod(self._settings_path, 0o600, follow_symlinks=False)
        except OSError as exc:
            if self._logger:
                self._logger.event(
                    "settings.permissions_failed",
                    f"Ayar dosyası izinleri güvenli hale getirilemedi: {exc}",
                    severity="warning",
                    details={"path": str(self._settings_path)},
                )

    def _preserve_unreadable_settings(self) -> Path | None:
        """Move an unreadable settings file aside before creating defaults."""

        if not self._settings_path.exists():
            return None
        backup_path = self._settings_path.with_name(
            f"{self._settings_path.stem}.corrupt-{uuid4().hex}{self._settings_path.suffix}"
        )
        try:
            self._settings_path.replace(backup_path)
        except OSError:
            return None
        return backup_path

    def _restore_defaults(self, *, reason: str) -> SettingsModel:
        settings = SettingsModel.default(self._base_dir)
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

    def save(self, settings: SettingsModel, *, notify: bool = True) -> None:
        snapshot = SettingsModel.from_dict(settings.to_dict())
        with self._lock:
            if hasattr(self, "_settings"):
                snapshot.extensions = deepcopy(self._settings.extensions)
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
            committed = self._commit_locked(snapshot)
            result = SettingsModel.from_dict(self._settings.to_dict())
        self._notify_commit(committed, notify=notify)
        return result

    def _commit_locked(
        self,
        snapshot: SettingsModel,
    ) -> tuple[SettingsModel | None, SettingsModel] | None:
        for identifier, project in self._extension_projections.items():
            snapshot.extensions.setdefault(identifier, {}).setdefault(
                "config", {}
            ).update(project(snapshot))
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
