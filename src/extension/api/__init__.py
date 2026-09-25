"""Stable, UI-independent extension API (major version 1)."""

from .contracts import API_MAJOR, ExtensionContext, ExtensionManifest, ExtensionRegistrar, ServiceBinding, TelemetrySource

__all__ = ["API_MAJOR", "ExtensionContext", "ExtensionManifest", "ExtensionRegistrar", "ServiceBinding", "TelemetrySource"]
