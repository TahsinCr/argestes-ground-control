from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def ensure_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_json_file(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return dict(default or {})
    return json.loads(file_path.read_text(encoding="utf-8"))


def save_json_file(path: str | Path, payload: dict[str, Any]) -> None:
    file_path = Path(path)
    ensure_directory(file_path.parent)
    file_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def clear_directory_contents(path: str | Path) -> Path:
    directory = ensure_directory(path)
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=False)
        else:
            item.unlink(missing_ok=True)
    return directory
