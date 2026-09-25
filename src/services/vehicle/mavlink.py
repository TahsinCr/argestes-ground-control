"""Compatibility import for the MAVLink extension adapter."""

from extensions.mavlink.vehicle import MavlinkVehicleService, NullVehicleService

__all__ = ["MavlinkVehicleService", "NullVehicleService"]
