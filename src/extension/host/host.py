"""Manifest-first extension discovery and dependency-safe composition."""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.dependency import BaseDependencyContainer
from src.extension.api import API_MAJOR, ExtensionContext, ExtensionManifest, ExtensionRegistrar, SidebarContribution, VisualContribution, StatusContribution, SettingsContribution
from src.services.storage import load_json_file, save_json_file


class ExtensionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtensionState:
    manifest: ExtensionManifest
    enabled: bool
    pending_restart: bool


class ExtensionHost:
    REQUIRED_CAPABILITY = "vehicle.protocol"

    def __init__(self, base_dir: Path, container: BaseDependencyContainer) -> None:
        self.base_dir = base_dir
        self.container = container
        self._state_path = base_dir / "data" / "extensions" / "state.json"
        # A malformed persisted switch must never silently re-enable a plugin.
        self._stored = load_json_file(self._state_path)
        self._manifests = self._discover(Path(__file__).resolve().parents[3] / "extensions")
        for key, value in self._stored.items():
            if key in self._manifests and not isinstance(value, bool):
                raise ExtensionError(f"Invalid enabled state for {key}")
        self._running: list[Any] = []
        self._started_plugins: list[Any] = []
        self._owned_tokens: dict[str, tuple[Any, ...]] = {}
        self._lifecycle_started = False
        self._active_ids: set[str] = set()
        self._dirty_settings: set[str] = set()
        self._sidebar_views: list[SidebarContribution] = []
        self._visual_views: list[VisualContribution] = []
        self._status_widgets: list[StatusContribution] = []
        self._settings_actions: dict[str, SettingsContribution] = {}
        self._validate(self._enabled_ids())

    @staticmethod
    def _discover(root: Path) -> dict[str, ExtensionManifest]:
        found: dict[str, ExtensionManifest] = {}
        if not root.exists():
            raise ExtensionError(f"Extension directory missing: {root}")
        for path in sorted(root.glob("*/manifest.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                manifest = ExtensionManifest(
                    identifier=payload["identifier"], name=payload["name"],
                    description=payload.get("description", ""),
                    category=payload.get("category", "Other"),
                    version=payload["version"], entry_point=payload["entry_point"],
                    api_major=payload.get("api_major", API_MAJOR),
                    provides=tuple(payload.get("provides", ())),
                    requires=tuple(payload.get("requires", ())),
                    default_enabled=bool(payload.get("default_enabled", False)),
                    settings=dict(payload.get("settings", {})),
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ExtensionError(f"Invalid manifest: {path}: {exc}") from exc
            if not re.fullmatch(r"[a-z][a-z0-9_]*", manifest.identifier):
                raise ExtensionError(f"Invalid extension identifier: {manifest.identifier}")
            if path.parent.name != manifest.identifier or manifest.identifier in found:
                raise ExtensionError(f"Extension identifier/path conflict: {path}")
            if manifest.api_major != API_MAJOR:
                raise ExtensionError(f"Incompatible extension API: {manifest.identifier}")
            found[manifest.identifier] = manifest
        return found

    def _enabled_ids(self) -> set[str]:
        return {
            key for key, manifest in self._manifests.items()
            if bool(self._stored.get(key, manifest.default_enabled))
        }

    def _validate(self, enabled: set[str]) -> list[str]:
        providers: dict[str, str] = {}
        for key in enabled:
            for capability in self._manifests[key].provides:
                if capability in providers:
                    raise ExtensionError(f"Capability conflict: {capability}")
                providers[capability] = key
        if self.REQUIRED_CAPABILITY not in providers:
            raise ExtensionError("At least one vehicle protocol extension must remain enabled")
        ordered: list[str] = []
        visiting: set[str] = set()

        def visit(key: str) -> None:
            if key in ordered:
                return
            if key in visiting:
                raise ExtensionError(f"Extension dependency cycle: {key}")
            visiting.add(key)
            for required in self._manifests[key].requires:
                provider = providers.get(required)
                if provider is None:
                    raise ExtensionError(f"{key} requires {required}")
                visit(provider)
            visiting.remove(key)
            ordered.append(key)

        for key in sorted(enabled):
            visit(key)
        return ordered

    def list_extensions(self) -> list[ExtensionState]:
        enabled = self._enabled_ids()
        return [ExtensionState(manifest, key in enabled,
                               (key in enabled) != (key in self._active_ids) or key in self._dirty_settings)
                for key, manifest in sorted(self._manifests.items())]

    def refresh_extensions(self) -> None:
        """Discover newly installed packages without activating them implicitly."""
        found = self._discover(Path(__file__).resolve().parents[3] / "extensions")
        missing = self._active_ids - set(found)
        if missing:
            raise ExtensionError(f"Active extension package missing: {', '.join(sorted(missing))}")
        added = set(found) - set(self._manifests)
        if added:
            updated = {**self._stored, **{key: False for key in added}}
            save_json_file(self._state_path, updated)
            self._stored = updated
        self._manifests = found

    def sidebar_views(self) -> tuple[SidebarContribution, ...]:
        return tuple(self._sidebar_views)

    def visual_views(self) -> tuple[VisualContribution, ...]:
        return tuple(self._visual_views)

    def status_widgets(self) -> tuple[StatusContribution, ...]:
        return tuple(self._status_widgets)

    def settings_action(self, identifier: str) -> SettingsContribution | None:
        return self._settings_actions.get(identifier)

    def is_active(self, identifier: str) -> bool:
        """Report what was actually loaded at startup, not a pending toggle."""
        return identifier in self._active_ids

    def can_reload(self, identifier: str) -> bool:
        manifest = self._manifests[identifier]
        if self.REQUIRED_CAPABILITY in manifest.provides:
            return False
        return not any(
            key != identifier and key in self._active_ids
            and set(other.requires).intersection(manifest.provides)
            for key, other in self._manifests.items()
        )

    def set_enabled(self, identifier: str, enabled: bool) -> None:
        if identifier not in self._manifests:
            raise KeyError(identifier)
        candidate = self._enabled_ids()
        if enabled:
            candidate.add(identifier)
        else:
            candidate.discard(identifier)
        self._validate(candidate)
        if enabled == (identifier in self._active_ids) and self._stored.get(identifier) == enabled:
            return
        was_active = identifier in self._active_ids
        if enabled and not was_active:
            self._load_one(identifier)
        elif not enabled and was_active:
            self._unload_one(identifier)
        previous = self._stored.get(identifier, self._manifests[identifier].default_enabled)
        self._stored[identifier] = enabled
        try:
            save_json_file(self._state_path, self._stored)
        except Exception:
            self._stored[identifier] = previous
            if was_active != enabled:
                if was_active:
                    self._load_one(identifier)
                else:
                    self._unload_one(identifier)
            raise

    def settings(self, identifier: str) -> dict[str, Any]:
        manifest = self._manifests[identifier]
        defaults = {key: spec.get("default") for key, spec in manifest.settings.items()}
        path = self.base_dir / "data" / "extensions" / identifier / "settings.json"
        return {**defaults, **load_json_file(path)}

    def save_settings(self, identifier: str, values: dict[str, Any]) -> None:
        manifest = self._manifests[identifier]
        if set(values) - set(manifest.settings):
            raise ValueError("Unknown extension setting")
        for key, value in values.items():
            kind = manifest.settings[key].get("type")
            if kind == "boolean" and not isinstance(value, bool):
                raise ValueError(f"{key} must be boolean")
            if kind == "string" and not isinstance(value, str):
                raise ValueError(f"{key} must be string")
            if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"{key} must be integer")
        path = self.base_dir / "data" / "extensions" / identifier / "settings.json"
        save_json_file(path, {**self.settings(identifier), **values})
        self._dirty_settings.add(identifier)

    def configure(self) -> None:
        for identifier in self._validate(self._enabled_ids()):
            self._load_one(identifier)

    def _load_one(self, identifier: str) -> None:
        manifest = self._manifests[identifier]
        module_name, separator, factory_name = manifest.entry_point.partition(":")
        if not separator or not module_name.startswith(f"extensions.{identifier}."):
            raise ExtensionError(f"Invalid entry point: {manifest.entry_point}")
        plugin = getattr(importlib.import_module(module_name), factory_name)()
        registrar = ExtensionRegistrar()
        context = ExtensionContext(self.base_dir, self.settings(identifier), self.container)
        plugin.configure(registrar, context)
        for binding in registrar.bindings:
            if self.container.container.has(binding.token):
                raise ExtensionError(f"Service conflict: {binding.token}")
        registered: list[Any] = []
        try:
            for binding in registrar.bindings:
                self.container.singleton(binding.token, factory=binding.factory,
                                         dependencies=binding.dependencies, priority=10)
                registered.append(binding.token)
            if self._lifecycle_started and hasattr(plugin, "start"):
                try:
                    plugin.start(context)
                except Exception:
                    if hasattr(plugin, "stop"):
                        plugin.stop()
                    raise
        except Exception:
            for token in reversed(registered):
                self.container.unregister(token)
            raise
        self._owned_tokens[identifier] = tuple(registered)
        for view in registrar.sidebar_views:
            key = f"{identifier}.{view.identifier}"
            self._sidebar_views.append(SidebarContribution(key, view.title, view.factory))
        for view in registrar.visual_views:
            self._visual_views.append(VisualContribution(f"{identifier}.{view.identifier}", view.factory))
        for widget in registrar.status_widgets:
            self._status_widgets.append(StatusContribution(f"{identifier}.{widget.identifier}", widget.factory, widget.placement))
        if registrar.settings_action is not None:
            self._settings_actions[identifier] = registrar.settings_action
        self._running.append((identifier, plugin, context))
        self._active_ids.add(identifier)
        if self._lifecycle_started:
            self._started_plugins.append(plugin)

    def _unload_one(self, identifier: str) -> None:
        entry = next(item for item in self._running if item[0] == identifier)
        _, plugin, _ = entry
        if plugin in self._started_plugins:
            if hasattr(plugin, "stop"):
                plugin.stop()
            self._started_plugins.remove(plugin)
        for token in reversed(self._owned_tokens.pop(identifier, ())):
            self.container.unregister(token)
        prefix = identifier + "."
        self._sidebar_views = [view for view in self._sidebar_views if not view.identifier.startswith(prefix)]
        self._visual_views = [view for view in self._visual_views if not view.identifier.startswith(prefix)]
        self._status_widgets = [widget for widget in self._status_widgets if not widget.identifier.startswith(prefix)]
        self._settings_actions.pop(identifier, None)
        self._running.remove(entry)
        self._active_ids.remove(identifier)

    def reload(self, identifier: str) -> None:
        """Restart only this extension after its own settings change."""
        if identifier not in self._active_ids:
            return
        if not self.can_reload(identifier):
            raise ExtensionError(f"{identifier} has live protocol/dependents; cannot reload it in isolation")
        self._unload_one(identifier)
        try:
            self._load_one(identifier)
        except Exception:
            # A rejected replacement must not remain advertised as enabled.
            self._stored[identifier] = False
            save_json_file(self._state_path, self._stored)
            raise
        self._dirty_settings.discard(identifier)

    def start(self) -> None:
        if self._lifecycle_started:
            return
        self._lifecycle_started = True
        try:
            for _identifier, plugin, context in self._running:
                if hasattr(plugin, "start"):
                    plugin.start(context)
                self._started_plugins.append(plugin)
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        for plugin in reversed(self._started_plugins):
            if hasattr(plugin, "stop"):
                plugin.stop()
        self._started_plugins.clear()
        self._lifecycle_started = False
