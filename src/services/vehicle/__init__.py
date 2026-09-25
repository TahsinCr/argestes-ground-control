"""Lazy compatibility exports for vehicle services."""

_EXPORTS = {
    "BatteryVoltageEstimate": ("src.services.vehicle.battery", "BatteryVoltageEstimate"),
    "VoltageBatteryEstimator": ("src.services.vehicle.battery", "VoltageBatteryEstimator"),
    "MutableParameterService": ("src.services.vehicle.parameters", "MutableParameterService"),
    "VehicleParameterService": ("src.services.vehicle.parameters", "VehicleParameterService"),
    "MutableTelemetryService": ("src.services.vehicle.telemetry_base", "MutableTelemetryService"),
    "MockTelemetryService": ("src.services.vehicle.telemetry_base", "MockTelemetryService"),
    "TelemetryDecoder": ("extensions.mavlink.telemetry", "TelemetryDecoder"),
    "VehicleTelemetryService": ("extensions.mavlink.telemetry", "VehicleTelemetryService"),
    "MavlinkParameterDefaultSource": ("extensions.mavlink.parameter_defaults", "MavlinkParameterDefaultSource"),
    "MavlinkVehicleService": ("extensions.mavlink.vehicle", "MavlinkVehicleService"),
    "NullVehicleService": ("extensions.mavlink.vehicle", "NullVehicleService"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(name)
    from importlib import import_module

    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
