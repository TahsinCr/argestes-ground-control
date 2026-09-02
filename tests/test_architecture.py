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
            for layer in ("models", "services", "ui")
            for path in (SRC / layer).rglob("*.py")
        )
        self.assertNotIn("kuzey_core", source)

    def test_project_uses_the_core_public_packages(self) -> None:
        allowed_prefixes = (
            "src.core.abstracts",
            "src.core.dependency",
            "src.core.events",
            "src.core.mavlink",
        )
        violations: list[str] = []
        legacy_references: list[str] = []
        for layer in ("models", "services", "ui"):
            for path in (SRC / layer).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "EventStream" in text or "publish_isolated" in text:
                    legacy_references.append(str(path.relative_to(ROOT)))
                for module in imported_modules(path):
                    if module.startswith("src.core.") and not module.startswith(
                        allowed_prefixes
                    ):
                        violations.append(f"{path.relative_to(ROOT)} -> {module}")
        self.assertEqual(violations, [])
        self.assertEqual(legacy_references, [])

    def test_service_groups_use_contracts_instead_of_peer_implementations(self) -> None:
        rules = {
            "vehicle": (
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
        self.assertFalse((SRC / "services" / "orchestration").exists())
        self.assertFalse((SRC / "services" / "catalogs").exists())
        self.assertFalse((SRC / "services" / "processing").exists())

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

    def test_leaflet_vendor_assets_are_unchanged(self) -> None:
        expected = {
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
