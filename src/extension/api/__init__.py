"""Stable, UI-independent extension API (major version 1)."""

from .contracts import (
    API_MAJOR,
    ConnectionProfiles,
    ConnectionSource,
    ConnectionBinding,
    ExtensionContext,
    ExtensionManifest,
    ExtensionRegistrar,
    ServiceBinding,
    VisualContribution,
    StatusContribution,
    SettingsContribution,
    TelemetrySource,
)
from .vehicle import MissionControl, RcControl
from .telemetry import TelemetryPipeline
from .parameters import ParameterService

__all__ = [
    "API_MAJOR",
    "ConnectionProfiles",
    "ConnectionSource",
    "ConnectionBinding",
    "ExtensionContext",
    "ExtensionManifest",
    "ExtensionRegistrar",
    "ServiceBinding",
    "VisualContribution",
    "StatusContribution",
    "SettingsContribution",
    "TelemetrySource",
    "MissionControl",
    "RcControl",
    "TelemetryPipeline",
    "ParameterService",
]
