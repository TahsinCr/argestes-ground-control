from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from src.models.logs import LogEntry
from src.ui.i18n import (
    ENGLISH,
    TURKISH,
    localized_log_message,
    normalize_language,
    t,
)


ROOT = Path(__file__).resolve().parents[1]


class I18nTests(unittest.TestCase):
    def test_language_sources_are_valid_json_files(self) -> None:
        package_dir = ROOT / "src" / "ui" / "i18n"
        for language in ("tr", "en"):
            path = package_dir / f"{language}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(payload, dict)
            self.assertTrue(payload)
            self.assertFalse((package_dir / f"{language}.py").exists())

    def test_language_packages_have_identical_keys(self) -> None:
        self.assertEqual(set(TURKISH), set(ENGLISH))
        self.assertGreater(len(TURKISH), 250)

    def test_literal_translation_keys_used_by_python_ui_exist(self) -> None:
        used: set[str] = set()
        for path in (ROOT / "src" / "ui").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "t":
                    continue
                if node.args and isinstance(node.args[0], ast.Constant):
                    key = node.args[0].value
                    if isinstance(key, str):
                        used.add(key)
        self.assertEqual(sorted(used - set(TURKISH)), [])

    def test_languages_do_not_fall_back_to_each_other(self) -> None:
        self.assertEqual(t("panel.telemetry", "tr"), "Uçuş Telemetrisi")
        self.assertEqual(t("panel.telemetry", "en"), "Flight Telemetry")
        self.assertEqual(t("missing.key", "en"), "[missing.key]")
        self.assertEqual(normalize_language("en-US"), "en")
        self.assertEqual(normalize_language("tr_TR"), "tr")

    def test_structured_logs_are_translated_without_mutating_raw_message(self) -> None:
        entry = LogEntry(
            "COMMAND",
            "AUTO komutu gönderildi",
            action="command.sent",
            details={"code": "AUTO"},
        )
        self.assertEqual(
            localized_log_message(entry, "tr"),
            "AUTO komutu gönderildi",
        )
        self.assertEqual(
            localized_log_message(entry, "en"),
            "AUTO command sent",
        )
        self.assertEqual(entry.message, "AUTO komutu gönderildi")

    def test_command_logs_keep_mode_and_mission_operation_details(self) -> None:
        mode_entry = LogEntry(
            "COMMAND",
            "AUTO modu heartbeat ile doğrulandı",
            action="command.confirmed",
            details={"code": "APPLY_MODE", "mode": "AUTO"},
        )
        self.assertEqual(
            localized_log_message(mode_entry, "tr"),
            "AUTO uçuş moduna geçiş heartbeat ile doğrulandı",
        )
        self.assertEqual(
            localized_log_message(mode_entry, "en"),
            "Flight mode change to AUTO confirmed by heartbeat",
        )

        mission_entry = LogEntry(
            "COMMAND",
            "4 görev öğesi döngü görevi olarak uçuş cihazı tarafından kabul edildi",
            action="command.confirmed",
            details={
                "code": "MISSION_UPLOAD",
                "item_count": 4,
                "vehicle_item_count": 4,
                "ignored_item_count": 1,
                "loop": True,
            },
        )
        self.assertEqual(
            localized_log_message(mission_entry, "tr"),
            "4 görev öğeli döngü planı uçuş cihazı tarafından kabul edildi",
        )
        self.assertEqual(
            localized_log_message(mission_entry, "en"),
            "Loop mission with 4 items accepted by the flight controller",
        )

    def test_unstructured_logs_keep_the_original_message(self) -> None:
        entry = LogEntry("PYTHON", "External library warning")
        self.assertEqual(localized_log_message(entry, "tr"), entry.message)
        self.assertEqual(localized_log_message(entry, "en"), entry.message)


if __name__ == "__main__":
    unittest.main()
