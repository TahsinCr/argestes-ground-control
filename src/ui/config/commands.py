from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NumericCommandField:
    """A numeric UI field backed by an existing command-model parameter."""

    key: str
    short_label: str
    command_code: str
    vehicle_parameter: str | None
    apply_label_key: str
    apply_tooltip_key: str
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
    NumericCommandField(
        key="target_altitude",
        short_label="ALT",
        command_code="SET_TARGET_PROFILE",
        vehicle_parameter=None,
        apply_label_key="command.apply_target_altitude",
        apply_tooltip_key="command.apply_target_altitude.tooltip",
        minimum=20.0,
        maximum=1000.0,
        default=140.0,
        suffix=" m",
    ),
    NumericCommandField(
        key="air_speed",
        short_label="SPD",
        command_code="SET_TARGET_PROFILE",
        vehicle_parameter="AIRSPEED_CRUISE",
        apply_label_key="command.apply_air_speed",
        apply_tooltip_key="command.apply_air_speed.tooltip",
        minimum=10.0,
        maximum=70.0,
        default=28.0,
        suffix=" m/s",
    ),
)

AUTONOMOUS_COMMANDS = (
    AutonomousCommand("TARGET_LOCK", "command.target_lock"),
    AutonomousCommand("KAMIKAZE", "command.kamikaze"),
    AutonomousCommand("KAMIKAZE_CHECK", "command.kamikaze_check"),
    AutonomousCommand("ACTUATOR_TEST", "command.actuator_test"),
    AutonomousCommand("HSS_ESCAPE", "command.hss_escape"),
    AutonomousCommand("HSS_PREDICT", "command.hss_predict"),
    AutonomousCommand("HSS_TRANSIT", "command.hss_transit"),
)

# Commands supplied by configuration are not added to the default list. Their
# labels live here only so the UI can present clearer names when they appear.
TASK_LABEL_KEYS = {
    **{command.code: command.label_key for command in AUTONOMOUS_COMMANDS},
    "PING_TEST": "command.ping_test",
}
