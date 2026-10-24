"""Stable, UI-independent extension API (major version 1)."""

from .clocks import (
    ClockQuality,
    ClockReference,
    ClockReferenceRegistry,
    ClockSample,
    ClockStatus,
)
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
from .parameters import ParameterService, ParameterSyncState, ParameterSyncStatus
from .settings import validate_settings

__all__ = [
    "validate_settings",
    "API_MAJOR",
    "ClockQuality",
    "ClockReference",
    "ClockReferenceRegistry",
    "ClockSample",
    "ClockStatus",
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
    "ParameterSyncState",
    "ParameterSyncStatus",
]
