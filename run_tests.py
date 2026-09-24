#!/usr/bin/env python3
"""Run the YKI and Vehicle Autonomy Core test suites."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
CORE_TEST_RUNNER = PROJECT_ROOT / "src" / "core" / "tests" / "run.py"
CORE_ROOT = PROJECT_ROOT / "src" / "core"


def main() -> int:
    """Run both suites and return a shell-friendly status code."""

    if sys.version_info < (3, 11):
        print(
            f"Error: Python 3.11 or newer is required (found: {sys.version.split()[0]}).",
            file=sys.stderr,
        )
        return 2

    if not CORE_TEST_RUNNER.is_file():
        print(
            "Error: src/core is missing. Run "
            "'git submodule update --init --recursive' first.",
            file=sys.stderr,
        )
        return 2
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTests(loader.discover(start_dir=str(PROJECT_ROOT / "tests")))
    suite.addTests(
        loader.discover(
            start_dir=str(CORE_ROOT / "tests"),
            pattern="test*.py",
            top_level_dir=str(CORE_ROOT),
        )
    )

    print(
        "==> Running "
        f"{suite.countTestCases()} YKI and Core tests as one suite",
        flush=True,
    )
    try:
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    except KeyboardInterrupt:
        print("Test run interrupted.", file=sys.stderr)
        return 130
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
