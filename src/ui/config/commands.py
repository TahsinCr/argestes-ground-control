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
    NumericCommandField("loiter_radius", "LTR", 20.0, 800.0, 75.0, " m"),
)

AUTONOMOUS_COMMANDS = (
    AutonomousCommand("TARGET_LOCK", "command.target_lock"),
    AutonomousCommand("KAMIKAZE", "command.kamikaze"),
    AutonomousCommand("WAYPOINT_TRACK", "command.waypoint_track"),
    AutonomousCommand("AUTO_TAKEOFF", "command.auto_takeoff"),
    AutonomousCommand("AUTO_LAND", "command.auto_land"),
    AutonomousCommand("SURVEY_AREA", "command.survey"),
)

# Commands supplied by configuration are not added to the default list. Their
# labels live here only so the UI can present clearer names when they appear.
TASK_LABEL_KEYS = {
    **{command.code: command.label_key for command in AUTONOMOUS_COMMANDS},
    "PING_TEST": "command.ping_test",
}
