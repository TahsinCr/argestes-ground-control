from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def ensure_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_json_file(
    path: str | Path, default: dict[str, Any] | None = None
) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return dict(default or {})
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        if default is None:
            raise
        return dict(default)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON kökü nesne olmalı: {file_path}")
    return payload


def save_json_file(path: str | Path, payload: dict[str, Any]) -> None:
    file_path = Path(path)
    ensure_directory(file_path.parent)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, file_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def clear_directory_contents(path: str | Path) -> Path:
    directory = ensure_directory(path)
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=False)
        else:
            item.unlink(missing_ok=True)
    return directory
