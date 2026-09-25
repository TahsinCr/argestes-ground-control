"""Compatibility imports for common and MAVLink telemetry services."""

from src.services.vehicle.telemetry_base import MutableTelemetryService, MockTelemetryService, clone_snapshot
from extensions.mavlink.telemetry import TelemetryDecoder, VehicleTelemetryService

__all__ = ["MutableTelemetryService", "MockTelemetryService", "clone_snapshot", "TelemetryDecoder", "VehicleTelemetryService"]
