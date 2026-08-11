from __future__ import annotations

try:
    from src.app import run
except ModuleNotFoundError as exc:
    if exc.name == "src.core" or str(exc.name).startswith("src.core."):
        raise SystemExit(
            "Shared core is not initialized. Run: "
            "git submodule update --init --recursive"
        ) from exc
    raise


if __name__ == "__main__":
    raise SystemExit(run())
