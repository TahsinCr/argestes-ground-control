"""Declarative, recursive configuration validation shared by all settings writers.

The supported schema vocabulary is intentionally small: type, properties,
items, required, enum, minimum, maximum and additionalProperties. Values are
validated without coercion or changing the extension's serialized structure.
"""
from __future__ import annotations

import math


def validate_settings(value, schema: dict, path: str = "config") -> None:
    kinds = {"object": dict, "array": list, "string": str,
             "boolean": bool, "integer": int, "number": (int, float),
             "null": type(None)}
    kind = schema.get("type")
    if kind is not None:
        if kind not in kinds:
            raise ValueError(f"{path}: unsupported schema type {kind}")
        if not isinstance(value, kinds[kind]) or (
            kind in {"integer", "number"} and isinstance(value, bool)
        ):
            raise ValueError(f"{path}: expected {kind}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path}: number must be finite")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is not an allowed choice")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for bound, compare in (("minimum", lambda a, b: a < b),
                               ("maximum", lambda a, b: a > b)):
            if bound in schema and compare(value, schema[bound]):
                raise ValueError(f"{path}: violates {bound} {schema[bound]}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in value:
                raise ValueError(f"{path}.{key}: required setting is missing")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: object keys must be strings")
            if key not in properties and schema.get("additionalProperties") is False:
                raise ValueError(f"{path}.{key}: unknown setting")
            validate_settings(item, properties.get(key, {}), f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_settings(item, schema.get("items", {}), f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError(f"{path}: value is not JSON compatible")
