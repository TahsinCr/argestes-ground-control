"""Satellite-count quality bands owned by the Map & Autonomy extension."""


def gps_quality_state(
    satellites: int | None, *, good_min: int, fair_min: int
) -> str:
    """Return the status-chip state for the configured GPS satellite bands."""
    if satellites is None or satellites < fair_min:
        return "danger"
    if satellites < good_min:
        return "warning"
    return "success"
