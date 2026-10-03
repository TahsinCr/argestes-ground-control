"""Extensible display catalog built on normalized snapshots, not wire messages."""

EXTRA_FIELDS = {
    "position": (
        "latitude",
        "longitude",
        "heading",
        "ground_speed",
        "air_speed",
        "local_north_m",
        "local_east_m",
        "local_down_m",
    ),
    "battery": (
        "battery_percent",
        "battery_voltage",
        "battery_current_amps",
        "battery_consumed_mah",
    ),
    "gps": (
        "gps_fix",
        "gps_satellites",
        "gps_hdop",
        "position_valid",
        "home_latitude",
        "home_longitude",
    ),
    "link": (
        "link_quality",
        "link_quality_source",
        "rc_rssi",
        "telemetry_round_trip_ms",
        "packets_in",
        "packets_out",
        "bandwidth_kbps",
        "packet_delivery_percent",
        "estimated_dropped_packets",
        "radio_local_rssi",
        "radio_remote_rssi",
        "radio_local_noise",
        "radio_remote_noise",
        "radio_tx_buffer",
        "radio_rx_errors",
        "radio_fixed_packets",
    ),
    "sensors": (
        "barometric_pressure_hpa",
        "barometric_temperature_c",
        "differential_pressure_hpa",
        "cpu_load",
        "timestamp",
        "uav_time",
    ),
}

UNITS = {
    "latitude": "°",
    "longitude": "°",
    "heading": "°",
    "ground_speed": "m/s",
    "air_speed": "m/s",
    "local_north_m": "m",
    "local_east_m": "m",
    "local_down_m": "m",
    "battery_percent": "%",
    "battery_voltage": "V",
    "battery_current_amps": "A",
    "battery_consumed_mah": "mAh",
    "telemetry_round_trip_ms": "ms",
    "bandwidth_kbps": "kbps",
    "packet_delivery_percent": "%",
    "barometric_pressure_hpa": "hPa",
    "differential_pressure_hpa": "hPa",
    "barometric_temperature_c": "°C",
    "cpu_load": "%",
    "radio_tx_buffer": "%",
}


def format_field(snapshot, key):
    import math
    from datetime import datetime

    value = getattr(snapshot, key, None)
    if value is None:
        return "--"
    if isinstance(value, bool):
        return "✓" if value else "—"
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    if isinstance(value, float):
        if not math.isfinite(value):
            return "--"
        value = (
            f"{value:.6f}"
            if "latitude" in key or "longitude" in key
            else f"{value:.2f}"
        )
    return f"{value} {UNITS.get(key, '')}".strip()
