"""Manifest-first extension discovery and dependency-safe composition."""

from __future__ import annotations

import importlib
import inspect
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.dependency import BaseDependencyContainer
from src.extension.api import (
    API_MAJOR,
    ClockSample,
    ClockReferenceRegistry,
    ExtensionContext,
    ExtensionManifest,
    ExtensionRegistrar,
    SettingsContribution,
    StatusContribution,
    VisualContribution,
)
from src.services.abstracts.logging import LogService
from src.services.logging_service import LoggingService
from src.services.settings import SettingsService


class ExtensionServices:
    """Delegate service access while binding shared logs to extension identity."""

    def __init__(self, services, identifier):
        self._services, self._identifier = services, identifier

    def __getattr__(self, name):
        return getattr(self._services, name)

    def resolve(self, token):
        service = self._services.resolve(token)
        return (
            service.for_extension(self._identifier)
            if token in (LoggingService, LogService)
            else service
        )


class ExtensionError(RuntimeError):
    pass


def _system_clock_sample(*, local: bool) -> ClockSample:
    value = datetime.now().astimezone() if local else datetime.now(UTC)
    return ClockSample(value=value, updated_at=value)


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
        self.clock_references = ClockReferenceRegistry()
        self.clock_references.register(
            "system",
            "local",
            "System local time",
            lambda: _system_clock_sample(local=True),
            stale_after_seconds=1.0,
        )
        self.clock_references.register(
            "system",
            "utc",
            "System UTC",
            lambda: _system_clock_sample(local=False),
            stale_after_seconds=1.0,
        )
        self._settings_service = container.resolve(SettingsService)
        # A malformed persisted switch must never silently re-enable a plugin.
        self._stored = {
            key: entry["enabled"]
            for key, entry in self._settings_service.current.extensions.items()
            if "enabled" in entry
        }
        if "telemetry" in self._stored:
            if not isinstance(self._stored["telemetry"], bool):
                raise ExtensionError("Invalid enabled state for telemetry")
            migrated = dict(self._stored)
            if "vehicle_connection" not in migrated:
                migrated["vehicle_connection"] = migrated["telemetry"]
            del migrated["telemetry"]
            self._save_states(migrated, remove="telemetry")
            self._stored = migrated
        self._manifests = self._discover(
            Path(__file__).resolve().parents[3] / "extensions"
        )
        for key, value in self._stored.items():
            if key in self._manifests and not isinstance(value, bool):
                raise ExtensionError(f"Invalid enabled state for {key}")
        persisted_ids = set(self._settings_service.current.extensions)
        missing_ids = persisted_ids - set(self._manifests)
        for key in missing_ids:
            # A removed package must not remain marked as active. Its config is
            # intentionally retained by _save_states for a possible reinstall.
            self._stored[key] = False
        enabled = self._enabled_ids()
        dependency_safe = self._without_unavailable_dependencies(
            enabled, self._manifests
        )
        disabled_dependents = enabled - dependency_safe
        for key in disabled_dependents:
            self._stored[key] = False
        self._running: list[Any] = []
        self._started_plugins: list[Any] = []
        self._owned_tokens: dict[str, tuple[Any, ...]] = {}
        self._lifecycle_started = False
        self._active_ids: set[str] = set()
        self._dirty_settings: set[str] = set()
        self._reload_baselines: dict[str, dict[str, Any]] = {}
        self._visual_views: list[VisualContribution] = []
        self._status_widgets: list[StatusContribution] = []
        self._settings_contributions: dict[str, SettingsContribution] = {}
        states = {
            key: self._stored.get(key, manifest.default_enabled)
            for key, manifest in self._manifests.items()
        }
        states.update({key: False for key in missing_ids})
        try:
            self._validate(dependency_safe)
        except ExtensionError:
            if missing_ids or disabled_dependents:
                self._save_states(states)
            raise
        self._save_states(states)

    def _save_states(self, states, *, remove=None):
        def mutate(settings):
            for key, enabled in states.items():
                entry = settings.extensions.setdefault(key, {})
                entry["enabled"] = enabled
                entry.setdefault("config", {})
            if remove is not None:
                old = settings.extensions.pop(remove, {})
                if old.get("config"):
                    settings.extensions.setdefault("vehicle_connection", {}).setdefault(
                        "config", old["config"]
                    )
            return settings

        self._settings_service.update(mutate, notify=False)

    @staticmethod
    def _discover(root: Path) -> dict[str, ExtensionManifest]:
        found: dict[str, ExtensionManifest] = {}
        if not root.exists():
            raise ExtensionError(f"Extension directory missing: {root}")
        for path in sorted(root.glob("*/manifest.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                manifest = ExtensionManifest(
                    identifier=payload["identifier"],
                    name=payload["name"],
                    description=payload.get("description", ""),
                    category=payload.get("category", "Other"),
                    version=payload["version"],
                    entry_point=payload["entry_point"],
                    api_major=payload.get("api_major", API_MAJOR),
                    provides=tuple(payload.get("provides", ())),
                    requires=tuple(payload.get("requires", ())),
                    default_enabled=bool(payload.get("default_enabled", False)),
                    settings=dict(payload.get("settings", {})),
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ExtensionError(f"Invalid manifest: {path}: {exc}") from exc
            if not re.fullmatch(r"[a-z][a-z0-9_]*", manifest.identifier):
                raise ExtensionError(
                    f"Invalid extension identifier: {manifest.identifier}"
                )
            if path.parent.name != manifest.identifier or manifest.identifier in found:
                raise ExtensionError(f"Extension identifier/path conflict: {path}")
            if manifest.api_major != API_MAJOR:
                raise ExtensionError(
                    f"Incompatible extension API: {manifest.identifier}"
                )
            found[manifest.identifier] = manifest
        return found

    def _enabled_ids(self) -> set[str]:
        return {
            key
            for key, manifest in self._manifests.items()
            if bool(self._stored.get(key, manifest.default_enabled))
        }

    @staticmethod
    def _without_unavailable_dependencies(
        enabled: set[str], manifests: dict[str, ExtensionManifest]
    ) -> set[str]:
        """Drop enabled extensions whose required capabilities are unavailable."""

        remaining = set(enabled)
        while True:
            providers = {
                capability
                for identifier in remaining
                for capability in manifests[identifier].provides
            }
            stranded = {
                identifier
                for identifier in remaining
                if any(
                    required not in providers
                    for required in manifests[identifier].requires
                )
            }
            if not stranded:
                return remaining
            remaining.difference_update(stranded)

    def _validate(
        self,
        enabled: set[str],
        *,
        manifests: dict[str, ExtensionManifest] | None = None,
    ) -> list[str]:
        manifests = manifests if manifests is not None else self._manifests
        providers: dict[str, str] = {}
        for key in enabled:
            for capability in manifests[key].provides:
                if capability in providers:
                    raise ExtensionError(f"Capability conflict: {capability}")
                providers[capability] = key
        if self.REQUIRED_CAPABILITY not in providers:
            raise ExtensionError(
                "At least one vehicle protocol extension must remain enabled"
            )
        ordered: list[str] = []
        visiting: set[str] = set()

        def visit(key: str) -> None:
            if key in ordered:
                return
            if key in visiting:
                raise ExtensionError(f"Extension dependency cycle: {key}")
            visiting.add(key)
            for required in manifests[key].requires:
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
        return [
            ExtensionState(
                manifest,
                key in enabled,
                (key in enabled) != (key in self._active_ids)
                or key in self._dirty_settings,
            )
            for key, manifest in sorted(self._manifests.items())
        ]

    def refresh_extensions(self) -> None:
        """Refresh installed packages and safely unload packages removed on disk."""
        found = self._discover(Path(__file__).resolve().parents[3] / "extensions")
        previous_manifests = self._manifests
        persisted_ids = set(self._settings_service.current.extensions)
        missing = (set(previous_manifests) | persisted_ids) - set(found)
        added = set(found) - set(previous_manifests)

        updated = dict(self._stored)
        for identifier in missing | added:
            # Keep per-extension configuration, but never leave a package that
            # is absent (or newly installed) marked as enabled.
            updated[identifier] = False

        requested = {
            identifier
            for identifier, manifest in found.items()
            if bool(updated.get(identifier, manifest.default_enabled))
        }
        dependency_safe = self._without_unavailable_dependencies(requested, found)
        for identifier in requested - dependency_safe:
            updated[identifier] = False

        # Validate before unloading anything. In particular, removing the only
        # vehicle protocol provider cannot be made safe by silently continuing.
        self._validate(dependency_safe, manifests=found)
        active_order = self._validate(set(self._active_ids))
        to_unload = self._active_ids - dependency_safe
        unload_order = [
            identifier
            for identifier in reversed(active_order)
            if identifier in to_unload
        ]

        unloaded: list[str] = []
        try:
            for identifier in unload_order:
                self._unload_one(identifier)
                unloaded.append(identifier)
            if updated != self._stored:
                self._save_states(updated)
        except Exception as exc:
            rollback_errors: list[tuple[str, Exception]] = []
            for identifier in reversed(unloaded):
                try:
                    self._load_one(identifier)
                except Exception as rollback_error:
                    rollback_errors.append((identifier, rollback_error))
            if rollback_errors:
                details = ", ".join(identifier for identifier, _ in rollback_errors)
                raise ExtensionError(
                    f"Extension refresh failed and could not restore: {details}"
                ) from exc
            raise

        self._stored = updated
        self._manifests = found
        for identifier in missing | to_unload:
            self._dirty_settings.discard(identifier)
            self._reload_baselines.pop(identifier, None)

    def visual_views(self) -> tuple[VisualContribution, ...]:
        return tuple(self._visual_views)

    def status_widgets(self) -> tuple[StatusContribution, ...]:
        return tuple(self._status_widgets)

    def settings_contribution(self, identifier: str) -> SettingsContribution | None:
        return self._settings_contributions.get(identifier)

    def is_active(self, identifier: str) -> bool:
        """Report what was actually loaded at startup, not a pending toggle."""
        return identifier in self._active_ids

    def can_reload(self, identifier: str) -> bool:
        manifest = self._manifests[identifier]
        if self.REQUIRED_CAPABILITY in manifest.provides:
            return False
        return not any(
            key != identifier
            and key in self._active_ids
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
        if (
            enabled == (identifier in self._active_ids)
            and self._stored.get(identifier) == enabled
        ):
            return
        was_active = identifier in self._active_ids
        was_dirty = identifier in self._dirty_settings
        previous_baseline = self._reload_baselines.get(identifier)
        if enabled and not was_active:
            self._load_one(identifier)
        elif not enabled and was_active:
            self._unload_one(identifier)
        previous = self._stored.get(
            identifier, self._manifests[identifier].default_enabled
        )
        self._stored[identifier] = enabled
        try:
            self._save_states(self._stored)
        except Exception:
            self._stored[identifier] = previous
            if was_dirty:
                self._dirty_settings.add(identifier)
            else:
                self._dirty_settings.discard(identifier)
            if previous_baseline is None:
                self._reload_baselines.pop(identifier, None)
            else:
                self._reload_baselines[identifier] = previous_baseline
            if was_active != enabled:
                if was_active:
                    self._load_one(identifier)
                else:
                    self._unload_one(identifier)
            raise
        self._dirty_settings.discard(identifier)
        self._reload_baselines.pop(identifier, None)

    def settings(self, identifier: str) -> dict[str, Any]:
        manifest = self._manifests[identifier]
        defaults = {key: spec.get("default") for key, spec in manifest.settings.items()}
        return {**defaults, **self._settings_service.extension_config(identifier)}

    def save_settings(self, identifier: str, values: dict[str, Any]) -> None:
        manifest = self._manifests[identifier]
        existing = self.settings(identifier)
        writable = set(manifest.settings) | set(existing)
        if set(values) - writable:
            raise ValueError("Unknown extension setting")
        for key, value in values.items():
            spec = manifest.settings.get(key)
            if spec is None:
                continue
            kind = spec.get("type")
            if kind == "boolean" and not isinstance(value, bool):
                raise ValueError(f"{key} must be boolean")
            if kind == "string" and not isinstance(value, str):
                raise ValueError(f"{key} must be string")
            if kind == "integer" and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                raise ValueError(f"{key} must be integer")
        if identifier in self._active_ids:
            self._reload_baselines.setdefault(identifier, existing)
        self._settings_service.update_extension(
            identifier, config={**existing, **values}
        )
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
        context = ExtensionContext(
            self.base_dir,
            self.settings(identifier),
            ExtensionServices(self.container, identifier),
            getattr(self, "clock_references", None),
        )
        try:
            plugin.configure(registrar, context)
        except Exception:
            clock_references = getattr(self, "clock_references", None)
            if clock_references is not None:
                clock_references.unregister_owner(identifier)
            raise
        for binding in registrar.bindings:
            if self.container.container.has(binding.token):
                clock_references = getattr(self, "clock_references", None)
                if clock_references is not None:
                    clock_references.unregister_owner(identifier)
                raise ExtensionError(f"Service conflict: {binding.token}")
        registered: list[Any] = []
        try:
            for binding in registrar.bindings:

                def scoped_factory(_binding=binding, **kwargs):
                    for name, token in _binding.dependencies.items():
                        if token in (LoggingService, LogService):
                            kwargs[name] = kwargs[name].for_extension(identifier)
                    return _binding.factory(**kwargs)

                scoped_factory.__signature__ = inspect.signature(binding.factory)
                self.container.singleton(
                    binding.token,
                    factory=scoped_factory,
                    dependencies=binding.dependencies,
                    priority=10,
                )
                registered.append(binding.token)
            if self._lifecycle_started and hasattr(plugin, "start"):
                try:
                    plugin.start(context)
                except Exception as start_error:
                    if hasattr(plugin, "stop"):
                        try:
                            plugin.stop()
                        except Exception as stop_error:
                            self._log_lifecycle_error(identifier, "stop", stop_error)
                    self._log_lifecycle_error(identifier, "start", start_error)
                    raise
        except Exception:
            for token in reversed(registered):
                self.container.unregister(token)
            clock_references = getattr(self, "clock_references", None)
            if clock_references is not None:
                clock_references.unregister_owner(identifier)
            raise
        self._owned_tokens[identifier] = tuple(registered)
        for view in registrar.visual_views:
            self._visual_views.append(
                VisualContribution(
                    f"{identifier}.{view.identifier}",
                    view.factory,
                    view.primary,
                    view.legacy_key,
                    view.region,
                    view.title,
                    view.initial_visible,
                    view.order,
                    view.preferred_height,
                )
            )
        for widget in registrar.status_widgets:
            self._status_widgets.append(
                StatusContribution(
                    f"{identifier}.{widget.identifier}",
                    widget.factory,
                    widget.placement,
                    widget.order,
                )
            )
        if registrar.settings_contribution is not None:
            self._settings_contributions[identifier] = registrar.settings_contribution
        self._running.append((identifier, plugin, context))
        self._active_ids.add(identifier)
        if self._lifecycle_started:
            self._started_plugins.append(plugin)

    def _unload_one(self, identifier: str) -> None:
        entry = next(item for item in self._running if item[0] == identifier)
        _, plugin, _ = entry
        if plugin in self._started_plugins:
            if hasattr(plugin, "stop"):
                try:
                    plugin.stop()
                except Exception as exc:
                    self._log_lifecycle_error(identifier, "stop", exc)
                    raise ExtensionError(
                        f"{identifier} could not be stopped; its services remain registered"
                    ) from exc
            self._started_plugins.remove(plugin)
        clock_references = getattr(self, "clock_references", None)
        if clock_references is not None:
            clock_references.unregister_owner(identifier)
        for token in reversed(self._owned_tokens.pop(identifier, ())):
            self.container.unregister(token)
        prefix = identifier + "."
        self._visual_views = [
            view
            for view in self._visual_views
            if not view.identifier.startswith(prefix)
        ]
        self._status_widgets = [
            widget
            for widget in self._status_widgets
            if not widget.identifier.startswith(prefix)
        ]
        self._settings_contributions.pop(identifier, None)
        self._running.remove(entry)
        self._active_ids.remove(identifier)

    def reload(self, identifier: str) -> None:
        """Restart only this extension after its own settings change."""
        if identifier not in self._active_ids:
            return
        if not self.can_reload(identifier):
            raise ExtensionError(
                f"{identifier} has live protocol/dependents; cannot reload it in isolation"
            )
        baseline = self._reload_baselines.get(identifier, self.settings(identifier))
        self._unload_one(identifier)
        try:
            self._load_one(identifier)
        except Exception as exc:
            self._log_lifecycle_error(identifier, "reload", exc)
            try:
                self._settings_service.update_extension(identifier, config=baseline)
                self._load_one(identifier)
            except Exception as rollback_error:
                self._log_lifecycle_error(identifier, "reload_rollback", rollback_error)
                raise ExtensionError(
                    f"{identifier} reload failed and its previous configuration could not be restored"
                ) from exc
            self._dirty_settings.discard(identifier)
            self._reload_baselines.pop(identifier, None)
            # Keep the original failure visible to the caller even though the
            # prior settings and a working extension instance are restored.
            raise
        self._dirty_settings.discard(identifier)
        self._reload_baselines.pop(identifier, None)

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
        remaining = []
        for plugin in reversed(tuple(self._started_plugins)):
            if not hasattr(plugin, "stop"):
                continue
            identifier = next(
                (
                    plugin_id
                    for plugin_id, running, _ in self._running
                    if running is plugin
                ),
                "unknown",
            )
            try:
                plugin.stop()
            except Exception as exc:
                self._log_lifecycle_error(identifier, "stop", exc)
                remaining.append(plugin)
        self._started_plugins = list(reversed(remaining))
        self._lifecycle_started = bool(self._started_plugins)

    def _log_lifecycle_error(
        self, identifier: str, operation: str, error: Exception
    ) -> None:
        try:
            self.container.resolve(LoggingService).for_extension(identifier).event(
                f"extension.{operation}_failed",
                f"Extension {identifier} {operation} işlemi başarısız: {error}",
                severity="danger",
                details={"error_type": error.__class__.__name__},
            )
        except Exception:
            # Lifecycle cleanup must remain best-effort even if logging itself
            # is one of the services that failed.
            pass
