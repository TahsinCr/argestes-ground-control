"""Validated language-package access for the GCS interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_PACKAGE_DIR = Path(__file__).resolve().parent


def _load_language_pack(language: str) -> dict[str, str]:
    path = _PACKAGE_DIR / f"{language}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"Dil paketi bulunamadı: {path.name}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Dil paketi geçerli JSON değil: {path.name}:{error.lineno}:{error.colno}"
        ) from error

    if not isinstance(payload, dict):
        raise RuntimeError(f"Dil paketi JSON nesnesi olmalı: {path.name}")
    invalid_keys = [
        key
        for key, value in payload.items()
        if not isinstance(key, str) or not isinstance(value, str)
    ]
    if invalid_keys:
        raise RuntimeError(
            f"Dil paketinde metin olmayan değerler var: {path.name}: {invalid_keys}"
        )
    return payload


TURKISH = _load_language_pack("tr")
ENGLISH = _load_language_pack("en")
LANGUAGES = {"tr": TURKISH, "en": ENGLISH}
SUPPORTED_LANGUAGES = tuple(LANGUAGES)

_turkish_keys = set(TURKISH)
_english_keys = set(ENGLISH)
if _turkish_keys != _english_keys:
    missing_tr = sorted(_english_keys - _turkish_keys)
    missing_en = sorted(_turkish_keys - _english_keys)
    raise RuntimeError(
        "Dil paketleri eşleşmiyor: "
        f"Türkçe eksik={missing_tr}, İngilizce eksik={missing_en}"
    )


def normalize_language(language: str) -> str:
    normalized = str(language or "").strip().lower().replace("_", "-")
    base = normalized.split("-", 1)[0]
    return base if base in LANGUAGES else "tr"


def t(key: str, language: str, **values: object) -> str:
    """Return interface text from exactly one language package.

    Missing keys do not fall back to another language, making mixed-language
    defects immediately visible during development.
    """

    selected = LANGUAGES[normalize_language(language)]
    text = selected.get(key, f"[{key}]")
    return text.format(**values) if values else text


def localized_log_message(entry: Any, language: str) -> str:
    """Translate a structured log entry into the selected display language.

    The original message in the JSONL archive remains unchanged. Existing rows
    can therefore be translated again after a language change without reducing
    their value as technical evidence.
    """

    action = str(getattr(entry, "action", "") or "").strip().lower()
    if not action or action == "service.event":
        return str(getattr(entry, "message", ""))

    key = f"log.event.{action}"
    values: dict[str, object] = {
        "source": str(getattr(entry, "source", "")),
        "message": str(getattr(entry, "message", "")),
        "device": str(getattr(entry, "device_id", "") or "-"),
    }
    details = getattr(entry, "details", {})
    if isinstance(details, dict):
        values.update(details)

    selected = LANGUAGES[normalize_language(language)]
    template_keys: list[str] = []
    if action.startswith("command."):
        code = str(values.get("code", "")).strip().lower().replace("-", "_")
        if code:
            command_key = f"{key}.{code}"
            if code == "mission_upload":
                repeat_mode = (
                    "loop"
                    if bool(values.get("loop", False))
                    else "once"
                )
                template_keys.append(f"{command_key}.{repeat_mode}")
            template_keys.append(command_key)
    elif action == "mission.upload_confirmed":
        repeat_mode = (
            "loop"
            if bool(values.get("loop", False))
            else "once"
        )
        template_keys.append(f"{key}.{repeat_mode}")
    template_keys.extend((key, f"log.{action}"))
    template = next(
        (
            selected[candidate]
            for candidate in template_keys
            if candidate in selected
        ),
        None,
    )
    if template is None and action.startswith("runtime."):
        if action.endswith("_start_failed"):
            template = selected.get("log.event.runtime.start_failed")
            values["service"] = action.removeprefix("runtime.").removesuffix(
                "_start_failed"
            )
        elif action.endswith("_stop_failed"):
            template = selected.get("log.event.runtime.stop_failed")
            values["service"] = action.removeprefix("runtime.").removesuffix(
                "_stop_failed"
            )
    if template is None:
        return str(getattr(entry, "message", ""))
    try:
        return template.format(**values)
    except (KeyError, TypeError, ValueError):
        return template


def connection_status_text(status: Any, language: str) -> tuple[str, str]:
    """Present vehicle connection health in the selected UI language."""

    channel = str(status.channel)
    options = dict(status.options)
    connected = bool(status.connected)
    summary_state = "connected" if connected else "disconnected"
    if channel == "telemetry" and options.get("serial_permission_required"):
        summary_key = "connection.status.telemetry.permission"
    else:
        summary_key = f"connection.status.{channel}.{summary_state}"
    summary = t(summary_key, language)

    if channel == "gps":
        age = options.get("gps_age_s")
        raw_satellites = options.get("satellites")
        values = {
            "fix": str(options.get("gps_fix", "NO FIX")),
            "satellites": int(raw_satellites) if raw_satellites is not None else "--",
        }
        detail = (
            t("connection.detail.gps.age", language, **values, age=float(age))
            if age is not None
            else t("connection.detail.gps.waiting", language, **values)
        )
    elif channel == "telemetry":
        if options.get("serial_permission_required"):
            detail = t(
                "connection.detail.telemetry.permission",
                language,
                device=str(options.get("serial_device", status.endpoint)),
            )
        else:
            quality_age = options.get("link_quality_age_s")
            if options.get("link_quality_available") and quality_age is not None:
                quality_key = (
                    "connection.detail.telemetry.rf_quality"
                    if options.get("link_quality_source") == "rf"
                    else "connection.detail.telemetry.delivery_quality"
                )
                quality = t(
                    quality_key,
                    language,
                    quality=int(options.get("link_quality", 0)),
                    age=float(quality_age),
                )
            else:
                quality = t("connection.detail.telemetry.quality_waiting", language)
            age = options.get("last_message_age_s")
            detail = (
                t("connection.detail.telemetry.age", language, age=float(age), quality=quality)
                if age is not None
                else t("connection.detail.telemetry.waiting", language, quality=quality)
            )
            round_trip_ms = options.get("round_trip_ms")
            latency = (
                t("connection.detail.telemetry.latency", language, latency=float(round_trip_ms))
                if round_trip_ms is not None
                else t("connection.detail.telemetry.latency_waiting", language)
            )
            detail = f"{detail} / {latency}"
    elif connected:
        detail = t(
            "connection.detail.autopilot.connected",
            language,
            device=options.get("device_id", "-"),
            system=options.get("system_id", 0),
            component=options.get("component_id", 0),
        )
    else:
        detail = t("connection.detail.autopilot.disconnected", language)
    return summary, detail
