from __future__ import annotations

import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from src.services.logging_service import LoggingService


class LoggingServiceTests(unittest.TestCase):
    def test_bounded_history_keeps_the_newest_entries_in_order(self) -> None:
        service = LoggingService(max_entries=3)

        for index in range(5):
            service.log("TEST", f"Entry {index}")

        self.assertEqual(
            [entry.message for entry in service.history],
            ["Entry 2", "Entry 3", "Entry 4"],
        )

    def test_session_files_rotate_with_valid_json_and_bounded_backups(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "gcs_test.json"
            service = LoggingService(
                log_path=path,
                max_file_bytes=180,
                backup_count=2,
            )

            for index in range(12):
                service.log("TEST", f"Entry {index}: " + "x" * 40)

            retained = [
                path,
                path.with_name(path.name + ".1"),
                path.with_name(path.name + ".2"),
            ]
            for rotated in retained:
                self.assertTrue(rotated.exists())
                self.assertIsInstance(json.loads(rotated.read_text()), list)
            self.assertFalse(path.with_name(path.name + ".3").exists())


if __name__ == "__main__":
    unittest.main()
