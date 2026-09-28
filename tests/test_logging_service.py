from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
