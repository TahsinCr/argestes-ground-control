from __future__ import annotations

import ast
import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class ArchitectureTests(unittest.TestCase):
    def test_layer_dependencies_point_inward(self) -> None:
        rules = {
            "core": ("src.models", "src.services", "src.ui", "PyQt6"),
            "models": ("src.services", "src.ui", "PyQt6"),
            "services": ("src.ui", "PyQt6"),
        }
        violations: list[str] = []
        for layer, forbidden_prefixes in rules.items():
            for path in (SRC / layer).rglob("*.py"):
                for module in imported_modules(path):
                    if module.startswith(forbidden_prefixes):
                        violations.append(f"{path.relative_to(ROOT)} -> {module}")
        self.assertEqual(violations, [])

    def test_plain_top_level_layer_names_exist(self) -> None:
        for layer in ("core", "models", "services", "ui"):
            self.assertTrue((SRC / layer).is_dir(), layer)
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in SRC.rglob("*.py")
        )
        self.assertNotIn("kuzey_core", source)

    def test_service_groups_use_contracts_instead_of_peer_implementations(self) -> None:
        rules = {
            "processing": (
                "src.services.vehicle",
                "src.services.orchestration",
                "src.services.runtime",
                "src.services.facade",
            ),
            "orchestration": (
                "src.services.vehicle",
                "src.services.processing",
                "src.services.runtime",
                "src.services.facade",
            ),
            "vehicle": (
                "src.services.processing",
                "src.services.orchestration",
                "src.services.runtime",
                "src.services.facade",
            ),
        }
        violations: list[str] = []
        for group, forbidden_prefixes in rules.items():
            for path in (SRC / "services" / group).rglob("*.py"):
                for module in imported_modules(path):
                    if module.startswith(forbidden_prefixes):
                        violations.append(f"{path.relative_to(ROOT)} -> {module}")
        self.assertEqual(violations, [])

    def test_only_vehicle_adapter_and_composition_root_know_core_mavlink(self) -> None:
        violations: list[str] = []
        for path in (SRC / "services").rglob("*.py"):
            relative = path.relative_to(SRC / "services")
            allowed = relative == Path("runtime.py") or relative.parts[0] == "vehicle"
            if not allowed and any(
                module.startswith("src.core.mavlink") for module in imported_modules(path)
            ):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])
        self.assertFalse(any((SRC / "services" / "io").rglob("*.py")))

    def test_stylesheet_and_leaflet_vendor_are_unchanged(self) -> None:
        expected = {
            SRC / "ui/themes/stylesheet.py": "760118c58811e3bcfbe8ec513aafed25b2447b595702328dc385cddc6fde86a0",
            SRC / "ui/web/vendor/leaflet.js": "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
            SRC / "ui/web/vendor/leaflet.css": "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
        }
        mismatches = {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path, digest in expected.items()
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest
        }
        self.assertEqual(mismatches, {})


if __name__ == "__main__":
    unittest.main()
