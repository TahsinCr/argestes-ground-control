from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NumericCommandField:
    key: str
    short_label: str
    minimum: float
    maximum: float
    default: float
    suffix: str
    decimals: int = 1


@dataclass(frozen=True, slots=True)
class AutonomousCommand:
    code: str
    label_key: str


# The default GCS aircraft is fixed-wing ArduPlane. QLAND is exclusive to
# QuadPlane/VTOL; AUTOLAND is the direct landing mode for fixed-wing aircraft.
FLIGHT_MODES = (
    "GUIDED",
    "AUTO",
    "LOITER",
    "RTL",
    "FBWA",
    "MANUAL",
    "TAKEOFF",
    "AUTOLAND",
)

PROFILE_FIELDS = (
    NumericCommandField("target_altitude", "ALT", 20.0, 1000.0, 140.0, " m"),
    NumericCommandField("air_speed", "SPD", 10.0, 70.0, 28.0, " m/s"),
)

AUTONOMOUS_COMMANDS = (
    AutonomousCommand("KAMIKAZE", "command.kamikaze"),
    AutonomousCommand("ACTUATOR_TEST", "command.actuator_test"),
)

# Commands supplied by configuration are not added to the default list. Their
# labels live here only so the UI can present clearer names when they appear.
TASK_LABEL_KEYS = {
    **{command.code: command.label_key for command in AUTONOMOUS_COMMANDS},
    "PING_TEST": "command.ping_test",
}
