"""Compatibility import for MAVLink/ArduPilot parameter defaults."""

from extensions.mavlink.parameter_defaults import MavlinkParameterDefaultSource, decode_parameter_defaults

__all__ = ["MavlinkParameterDefaultSource", "decode_parameter_defaults"]
