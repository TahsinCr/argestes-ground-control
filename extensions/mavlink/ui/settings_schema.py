"""Settings fields owned by the MAVLink protocol extension."""
from src.ui.config.settings_schema import ChoiceSpec, FieldSpec, SectionSpec

SETTINGS_SECTIONS = (
    SectionSpec(
        "dialog.settings.section.mavlink",
        (
            FieldSpec("backend.mavlink.endpoint", "dialog.settings.mavlink_endpoint", "text"),
            FieldSpec("backend.mavlink.baud", "dialog.settings.mavlink_baud", "spin", minimum=1200, maximum=3000000),
            FieldSpec("backend.mavlink.heartbeat_timeout", "dialog.settings.mavlink_timeout", "spin", minimum=1, maximum=120, suffix=" s"),
            FieldSpec("backend.mavlink.operation_timeout", "dialog.settings.mavlink_operation_timeout", "spin", minimum=1, maximum=120, suffix=" s"),
            FieldSpec("backend.mavlink.dialect", "dialog.settings.mavlink_dialect", "combo", (
                ChoiceSpec("ardupilotmega", "mavlink.dialect.ardupilotmega"),
                ChoiceSpec("common", "mavlink.dialect.common"),
            )),
            FieldSpec("backend.mavlink.source_system", "dialog.settings.mavlink_source_system", "spin", minimum=0, maximum=255),
            FieldSpec("backend.mavlink.source_component", "dialog.settings.mavlink_source_component", "spin", minimum=0, maximum=255),
            FieldSpec("backend.mavlink.autoreconnect", "dialog.settings.mavlink_autoreconnect", "check"),
            FieldSpec("backend.mavlink.sync_parameters_on_connect", "dialog.settings.mavlink_sync_parameters", "check"),
            FieldSpec("backend.mavlink.sync_mission_on_connect", "dialog.settings.mavlink_sync_mission", "check"),
            FieldSpec("backend.mavlink.auto_connect", "dialog.settings.mavlink_auto_connect", "check"),
        ),
    ),
)
