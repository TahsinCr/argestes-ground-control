from __future__ import annotations

import unittest
from pathlib import Path

from src.models.logs import LogEntry
from src.ui.i18n import (
    localized_log_message,
    normalize_language,
    t,
)


ROOT = Path(__file__).resolve().parents[1]


class I18nTests(unittest.TestCase):
    def test_languages_do_not_fall_back_to_each_other(self) -> None:
        self.assertEqual(t("panel.telemetry", "tr"), "Araç Telemetrisi")
        self.assertEqual(t("panel.telemetry", "en"), "Vehicle Telemetry")
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
            "AUTO araç moduna geçiş heartbeat ile doğrulandı",
        )
        self.assertEqual(
            localized_log_message(mode_entry, "en"),
            "Vehicle mode change to AUTO confirmed by heartbeat",
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
            "4 görev öğeli döngü planı araç denetleyicisi tarafından kabul edildi",
        )
        self.assertEqual(
            localized_log_message(mission_entry, "en"),
            "Loop mission with 4 items accepted by the vehicle controller",
        )

    def test_unstructured_logs_keep_the_original_message(self) -> None:
        entry = LogEntry("PYTHON", "External library warning")
        self.assertEqual(localized_log_message(entry, "tr"), entry.message)
        self.assertEqual(localized_log_message(entry, "en"), entry.message)


if __name__ == "__main__":
    unittest.main()
