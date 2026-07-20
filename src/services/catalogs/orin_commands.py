from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def configured_orin_commands(config_path: Path) -> tuple[str, ...]:
    """Return normalized task names exposed by the standalone Orin config.

    The YKİ only reads command names. Executable paths remain owned and
    validated by ``orin_mavlink_agent.py`` on the Orin.
    """

    path = Path(config_path)
    if not path.exists():
        return ()
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Orin config JSON geçersiz: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Orin config kökü bir nesne olmalı")
    commands = payload.get("commands", {})
    if not isinstance(commands, dict):
        raise ValueError("Orin config commands bölümü bir nesne olmalı")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name, command in commands.items():
        name = str(raw_name).strip().upper()
        if not name:
            raise ValueError("Orin komut adı boş olamaz")
        if not isinstance(command, str) and not (
            isinstance(command, list)
            and command
            and all(isinstance(item, str) for item in command)
        ):
            raise ValueError(f"Orin komutu string veya string listesi olmalı: {name}")
        if name not in seen:
            normalized.append(name)
            seen.add(name)
    return tuple(normalized)
